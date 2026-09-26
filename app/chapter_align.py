"""章节时刻对齐核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

每条章节 cue（非空文本）在**同一说话人的连续词**文本中查找全部 Unicode 字符
命中：把该说话人连续区间内所有词的文本原样拼接（不插空格），在其中枚举 cue 的
所有出现位置（允许重叠，例如 cue="aa" 在 "aaa" 中有 2 个命中），命中可横跨多个
词（允许跨词）。命中候选时刻取**命中首字符所在词**的 start_ms。

为每章选一个命中，要求相邻章节的候选时刻严格递增且至少相隔 min_gap_ms
（min_gap_ms 为正整数，间隔约束本身即蕴含严格递增），目标是最小化

    sum_chapter |候选时刻 - approx_ms|

同分时取**命中起始全局字符位序列字典序最小**者。全局字符位指该字符在“全部词
文本原样拼接”中的 0-based code point 偏移；不同命中起始字符位互不相同，故字典序
裁决无歧义。

算法（后向 DP + 贪心重建）：

* 每章候选按全局字符位升序枚举（同一词内随偏移升序、词间随词序升序），而词序
  时间非倒序，故候选时刻随之非降，即候选天然按 (time, global_pos) 有序；
* bwd[j][p]：第 j 章取其第 p 个候选时，后缀 [j, C) 可达的最小绝对偏差之和，
  末章直接取该候选自身偏差；其余章转移到第 j+1 章中第一个时刻满足间隔约束的
  候选 q，取 suf[j+1][q] 的最小值。每行从右向左扫一遍即得后缀最小，并用
  bisect 定位 q，整体 O(H·log H)，H 为候选总数；
* 重建：最优值为 suf[0][0]（首章无前驱约束）。逐章从左向右枚举候选，第一个
  满足时间间隔且“本章偏差 + 后缀最小”恰等于剩余预算者即为全局字符位最小的
  可行选择，归纳可得命中起始全局字符位序列字典序最小。
"""

from bisect import bisect_left

from .errors import DomainError
from .segmentation import validate_words

INF = float("inf")


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def _find_all(text, cue):
    """枚举 cue 在 text 中的全部出现起始下标（允许重叠）。"""
    positions = []
    start = 0
    while True:
        pos = text.find(cue, start)
        if pos < 0:
            return positions
        positions.append(pos)
        start = pos + 1  # 允许重叠命中


def align_chapters(words, chapters, min_gap_ms):
    """为每章选择一个 cue 命中时刻，返回 (aligned, total_deviation_ms)。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker
    （沿用同一套词序与时间校验）；chapters 按章节顺序排列，每项需具备属性
    title / cue / approx_ms。

    aligned 按章排序，每项为 dict：
    index / title / time_ms / word_index / char_start / char_end。
    word_index 为命中首字符所在词序号（0-based）；char_start/char_end 为该词内
    Unicode 字符偏移区间 [char_start, char_end)——命中未跨词时覆盖整条 cue，
    跨词时截断到命中首字符所在词的词末（char_end = 该词字符数）。

    某章无任何命中，或无法为所有章节组成完整可行方案时抛出
    DomainError(code="infeasible_chapter_alignment")（HTTP 422）。
    """
    validate_words(words)
    n = len(words)

    # ---- 全局字符位前缀：global_offsets[i] 为词 i 首字符在全量拼接文本中的偏移 ----
    char_counts = [len(w.text) for w in words]  # Unicode code points
    global_offsets = [0] * n
    for i in range(1, n):
        global_offsets[i] = global_offsets[i - 1] + char_counts[i - 1]

    # ---- 同一说话人连续区间（run_start, run_text, run_offsets）----
    # run_offsets[k]：区间内第 k 个词首字符在 run_text 中的偏移。
    run_info = []
    run_start = 0
    for i in range(1, n + 1):
        if i < n and words[i].speaker == words[i - 1].speaker:
            continue
        run_end = i
        run_text = "".join(words[k].text for k in range(run_start, run_end))
        run_offsets = [0]
        for k in range(run_start, run_end - 1):
            run_offsets.append(run_offsets[-1] + char_counts[k])
        run_info.append((run_start, run_text, run_offsets))
        run_start = run_end

    # ---- 每章候选：(候选时刻, 命中首字符所在词, 词内起始偏移, 全局字符位) ----
    # 枚举按全局字符位升序（词序 × 词内偏移），时刻随之非降。
    chapter_candidates = []
    chapter_times = []
    for ch_idx, chapter in enumerate(chapters):
        cue = chapter.cue
        cands = []
        for run_start, run_text, run_offsets in run_info:
            ptr = 0
            for local_pos in _find_all(run_text, cue):
                while ptr + 1 < len(run_offsets) and run_offsets[ptr + 1] <= local_pos:
                    ptr += 1
                word_index = run_start + ptr
                char_start = local_pos - run_offsets[ptr]
                global_pos = global_offsets[word_index] + char_start
                cands.append(
                    (words[word_index].start_ms, word_index, char_start, global_pos)
                )
        if not cands:
            _fail(
                "infeasible_chapter_alignment",
                f"第 {ch_idx} 章（标题：{chapter.title}）的 cue 在同一说话人连续词中"
                "没有任何命中",
                chapter_index=ch_idx,
            )
        chapter_candidates.append(cands)
        chapter_times.append([c[0] for c in cands])

    chapter_count = len(chapter_candidates)

    # ---- 后向 DP ----
    # 每行只需后缀最小表：suffix_min[j][p] = 第 j 章从第 p 个候选（含）起为后缀
    # [j, C) 能取得的最小绝对偏差之和（INF 表示不可行）。按章倒序构建后转回正序。
    suffix_rev = []
    for j in range(chapter_count - 1, -1, -1):
        cands = chapter_candidates[j]
        times = chapter_times[j]
        approx = chapters[j].approx_ms
        row = [INF] * len(cands)
        if j == chapter_count - 1:
            for p, time in enumerate(times):
                row[p] = abs(time - approx)
        else:
            nxt_times = chapter_times[j + 1]
            nxt_suf = suffix_rev[-1]  # 栈顶即第 j+1 章的后缀最小表
            for p, time in enumerate(times):
                q = bisect_left(nxt_times, time + min_gap_ms)
                tail = nxt_suf[q] if q < len(nxt_suf) else INF
                if tail != INF:
                    row[p] = abs(time - approx) + tail
        # 就地转为后缀最小
        best = INF
        for p in range(len(row) - 1, -1, -1):
            if row[p] < best:
                best = row[p]
            row[p] = best
        suffix_rev.append(row)
    suffix_min = suffix_rev[::-1]

    optimum = suffix_min[0][0] if chapter_count else 0
    if optimum == INF:
        _fail(
            "infeasible_chapter_alignment",
            "不存在满足时刻严格递增且相邻至少相隔 min_gap_ms 的完整章节对齐方案",
            chapter_count=chapter_count,
            min_gap_ms=min_gap_ms,
        )

    # ---- 贪心重建：逐章取全局字符位最小（候选枚举顺序最早）的可行命中 ----
    aligned = []
    prev_time = None
    budget = optimum
    for j, cands in enumerate(chapter_candidates):
        times = chapter_times[j]
        lower = (
            bisect_left(times, prev_time + min_gap_ms)
            if prev_time is not None
            else 0
        )
        chosen_p = -1
        tail_suf = suffix_min[j + 1] if j + 1 < chapter_count else None
        nxt_times = chapter_times[j + 1] if j + 1 < chapter_count else None
        for p in range(lower, len(cands)):
            if tail_suf is None:
                tail = 0
            else:
                q = bisect_left(nxt_times, times[p] + min_gap_ms)
                tail = tail_suf[q] if q < len(tail_suf) else INF
            deviation = abs(times[p] - chapters[j].approx_ms)
            if tail != INF and deviation + tail == budget:
                chosen_p = p
                break
        # optimum 有限即存在完整方案，chosen_p 必已赋值。
        time, word_index, char_start, _ = cands[chosen_p]
        char_end = min(char_start + len(chapters[j].cue), char_counts[word_index])
        aligned.append(
            {
                "index": j,
                "title": chapters[j].title,
                "time_ms": time,
                "word_index": word_index,
                "char_start": char_start,
                "char_end": char_end,
            }
        )
        budget -= abs(time - chapters[j].approx_ms)
        prev_time = time

    return aligned, int(optimum)
