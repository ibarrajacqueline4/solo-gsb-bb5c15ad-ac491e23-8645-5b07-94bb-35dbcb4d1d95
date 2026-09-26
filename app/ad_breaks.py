"""广告插播点选择核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

候选：相邻词之间静音时长 >= min_silence_ms 的区间，插播时间取静音区间的
整数中点 (start + end) // 2。输入词序列时间有序且不重叠，且候选静音至少
1ms（min_silence_ms 为正整数），可证候选时间随词序**严格递增**：

    mid_i <= (start_{i+1} - 1 + start_{i+1}) // 2 < start_{i+1} <= mid_{i+1}

（end_i < start_{i+1}，而 end_{i+1} >= start_{i+1}、start_{i+2} >= end_{i+1}）。

选择：恰选 break_count 个候选，相邻选中时间至少相隔 min_spacing_ms，
最大化选中静音总毫秒数；同分取时间序列字典序最小者。

算法（分层后缀最大 DP + 贪心重建），O(m * break_count) 时间/空间，
m 为候选数（<= 词数 - 1），break_count <= 5：

* nxt[t]：候选 t 之后第一个满足间隔约束的下标（时间有序，双指针一次扫出）；
* layer[j][i]：从下标 i 起（含）恰选 j 个候选的最大总静音。先对每个 t 算
  g[t] = sil[t] + layer[j-1][nxt[t]]，再对 g 取后缀最大即得 layer[j]；
* 重建：当前下界 lo 与剩余层数 j 的目标值 layer[j][lo] 已知，从左向右找第一个
  满足 sil[t] + layer[j-1][nxt[t]] == 目标值 的 t。任何最优序列的首个选择都
  必然满足该等式（layer 是精确最大值，两头夹逼可得），故每步取最小可行下标
  即得字典序最小的时间序列；之后以 lo = nxt[t] 归纳继续。
"""

from .errors import DomainError
from .segmentation import validate_words

INFEASIBLE = -1  # 静音总量恒为非负整数，-1 可安全表示“不可行”


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def optimal_ad_breaks(words, break_count, min_silence_ms, min_spacing_ms):
    """恰选 break_count 个插播点，使选中静音总毫秒数最大。

    参数 words 中每个对象需具备属性：start_ms / end_ms（text / speaker 本接口
    不使用，但沿用同一套词序与时间校验）。

    返回 (breaks, total_silence_ms)；breaks 按时间升序，每项为 dict：
    index / time_ms / silence_start_ms / silence_end_ms / silence_ms。
    无可行方案时抛出 DomainError(code="infeasible_ad_breaks")。
    """
    validate_words(words)

    # ---- 候选：相邻词之间静音达标的区间 ----
    cand_time = []    # 插播时间：静音区间整数中点
    cand_sil = []     # 静音时长
    cand_start = []   # 静音起点（前词 end_ms）
    cand_end = []     # 静音终点（后词 start_ms）
    for i in range(len(words) - 1):
        s = words[i].end_ms
        e = words[i + 1].start_ms
        if e - s >= min_silence_ms:
            cand_start.append(s)
            cand_end.append(e)
            cand_sil.append(e - s)
            cand_time.append((s + e) // 2)
    m = len(cand_time)

    # ---- nxt[t]：t 之后第一个与 t 间隔 >= min_spacing_ms 的候选下标 ----
    nxt = [0] * m
    r = 0
    for t in range(m):
        r = max(r, t + 1)
        while r < m and cand_time[r] - cand_time[t] < min_spacing_ms:
            r += 1
        nxt[t] = r

    # ---- 分层 DP：layer[j][i] = 从下标 i 起恰选 j 个的最大总静音 ----
    # layer[0][i] 恒为 0（选 0 个总可行）；其余层由 g 的后缀最大得到。
    layers = [[0] * (m + 1)]
    for j in range(1, break_count + 1):
        prev = layers[-1]
        g = [INFEASIBLE] * (m + 1)
        for t in range(m):
            tail = prev[nxt[t]]
            if tail != INFEASIBLE:
                g[t] = cand_sil[t] + tail
        layer = [INFEASIBLE] * (m + 1)
        best = INFEASIBLE
        for i in range(m - 1, -1, -1):
            if g[i] > best:
                best = g[i]
            layer[i] = best
        layers.append(layer)

    optimum = layers[break_count][0]
    if optimum == INFEASIBLE:
        _fail(
            "infeasible_ad_breaks",
            "不存在满足全部约束的插播方案",
            break_count=break_count,
            candidate_count=m,
        )

    # ---- 贪心重建字典序最小的时间序列 ----
    chosen = []
    lo = 0
    for j in range(break_count, 0, -1):
        target = layers[j][lo]
        prev = layers[j - 1]
        t = lo
        while True:  # target 来自后缀最大，必存在可行 t
            tail = prev[nxt[t]]
            if tail != INFEASIBLE and cand_sil[t] + tail == target:
                break
            t += 1
        chosen.append(t)
        lo = nxt[t]

    breaks = [
        {
            "index": idx,
            "time_ms": cand_time[t],
            "silence_start_ms": cand_start[t],
            "silence_end_ms": cand_end[t],
            "silence_ms": cand_sil[t],
        }
        for idx, t in enumerate(chosen)
    ]
    return breaks, optimum
