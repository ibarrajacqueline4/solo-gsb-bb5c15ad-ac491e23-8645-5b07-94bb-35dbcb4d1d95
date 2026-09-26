"""播放变速规划核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

把连续同一说话人的词合并为语速块（至多 20 块）：块内自首词 start_ms 至
末词 end_ms 整段变速，块间静音保持原时长。每块从 100% / 125% / 150% 三种
语速中选一种，块播放时长为 ceil(原时长 × 100 / 语速)；总播放时长为各块
播放时长与块间静音之和，不得超过 max_duration_ms。目标：总变速代价
Σ 原时长 × (语速 - 100)² 最小；同代价取总播放时长最长者，再取语速序列
字典序最小者（100 < 125 < 150）。

算法（折半枚举，meet-in-the-middle），O(3^(m/2) · m) 时间/空间，
m 为块数（≤ 20，m = 20 时约 12 万个组合）：

* 每块仅 3 种语速，但块时长为任意整数时 (播放时长, 代价) 的帕累托前沿
  会指数膨胀（归并剪枝几乎不生效），直接对块做 DP 不可行；折半枚举把
  3^m 降为 2 × 3^(m/2)；
* 每种组合用三进制编码记录语速选择：枚举按语速序列字典序生成，编码即
  枚举序号（首块为最高位），故编码大小顺序与语速序列字典序一致；
* 右半：枚举全部 (播放时长, 代价, 编码)，按 (时长, 编码) 排序后扫描出
  每个前缀的最优右半——代价最小、同代价时长最长、再同取编码最小；
* 左半：按字典序枚举，用二分在右半中找到时长不超剩余预算的前缀最优，
  合并为候选；全局按 (总代价, -总时长) 取最小，且严格更优才替换——
  打平时先枚举到的左半序列字典序更小，同一左半下右半又已取字典序最小，
  故最终结果即字典序最小的语速序列；
* 全部块取 150% 仍超时（或块间静音本身已超 max_duration_ms）时无可行
  方案，抛出 infeasible_playback_plan。
"""

from bisect import bisect_right

from .errors import DomainError
from .segmentation import validate_words

MAX_BLOCKS = 20           # 语速块数上限
SPEEDS = (100, 125, 150)  # 可选语速（百分比，升序）


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def _ceil_div(a, b):
    return -(-a // b)


def _enumerate_half(options):
    """枚举半个块序列的全部语速组合，返回 [(播放时长, 代价, 编码)]。

    按语速序列字典序生成；编码为选择的三进制序号（首块为最高位），
    编码大小顺序与语速序列字典序一致。
    """
    combos = [(0, 0, 0)]
    k = len(options)
    for j, opts in enumerate(options):
        factor = 3 ** (k - 1 - j)
        combos = [
            (duration + play, cost + price, code + choice * factor)
            for duration, cost, code in combos
            for choice, (play, price) in enumerate(opts)
        ]
    return combos


def plan_playback(words, max_duration_ms):
    """为每个语速块选择语速，返回 (blocks, total_cost, total_duration_ms)。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker
    （沿用同一套词序与时间校验）。

    blocks 按时间升序，每项为 dict：index / start_word / end_word / speaker /
    source_start_ms / source_end_ms / duration_ms / speed /
    playback_duration_ms / playback_start_ms / playback_end_ms；播放时刻从 0
    起按块播放时长与块间静音依次累加。块数超过 20 时抛出
    DomainError(code="too_many_blocks")；无可行方案时抛出
    DomainError(code="infeasible_playback_plan")（均为 HTTP 422）。
    """
    validate_words(words)
    n = len(words)

    # ---- 合并连续同一说话人的词为语速块 ----
    block_words = []  # (首词序号, 末词序号)，均为 0-based、含端点
    run_start = 0
    for i in range(1, n + 1):
        if i == n or words[i].speaker != words[run_start].speaker:
            block_words.append((run_start, i - 1))
            run_start = i
    m = len(block_words)
    if m > MAX_BLOCKS:
        _fail(
            "too_many_blocks",
            f"同一说话人连续块数 {m} 超过上限 {MAX_BLOCKS}",
            block_count=m,
        )

    # ---- 块原始时长、块间静音、每块 (播放时长, 代价) 选项 ----
    durations = [words[e].end_ms - words[s].start_ms for s, e in block_words]
    silence_ms = sum(
        words[block_words[i + 1][0]].start_ms - words[block_words[i][1]].end_ms
        for i in range(m - 1)
    )
    options = [
        [(_ceil_div(d * 100, speed), d * (speed - 100) ** 2) for speed in SPEEDS]
        for d in durations
    ]
    budget = max_duration_ms - silence_ms  # 各块播放时长之和的上限

    # ---- 折半枚举：右半按 (时长, 编码) 排序，扫出每个前缀的最优右半 ----
    half = m // 2
    left = _enumerate_half(options[:half])
    right = _enumerate_half(options[half:])
    right.sort(key=lambda combo: (combo[0], combo[2]))
    right_durations = [duration for duration, _, _ in right]
    best_cost = []      # 前缀最优的代价
    best_duration = []  # 前缀最优的播放时长
    best_code = []      # 前缀最优的编码
    cur = None
    for duration, cost, code in right:
        if cur is None or cost < cur[0] or (cost == cur[0] and duration > cur[1]):
            cur = (cost, duration, code)
        best_cost.append(cur[0])
        best_duration.append(cur[1])
        best_code.append(cur[2])

    # ---- 左半按字典序枚举，二分合并；严格更优才替换 ----
    answer = None  # (总代价, -总播放时长)
    answer_codes = None
    for duration_l, cost_l, code_l in left:
        i = bisect_right(right_durations, budget - duration_l) - 1
        if i < 0:
            continue
        key = (cost_l + best_cost[i], -(duration_l + best_duration[i]))
        if answer is None or key < answer:
            answer = key
            answer_codes = (code_l, best_code[i])
    if answer is None:
        _fail(
            "infeasible_playback_plan",
            "不存在满足全部约束的播放变速方案",
            block_count=m,
            max_duration_ms=max_duration_ms,
        )

    # ---- 解码语速选择（三进制，首块为最高位）----
    speeds = []
    for code, k in ((answer_codes[0], half), (answer_codes[1], m - half)):
        choices = []
        for _ in range(k):
            code, choice = divmod(code, 3)
            choices.append(choice)
        speeds.extend(SPEEDS[choice] for choice in reversed(choices))

    # ---- 输出：播放时刻从 0 起按块播放时长与块间静音累加 ----
    blocks = []
    cursor = 0
    for i, (s, e) in enumerate(block_words):
        play = _ceil_div(durations[i] * 100, speeds[i])
        blocks.append(
            {
                "index": i,
                "start_word": s,
                "end_word": e,
                "speaker": words[s].speaker,
                "source_start_ms": words[s].start_ms,
                "source_end_ms": words[e].end_ms,
                "duration_ms": durations[i],
                "speed": speeds[i],
                "playback_duration_ms": play,
                "playback_start_ms": cursor,
                "playback_end_ms": cursor + play,
            }
        )
        cursor += play
        if i + 1 < m:
            cursor += words[block_words[i + 1][0]].start_ms - words[e].end_ms
    return blocks, answer[0], cursor
