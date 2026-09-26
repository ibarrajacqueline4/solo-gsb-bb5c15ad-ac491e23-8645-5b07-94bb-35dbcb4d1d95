"""多人同时发声（叠话）统计报告核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

与转写后处理各接口不同，本接口面向多说话人交叠场景：词序列**允许乱序与
交叠**，仅逐词校验 end_ms >= start_ms；零时长词（end_ms == start_ms）
不参与任何统计。每个词视为左闭右开区间 [start_ms, end_ms)，端点相接
（一词 end_ms 等于另一词 start_ms）不计重叠。

统计内容：

* 各说话人发声覆盖：同一说话人全部词区间的并集总毫秒数（重叠词只计一次）；
* 叠话区间：至少两人同时发声的时间段，按活跃说话人集合的变化切分，
  仅合并相邻且集合相同的片段；每段列出起止时刻与按标识排序的说话人；
* 说话人对重叠：每段叠话时长计入其活跃集合的全部两人组合——三人同时
  发声的时长分别计入三对。

算法（事件扫描线），O(n log n + E·k + M·k²) 时间、O(n + M + k²) 空间
（n 为词数 ≤ 2000，E 为不同边界时刻数 ≤ 2n，M 为输出段数 ≤ E，
k 为活跃说话人数 ≤ 20）：

* 把每个非零时长词拆为“开始/结束”两类事件，按时刻归组；
* 按时刻升序扫描，同一时刻先处理全部结束事件、再处理全部开始事件，
  即得左闭右开语义（端点相接不计重叠）；
* 相邻事件时刻之间活跃说话人集合不变：用每说话人计数器维护活跃集合，
  同一说话人的重叠词自然只计一次；
* 每个基本区间的时长累加到集合内每个说话人的覆盖；集合大小 ≥ 2 的
  基本区间若与上一输出段端点相接且集合相同则合并，否则另起新段；
* 说话人对重叠按输出段展开：每段时长计入其活跃集合的全部两人组合——
  三人同时发声的时长分别计入三对。
"""

from collections import Counter
from itertools import combinations

from .errors import DomainError

MAX_SPEAKERS = 20  # 不同说话人上限


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def build_crosstalk_report(words):
    """统计多人同时发声情况，返回 (speakers, segments, pair_overlaps)。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker。
    词序列允许乱序与交叠；仅逐词校验 end_ms >= start_ms（否则抛出
    DomainError(code="invalid_time_range")，word_index 为输入序），
    不同说话人超过 20 名时抛出 DomainError(code="too_many_speakers")
    （均为 HTTP 422）。零时长词不参与任何统计。

    speakers：按标识排序的 [{"speaker", "covered_ms"}]，covered_ms 为该
    说话人词区间并集的总毫秒数（同一说话人重叠词只计一次，仅含零时长词
    的说话人为 0）；
    segments：按时间升序的叠话区间 [{"index", "start_ms", "end_ms",
    "duration_ms", "speakers"}]，speakers 按标识排序、至少两人；
    pair_overlaps：按 (speaker_a, speaker_b) 排序的 [{"speaker_a",
    "speaker_b", "overlap_ms"}]，仅含 overlap_ms > 0 的说话人对。
    """
    for idx, w in enumerate(words):
        if w.end_ms < w.start_ms:
            _fail(
                "invalid_time_range",
                f"第 {idx} 个词的 end_ms({w.end_ms}) 早于 start_ms({w.start_ms})",
                word_index=idx,
            )

    speakers = sorted({w.speaker for w in words})
    if len(speakers) > MAX_SPEAKERS:
        _fail(
            "too_many_speakers",
            f"不同说话人数 {len(speakers)} 超过上限 {MAX_SPEAKERS}",
            speaker_count=len(speakers),
        )

    # ---- 事件归组：零时长词不参与统计 ----
    starts = {}  # 时刻 -> Counter(说话人)
    ends = {}
    times = set()
    for w in words:
        if w.end_ms == w.start_ms:
            continue
        times.add(w.start_ms)
        times.add(w.end_ms)
        starts.setdefault(w.start_ms, Counter())[w.speaker] += 1
        ends.setdefault(w.end_ms, Counter())[w.speaker] += 1

    # ---- 扫描线：相邻事件时刻之间活跃集合不变 ----
    covered = {s: 0 for s in speakers}
    segments = []
    active = Counter()
    prev_t = None
    for t in sorted(times):
        if prev_t is not None:
            duration = t - prev_t
            current = sorted(active)
            for s in current:
                covered[s] += duration
            if len(current) >= 2:
                if (
                    segments
                    and segments[-1]["end_ms"] == prev_t
                    and segments[-1]["speakers"] == current
                ):
                    # 与上一段端点相接且集合相同：合并
                    segments[-1]["end_ms"] = t
                    segments[-1]["duration_ms"] += duration
                else:
                    segments.append(
                        {
                            "index": len(segments),
                            "start_ms": prev_t,
                            "end_ms": t,
                            "duration_ms": duration,
                            "speakers": current,
                        }
                    )
        # 同一时刻先结束、后开始 => 左闭右开，端点相接不计重叠
        for s, c in ends.get(t, {}).items():
            active[s] -= c
            if active[s] == 0:
                del active[s]
        for s, c in starts.get(t, {}).items():
            active[s] += c
        prev_t = t

    # ---- 说话人对重叠：每段时长计入其活跃集合的全部两人组合 ----
    pair_overlap = Counter()  # (标识较小者, 标识较大者) -> 重叠毫秒数
    for seg in segments:
        for pair in combinations(seg["speakers"], 2):
            pair_overlap[pair] += seg["duration_ms"]

    speaker_stats = [{"speaker": s, "covered_ms": covered[s]} for s in speakers]
    pair_overlaps = [
        {"speaker_a": a, "speaker_b": b, "overlap_ms": ms}
        for (a, b), ms in sorted(pair_overlap.items())
    ]
    return speaker_stats, segments, pair_overlaps
