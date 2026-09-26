"""播客转写字幕分段核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

算法（两趟 DP + 贪心重建），整体 O(n) 时间、O(n) 空间：

* 每段最多 18 个 Unicode 字符、每词至少 1 个字符 => 每段至多 18 个词，
  枚举一个段首词的所有可行段末词只需常数步；
* 前向 DP：fwd[t] = 覆盖前 t 个词的最小总分；
* 后向 DP：bwd[i] = 覆盖后缀 [i, n) 的最小总分；
* 最优总分 = fwd[n]。之后从左向右贪心重建断点序列：在当前边界 i，
  若直接以 [i, n) 作末段能达到最优总分，则结束（其断点序列是任何继续
  分段方案的严格前缀，字典序最小）；否则取最小的、使
  “本段分 + bwd[j]”恰好补足最优总分的断点 j。每步只在至多 18 个候选中
  选择，天然得到字典序最小的断点序列。
"""

from .errors import DomainError

# 分段硬约束
MAX_CHARS = 18                 # 每段最多 18 个 Unicode 字符（按 code point 计）
MIN_DURATION_MS = 1_000        # 段时长下限
MAX_DURATION_MS = 6_000        # 段时长上限
MAX_WORDS_PER_SEGMENT = 18     # 每词至少 1 个字符 => 段内词数上限

# 计分规则
SHORT_DURATION_MS = 2_000      # 不足 2 秒的非末段记 1 分
NO_SENTENCE_ENDING_PENALTY = 2  # 非末段末词不以句末标点结尾记 2 分
SENTENCE_ENDINGS = frozenset("。！？")  # 认可的句末标点

INF = float("inf")


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def validate_words(words):
    """校验输入序列：非空、词内时间合法、相邻词不倒序、不重叠（端点相等允许）。"""
    if not words:
        _fail("empty_words", "words 不能为空")

    prev = None
    for idx, w in enumerate(words):
        if w.end_ms < w.start_ms:
            _fail(
                "invalid_time_range",
                f"第 {idx} 个词的 end_ms({w.end_ms}) 早于 start_ms({w.start_ms})",
                word_index=idx,
            )
        if prev is not None:
            if w.start_ms < prev.start_ms:
                _fail(
                    "time_out_of_order",
                    f"第 {idx} 个词的 start_ms({w.start_ms}) 早于前一个词的 "
                    f"start_ms({prev.start_ms})，输入必须按时间排列",
                    word_index=idx,
                )
            if w.start_ms < prev.end_ms:
                _fail(
                    "overlapping_time_range",
                    f"第 {idx} 个词的 start_ms({w.start_ms}) 早于前一个词的 "
                    f"end_ms({prev.end_ms})，相邻词时间不允许重叠",
                    word_index=idx,
                )
        prev = w


def optimal_segmentation(words):
    """在所有可行分段中求总分最小者；同分取断点序列字典序最小者。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker。

    返回 (segments, total_score)；无可行分段时抛出
    DomainError(code="infeasible_segmentation")，并携带最早无法到达的
    前缀末词序号 last_word_index（0-based）。
    """
    validate_words(words)
    n = len(words)

    char_counts = [len(w.text) for w in words]  # Unicode code points

    # run_end[i]：从 i 开始的同一说话人连续区间的右开边界
    run_end = [0] * n
    boundary = n
    for i in range(n - 1, -1, -1):
        if i == n - 1 or words[i].speaker != words[i + 1].speaker:
            boundary = i + 1
        run_end[i] = boundary

    # 预计算每个段首 i 的可行段：(段末词边界 j, 时长, 非末段扣分)。
    # 每个 i 至多 18 个候选；末段扣分恒为 0，使用时按 j==n 判断。
    segments_by_start = [None] * n
    for i in range(n):
        opts = []
        chars = 0
        limit = min(run_end[i], i + MAX_WORDS_PER_SEGMENT)
        start = words[i].start_ms
        for j in range(i + 1, limit + 1):
            chars += char_counts[j - 1]
            if chars > MAX_CHARS:
                break  # 再加词只会更长
            duration = words[j - 1].end_ms - start
            if duration < MIN_DURATION_MS:
                continue  # 后续可能随 j 增长而达标
            if duration > MAX_DURATION_MS:
                break  # 再加词只会更长
            if j == n:
                cost = 0
            else:
                cost = (1 if duration < SHORT_DURATION_MS else 0)
                if words[j - 1].text[-1] not in SENTENCE_ENDINGS:
                    cost += NO_SENTENCE_ENDING_PENALTY
            opts.append((j, duration, cost))
        segments_by_start[i] = opts

    # ---- 前向 DP：fwd[t] = 覆盖前 t 个词的最小总分 ----
    fwd = [INF] * (n + 1)
    fwd[0] = 0
    for i in range(n):
        base = fwd[i]
        if base == INF:
            continue
        for j, _, cost in segments_by_start[i]:
            candidate = base + cost
            if candidate < fwd[j]:
                fwd[j] = candidate

    optimum = fwd[n]
    if optimum == INF:
        # 最早无法到达的前缀：第一个 fwd[t] 不可达的边界 t（1<=t<=n）
        earliest_boundary = next(t for t in range(1, n + 1) if fwd[t] == INF)
        _fail(
            "infeasible_segmentation",
            "不存在满足全部约束的分段方案",
            last_word_index=earliest_boundary - 1,
        )

    # ---- 后向 DP：bwd[i] = 覆盖后缀 [i, n) 的最小总分 ----
    bwd = [INF] * (n + 1)
    bwd[n] = 0
    for i in range(n - 1, -1, -1):
        best = INF
        for j, _, cost in segments_by_start[i]:
            tail = bwd[j]
            if tail != INF:
                value = cost + tail
                if value < best:
                    best = value
        bwd[i] = best

    # ---- 贪心重建字典序最小的最优断点序列 ----
    cuts = []
    i = 0
    accumulated = 0
    while i < n:
        chosen_j = -1
        chosen_cost = 0
        for j, _, cost in segments_by_start[i]:
            if bwd[j] == INF:
                continue
            if accumulated + cost + bwd[j] != optimum:
                continue
            if j == n:
                # 直接结束：断点序列是任何“继续切分”方案的严格前缀，
                # 标准字典序下最小，优先选择。
                chosen_j = n
                break
            if chosen_j < 0:
                chosen_j = j
                chosen_cost = cost
        # fwd/bwd 已保证存在最优补全，chosen_j 必已赋值。
        if chosen_j == n:
            break
        cuts.append(chosen_j)
        accumulated += chosen_cost
        i = chosen_j

    boundaries = [0, *cuts, n]
    segments = []
    for seg_index, (a, b) in enumerate(zip(boundaries, boundaries[1:])):
        chunk = words[a:b]
        segments.append(
            {
                "index": seg_index,
                "start_word": a,
                "end_word": b - 1,  # 词序号均为 0-based、含端点
                "start_ms": words[a].start_ms,
                "end_ms": words[b - 1].end_ms,
                "duration_ms": words[b - 1].end_ms - words[a].start_ms,
                "speaker": words[a].speaker,
                "text": "".join(w.text for w in chunk),
                "char_count": sum(char_counts[a:b]),
            }
        )
    return segments, int(optimum)
