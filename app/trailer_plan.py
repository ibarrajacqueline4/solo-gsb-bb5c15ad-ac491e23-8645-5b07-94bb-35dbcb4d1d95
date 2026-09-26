"""预告片片段组合核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

从 1～20 个候选片段中选出词区间互不重叠、总时长不超 budget_ms 的组合，
使 1～3 个必选说话人各自至少在某个选中片段中出现一次。候选片段必须是
**单一说话人的连续词区间**，时长取末词 end_ms 减首词 start_ms。目标：
先最大化总评分，同分最小化总时长，再按“原时间顺序排列的候选序号序列”
字典序取最小者。选中片段按源时间升序返回；预告片时刻从 0 起按片段时长
依次累加。

算法（排序分层 DP + 帕累托前沿 + 贪心重建）：

* 候选按 (start_word, end_word, 原序号) 排序；nxt[t] 为排序后第一个首词
  序号大于候选 t 末词序号的位置（在有序首词数组上二分），即选中 t 之后
  只能从 nxt[t] 起继续；
* 状态 (i, mask)：从排序后第 i 个候选（含）起、尚待覆盖的必选说话人集合
  为 mask 时可达的 (总评分, 总时长) 帕累托前沿——同分只留最短时长，
  “分更高且时长更短”的点支配并剪除其余点。必选说话人至多 3 个 =>
  mask 至多 8 种，状态至多 21×8 个；
* 前沿紧凑表示：两个并行的定长有符号 64 位整数数组（array('q')），
  评分严格升序、时长严格递减，每个 (评分, 时长) 点仅占 16 字节
  （旧实现每个点是一个 Python dict 条目，约数百字节，20 个时间相接、
  评分按 2 的幂增长的候选会产生 2^20 量级个互不支配点而 MemoryError）。
  契约允许任意大的非负评分/时长，若其总和超出 int64 则自动回退为普通
  Python 整数列表，语义完全一致；
* 转移：跳过候选 i（继承状态 (i+1, mask) 的前沿），或选上候选 i（状态
  (nxt[i], mask \\ {speaker_i}) 的前沿逐项加上候选 i 的评分与时长），
  两路升序归并（同分取时长较小者），再从右向左原地剪除被支配点。
  归并时顺带丢弃时长超过预算的点：时长只会随选中片段累加，超预算点
  不可能在后续转移中变回可行，剪去它们不影响任何状态的最优答案。
  边界：i = m 时仅 mask 为空有前沿 [(0, 0)]；
* 答案：状态 (0, 全集) 前沿中最后一个点（评分最高、同分时长最短；其
  时长不超预算即可行，否则该状态无可行点）；
* 重建：当前状态目标 (S, D) 恒为该状态前沿中的帕累托最优点（若被支配，
  支配点与已选前缀组合后会支配上层目标，矛盾），故余项必落在下一状态的
  前沿内。从左向右枚举下一个被选候选 t，余项 (S - score_t, D - dur_t)
  二分命中状态 (nxt[t], mask') 前沿即 t 可行；在所有可行 t 中取原候选
  序号最小者，归纳可得字典序最小的序号序列。目标降为 (0, 0) 且必选
  说话人已覆盖时停止：空后缀是任何“再选零分零时长候选”方案的严格前缀，
  字典序最小。
"""

from array import array
from bisect import bisect_left, bisect_right

from .errors import DomainError
from .segmentation import validate_words

_INT64_MAX = (1 << 63) - 1


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


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

    # ---- 前沿容器：默认定长 int64 紧凑数组；总量可能溢出时回退普通列表 ----
    packed = sum(scores) <= _INT64_MAX and sum(durs) <= _INT64_MAX
    if packed:
        empty_front = (array("q"), array("q"))

        def new_seq():
            return array("q")

        def make_front(points):
            fs = array("q")
            fd = array("q")
            for s, d in points:
                fs.append(s)
                fd.append(d)
            return fs, fd
    else:
        empty_front = ([], [])

        def new_seq():
            return []

        def make_front(points):
            fs = []
            fd = []
            for s, d in points:
                fs.append(s)
                fd.append(d)
            return fs, fd

    def merge_fronts(skip, take, add_score, add_dur):
        """跳过候选（skip）与选上候选（take 整体平移 add_*）两路前沿归并。

        输入两路均为评分严格升序、时长严格递减；同分只留较短时长，
        丢弃时长超预算点，再从右向左原地做帕累托剪枝。take 为空时
        skip 前沿早已按同一预算剪过，可直接别名复用（前沿只读）。
        """
        skip_s, skip_d = skip
        take_s, take_d = take
        if not take_s:
            return skip
        merged_s = new_seq()
        merged_d = new_seq()
        a = b = 0
        na, nb = len(skip_s), len(take_s)
        while a < na or b < nb:
            shifted = take_s[b] + add_score if b < nb else None
            if b == nb or (a < na and skip_s[a] < shifted):
                s, d = skip_s[a], skip_d[a]
                a += 1
            elif a == na or skip_s[a] > shifted:
                s, d = shifted, take_d[b] + add_dur
                b += 1
            else:  # 同分：保留时长较短者
                s = shifted
                d0, d1 = skip_d[a], take_d[b] + add_dur
                d = d0 if d0 < d1 else d1
                a += 1
                b += 1
            if d <= budget_ms:  # 时长只增不减：超预算点此后不可能变回可行
                merged_s.append(s)
                merged_d.append(d)
        # 反向原地帕累托剪枝：保留点写到尾部连续位置，顺序天然保持升序
        w = len(merged_s) - 1
        best_dur = None
        for k in range(w, -1, -1):
            d = merged_d[k]
            if best_dur is None or d < best_dur:
                best_dur = d
                merged_s[w] = merged_s[k]
                merged_d[w] = d
                w -= 1
        del merged_s[: w + 1]
        del merged_d[: w + 1]
        return merged_s, merged_d

    # ---- DP：dp[i][mask] = (评分数组, 时长数组) 帕累托前沿 ----
    dp = [[None] * (full_mask + 1) for _ in range(m + 1)]
    dp[m][0] = make_front(((0, 0),))
    for mask in range(1, full_mask + 1):
        dp[m][mask] = empty_front
    for i in range(m - 1, -1, -1):
        bit = bits[i]
        add_score = scores[i]
        add_dur = durs[i]
        tail_row = dp[nxt[i]]
        skip_row = dp[i + 1]
        row = dp[i]
        for mask in range(full_mask + 1):
            row[mask] = merge_fronts(
                skip_row[mask], tail_row[mask & ~bit], add_score, add_dur
            )

    # ---- 答案：前沿末点评分最高且同分时长最短，其时长不超预算即可行 ----
    front_s, front_d = dp[0][full_mask]
    if not front_s or front_d[-1] > budget_ms:
        _fail(
            "infeasible_trailer_plan",
            "不存在满足全部约束的预告片片段组合",
            candidate_count=m,
            budget_ms=budget_ms,
        )
    best = (front_s[-1], front_d[-1])

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
            k = bisect_left(tail[0], s_rem)
            if (
                k < len(tail[0])
                and tail[0][k] == s_rem
                and tail[1][k] == d_rem
                and (pick < 0 or origs[t] < origs[pick])
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
