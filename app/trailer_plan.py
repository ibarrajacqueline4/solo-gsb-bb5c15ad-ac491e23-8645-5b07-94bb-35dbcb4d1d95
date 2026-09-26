"""预告片片段组合核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

从 1～20 个候选片段中选出词区间互不重叠、总时长不超 budget_ms 的组合，
使 1～3 个必选说话人各自至少在某个选中片段中出现一次。候选片段必须是
**单一说话人的连续词区间**，时长取末词 end_ms 减首词 start_ms。目标：
先最大化总评分，同分最小化总时长，再按“原时间顺序排列的候选序号序列”
字典序取最小者。选中片段按源时间升序返回；预告片时刻从 0 起按片段时长
依次累加。

算法（排序分层 DP + 预算剪枝 + 紧凑帕累托前沿 + 贪心重建）：

* 候选按 (start_word, end_word, 原序号) 排序；nxt[t] 为排序后第一个首词
  序号大于候选 t 末词序号的位置（在有序首词数组上二分），即选中 t 之后
  只能从 nxt[t] 起继续；
* 状态 (i, mask)：从排序后第 i 个候选（含）起、尚待覆盖的必选说话人集合
  为 mask 时可达的 (总评分, 总时长) 帕累托前沿——评分升序、时长严格递减
  （同一评分只留最短时长，被“分更高且时长不更长”支配的点剪除）。必选
  说话人至多 3 个 => mask 至多 8 种，状态至多 21×8 个；
* 预算剪枝：转移时丢弃总时长超过 budget_ms 的点（时长均非负，这些点不
  可能成为任何可行解的前缀）；
* 紧凑存储（关键，避免在“任意子集和互不相同”的输入下耗尽内存）：前沿
  用两个并行的 array('q')（评分、时长，64 位整数，每点 16 字节）按评分
  升序存放，而不是 Python 元组列表或字典（每点上百字节）。两个来源
  （跳过前沿、选 i 的平移前沿）都已按评分有序，线性归并即得新前沿。候选
  至多 20 个 => 全部状态合计至多约 8·2^20 个点 × 16 字节 ≈ 128 MiB。
  仅当评分或预算超过 64 位整数范围时才回退到 dict 前沿（点数受 m=20
  约束且为极端冷僻输入），两条路径裁决逻辑完全一致；
* 转移：跳过候选 i（前沿 A = dp[i+1][mask]），或选上候选 i（前沿 B =
  dp[nxt[i]][mask \\ {speaker_i}] 逐项加 (score_i, dur_i)，丢超预算点），
  归并 A、B：同评分取更短时长，再按评分降序扫描剔除被支配点；边界
  i = m 时仅 mask 为空有前沿 {(0, 0)}；
* 答案：状态 (0, 全集) 前沿（评分升序、时长严格递减）中从高分起第一个
  时长不超预算的点即评分最大、同分时长最短的最优解；
* 重建：当前目标 (S, D) 恒为该状态前沿中的帕累托最优点（若被支配，支配
  点与已选前缀组合后会支配上层目标，矛盾），故余项必落在下一状态前沿内。
  从左向右枚举下一个被选候选 t，余项 (S - score_t, D - dur_t) 命中状态
  (nxt[t], mask') 前沿即 t 可行；在所有可行 t 中取原候选序号最小者，归纳
  可得字典序最小的序号序列。目标降为 (0, 0) 且必选说话人已覆盖时停止：
  空后缀是任何“再选零分零时长候选”方案的严格前缀，字典序最小。
"""

from array import array
from bisect import bisect_left, bisect_right

from .errors import DomainError
from .segmentation import validate_words

# 64 位有符号整数边界（array('q') 的可表示范围）
_I64_MAX = (1 << 63) - 1


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


class _Front:
    """帕累托前沿：评分严格升序、时长严格递减的点列。

    紧凑表示优先用 array('q')（ss/dd）；任一值超出 64 位时回退到普通列表
    （py 分支，元素为 (score, dur)）。两种表示均保持评分升序。
    """

    __slots__ = ("ss", "dd", "py")

    def __init__(self, ss=None, dd=None, py=None):
        self.ss = ss
        self.dd = dd
        self.py = py

    @classmethod
    def empty(cls):
        return cls(ss=array("q"), dd=array("q"))

    @classmethod
    def zero(cls):
        ss = array("q")
        dd = array("q")
        ss.append(0)
        dd.append(0)
        return cls(ss=ss, dd=dd)

    @classmethod
    def from_py(cls, points):
        return cls(py=points)

    def is_py(self):
        return self.py is not None

    def __len__(self):
        return len(self.py) if self.py is not None else len(self.ss)

    def get_dur(self, score):
        """返回评分对应时长；不存在返回 None。"""
        if self.py is not None:
            idx = bisect_left(self.py, (score, -1))
            if idx < len(self.py) and self.py[idx][0] == score:
                return self.py[idx][1]
            return None
        ss = self.ss
        idx = bisect_left(ss, score)
        if idx < len(ss) and ss[idx] == score:
            return self.dd[idx]
        return None

    def best_feasible(self, budget_ms):
        """评分最大、同分时长最短且时长 <= budget_ms 的点 (s, d) 或 None。"""
        best = None
        if self.py is not None:
            for s, d in self.py:  # 评分升序
                if d <= budget_ms:
                    best = (s, d)
        else:
            ss, dd = self.ss, self.dd
            for idx in range(len(ss)):
                d = dd[idx]
                if d <= budget_ms:
                    best = (ss[idx], d)
        return best


def _merge_py(skip_py, tail_py, score_i, dur_i, budget_ms):
    """dict/列表回退路径的合并：返回评分升序的 (s, d) 帕累托列表。"""
    # 同评分最短时长
    points = dict(skip_py)
    for s, d in tail_py:
        d2 = d + dur_i
        if d2 <= budget_ms:
            s2 = s + score_i
            old = points.get(s2)
            if old is None or d2 < old:
                points[s2] = d2
    # 帕累托剪枝：评分降序，仅留时长严格递减者
    kept = []
    best_dur = None
    for s in sorted(points, reverse=True):
        d = points[s]
        if best_dur is None or d < best_dur:
            kept.append((s, d))
            best_dur = d
    kept.reverse()
    return kept


def _merge_compact(skip, tail, score_i, dur_i, budget_ms):
    """线性归并两个紧凑前沿并剪枝，返回新的紧凑 _Front。

    A=skip 原样；B=tail 平移 (score_i, dur_i) 且丢超预算点。两者均评分
    升序、时长严格递减；平移保持该序。先按评分降序归并、同评分取更短时长，
    再在降序点列上做帕累托剪枝（仅留时长严格小于此前所有高分点者），最后
    翻转为评分升序、时长严格递减。结果用列表收集后一次性构造 array('q')
    （批量 C 构造远快于逐元素 append）。
    """
    a_ss, a_dd = skip.ss, skip.dd
    t_ss, t_dd = tail.ss, tail.dd

    # 第一遍：评分降序归并 + 预算剪枝 + 同评分取更短时长
    mrg_ss = []
    mrg_dd = []
    append_s = mrg_ss.append
    append_d = mrg_dd.append
    ia = len(a_ss) - 1
    it = len(t_ss) - 1
    while ia >= 0 or it >= 0:
        if it < 0 or (ia >= 0 and a_ss[ia] >= t_ss[it] + score_i):
            s = a_ss[ia]
            d = a_dd[ia]
            ia -= 1
        else:
            d = t_dd[it] + dur_i
            if d > budget_ms:
                # B 时长随评分降低而严格递增：此点超时，更小评分 B 点也超时，
                # 跳过该点继续归并（不提前终止，保持与 A 的交错判定简单）。
                it -= 1
                continue
            s = t_ss[it] + score_i
            it -= 1
        if mrg_ss and mrg_ss[-1] == s:
            if d < mrg_dd[-1]:
                mrg_dd[-1] = d
        else:
            append_s(s)
            append_d(d)

    # 第二遍：在降序点列上标记帕累托最优点，再按升序收集结果
    keep = bytearray(len(mrg_ss))
    best_dur = None
    for idx in range(len(mrg_ss)):  # 评分降序：高分到低分
        d = mrg_dd[idx]
        if best_dur is None or d < best_dur:
            keep[idx] = 1
            best_dur = d
    out_ss = []
    out_dd = []
    for idx in range(len(mrg_ss) - 1, -1, -1):  # 翻转为评分升序
        if keep[idx]:
            out_ss.append(mrg_ss[idx])
            out_dd.append(mrg_dd[idx])
    return _Front(ss=array("q", out_ss), dd=array("q", out_dd))


def plan_trailer(words, candidates, budget_ms, required_speakers):
    """选出最优预告片片段组合，返回 (clips, total_score, total_duration_ms)。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker
    （沿用同一套词序与时间校验）；candidates 每项需具备属性
    start_word / end_word / score；required_speakers 为 1～3 个非空字符串
    （重复项按去重处理）。

    clips 按源时间升序，每项为 dict：index / candidate_index / speaker /
    score / start_word / end_word / source_start_ms / source_end_ms /
    duration_ms / trailer_start_ms / trailer_end_ms；trailer 时刻从 0 起
    按片段时长累加。候选词序号越界、区间倒置或横跨多个说话人时抛出相应
    DomainError；无可行组合时抛出
    DomainError(code="infeasible_trailer_plan")（均为 HTTP 422）。
    """
    validate_words(words)
    n = len(words)

    # ---- 同一说话人连续区间的右开边界（与分段模块同一推导）----
    run_end = [0] * n
    boundary = n
    for i in range(n - 1, -1, -1):
        if i == n - 1 or words[i].speaker != words[i + 1].speaker:
            boundary = i + 1
        run_end[i] = boundary

    # ---- 候选校验与预处理 ----
    required = list(dict.fromkeys(required_speakers))  # 去重，保持输入顺序
    required_bit = {speaker: 1 << k for k, speaker in enumerate(required)}

    # 每项：(start_word, end_word, score, speaker, duration_ms, 原序号, 必选位)
    cands = []
    for idx, c in enumerate(candidates):
        s, e = c.start_word, c.end_word
        if not (0 <= s < n) or not (0 <= e < n):
            _fail(
                "candidate_index_out_of_range",
                f"第 {idx} 个候选的词序号 [{s}, {e}] 超出词序列范围 [0, {n})",
                candidate_index=idx,
            )
        if s > e:
            _fail(
                "candidate_range_inverted",
                f"第 {idx} 个候选的 start_word({s}) 大于 end_word({e})",
                candidate_index=idx,
            )
        if run_end[s] <= e:
            _fail(
                "candidate_speaker_mixed",
                f"第 {idx} 个候选的词区间 [{s}, {e}] 横跨多个说话人",
                candidate_index=idx,
            )
        speaker = words[s].speaker
        cands.append(
            (
                s,
                e,
                c.score,
                speaker,
                words[e].end_ms - words[s].start_ms,
                idx,
                required_bit.get(speaker, 0),
            )
        )

    # 错误信息中的候选数为输入候选总数
    candidate_total = len(cands)

    # ---- 按 (start_word, end_word, 原序号) 排序，展开为按位置的数组 ----
    cands.sort(key=lambda c: (c[0], c[1], c[5]))
    starts = [c[0] for c in cands]
    scores = [c[2] for c in cands]
    durs = [c[4] for c in cands]
    origs = [c[5] for c in cands]
    bits = [c[6] for c in cands]
    m = len(cands)

    # nxt[t]：排序后第一个首词序号大于候选 t 末词序号的位置（词区间不重叠）
    nxt = [bisect_right(starts, c[1]) for c in cands]

    full_mask = (1 << len(required)) - 1

    # ---- 决定前沿表示：若任何可达总分或总时长可能超出 64 位有符号整数，
    #      全程走 Python 大整数（py）路径；否则用紧凑 array('q')。
    #      总分上界 = 忽略预算/互斥细节的后缀评分和之和上确界，用加权区间
    #      调度 ub[i]（互斥子集最高评分）；总时长只需与 budget_ms 比较，
    #      存入前沿的时长必 <= budget_ms，故以 budget_ms 是否越界为准。 ----
    ub = [0] * (m + 1)
    for k in range(m - 1, -1, -1):
        take = scores[k] + ub[nxt[k]]
        skip = ub[k + 1]
        ub[k] = take if take > skip else skip
    use_compact = ub[0] <= _I64_MAX and budget_ms <= _I64_MAX

    # ---- 帕累托前沿 DP（见模块文档）；按 use_compact 选择紧凑/大整数表示 ----
    dp = [[None] * (full_mask + 1) for _ in range(m + 1)]
    if use_compact:
        empty_front = _Front.empty()
        zero_front = _Front.zero()
    else:
        empty_front = _Front.from_py([])
        zero_front = _Front.from_py([(0, 0)])
    for mask in range(full_mask + 1):
        dp[m][mask] = zero_front if mask == 0 else empty_front
    for i in range(m - 1, -1, -1):
        bit = bits[i]
        score_i = scores[i]
        dur_i = durs[i]
        tail_row = dp[nxt[i]]
        skip_row = dp[i + 1]
        row = [None] * (full_mask + 1)
        for mask in range(full_mask + 1):
            if use_compact:
                row[mask] = _merge_compact(
                    skip_row[mask], tail_row[mask & ~bit],
                    score_i, dur_i, budget_ms,
                )
            else:
                row[mask] = _Front.from_py(
                    _merge_py(
                        skip_row[mask].py, tail_row[mask & ~bit].py,
                        score_i, dur_i, budget_ms,
                    )
                )
        dp[i] = row

    # ---- 答案：评分升序、时长严格递减前沿中最优的可行点 ----
    best = dp[0][full_mask].best_feasible(budget_ms)
    if best is None:
        _fail(
            "infeasible_trailer_plan",
            "不存在满足全部约束的预告片片段组合",
            candidate_count=candidate_total,
            budget_ms=budget_ms,
        )

    # ---- 贪心重建字典序最小的（时间顺序）候选序号序列 ----
    target_s, target_d = best
    chosen = []  # 排序后位置，递增
    i, mask = 0, full_mask
    while target_s != 0 or target_d != 0 or mask != 0:
        pick = -1
        for t in range(i, m):
            s_rem = target_s - scores[t]
            d_rem = target_d - durs[t]
            if s_rem < 0 or d_rem < 0:
                continue
            tail = dp[nxt[t]][mask & ~bits[t]]
            if tail.get_dur(s_rem) == d_rem and (
                pick < 0 or origs[t] < origs[pick]
            ):
                pick = t
        # 目标恒为可达的帕累托最优点，pick 必已赋值。
        chosen.append(pick)
        i = nxt[pick]
        mask &= ~bits[pick]
        target_s -= scores[pick]
        target_d -= durs[pick]

    # ---- 输出：按源时间升序，预告片时刻从 0 累加 ----
    clips = []
    trailer_cursor = 0
    for out_index, t in enumerate(chosen):
        s, e, score, speaker, dur, orig, _ = cands[t]
        clips.append(
            {
                "index": out_index,
                "candidate_index": orig,
                "speaker": speaker,
                "score": score,
                "start_word": s,
                "end_word": e,
                "source_start_ms": words[s].start_ms,
                "source_end_ms": words[e].end_ms,
                "duration_ms": dur,
                "trailer_start_ms": trailer_cursor,
                "trailer_end_ms": trailer_cursor + dur,
            }
        )
        trailer_cursor += dur
    return clips, best[0], trailer_cursor
