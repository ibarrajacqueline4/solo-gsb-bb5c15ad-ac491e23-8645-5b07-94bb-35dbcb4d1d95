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
* 转移：跳过候选 i（继承状态 (i+1, mask) 的前沿），或选上候选 i（状态
  (nxt[i], mask \\ {speaker_i}) 的前沿逐项加上候选 i 的评分与时长），
  合并后剪除被支配点；边界：i = m 时仅 mask 为空有前沿 {(0, 0)}；
* 答案：状态 (0, 全集) 前沿中时长不超预算的点，先取评分最大、再取时长
  最短（前沿按评分升序、时长严格递减，取最后一个时长不超预算的点）；
* 重建：当前状态目标 (S, D) 恒为该状态前沿中的帕累托最优点（若被支配，
  支配点与已选前缀组合后会支配上层目标，矛盾），故余项必落在下一状态的
  前沿内。从左向右枚举下一个被选候选 t，余项 (S - score_t, D - dur_t)
  命中状态 (nxt[t], mask') 前沿即 t 可行；在所有可行 t 中取原候选序号
  最小者，归纳可得字典序最小的序号序列。目标降为 (0, 0) 且必选说话人
  已覆盖时停止：空后缀是任何“再选零分零时长候选”方案的严格前缀，
  字典序最小。
"""

from bisect import bisect_right

from .errors import DomainError
from .segmentation import validate_words


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def _prune(points):
    """把 {评分: 最短时长} 剪成帕累托前沿：按评分升序、时长严格递减。"""
    kept = []
    best_dur = None
    for score, dur in sorted(points.items(), reverse=True):
        if best_dur is None or dur < best_dur:
            kept.append((score, dur))
            best_dur = dur
    kept.reverse()
    return kept  # 评分升序；时长随之严格递减


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

    # ---- DP：dp[i][mask] = (帕累托前沿列表, {评分: 时长}) ----
    dp = [[None] * (full_mask + 1) for _ in range(m + 1)]
    for mask in range(full_mask + 1):
        dp[m][mask] = ([(0, 0)], {0: 0}) if mask == 0 else ([], {})
    for i in range(m - 1, -1, -1):
        bit = bits[i]
        tail_row = dp[nxt[i]]
        skip_row = dp[i + 1]
        for mask in range(full_mask + 1):
            pts = dict(skip_row[mask][1])  # 跳过候选 i
            for s, d in tail_row[mask & ~bit][1].items():  # 选上候选 i
                s2, d2 = s + scores[i], d + durs[i]
                if s2 not in pts or d2 < pts[s2]:
                    pts[s2] = d2
            front = _prune(pts)
            dp[i][mask] = (front, dict(front))

    # ---- 答案：前沿中时长不超预算、评分最大（同分时长最短）的点 ----
    front = dp[0][full_mask][0]
    best = None
    for s, d in front:  # 评分升序、时长递减，最后一个可行点即答案
        if d <= budget_ms:
            best = (s, d)
    if best is None:
        _fail(
            "infeasible_trailer_plan",
            "不存在满足全部约束的预告片片段组合",
            candidate_count=m,
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
            tail = dp[nxt[t]][mask & ~bits[t]][1]
            if tail.get(s_rem) == d_rem and (pick < 0 or origs[t] < origs[pick]):
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
