"""响度增益规划核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

输入 1～300 个按时间排列且互不重叠的测量帧 frames（每帧含 start_ms /
end_ms / loudness_lufs / true_peak_dbfs）与 target_lufs、max_step_db，
为每帧选择 -12～12 的整数 dB 增益 g_i，满足：

* 峰值安全：true_peak_dbfs_i + g_i <= 0，即 g_i <= -true_peak_dbfs_i；
  （增益下限 -12 恒满足该式，因为 true_peak_dbfs_i <= 0，故可行域
  [-12, min(12, -true_peak_dbfs_i)] 始终非空——全取 -12 永远可行；）
* 平滑：相邻增益差 |g_{i+1} - g_i| <= max_step_db。

目标：最小化
Σ_i |loudness_lufs_i + g_i - target_lufs|
  + 2 · Σ_{i=1}^{n-1} |g_i - g_{i-1}|，
同分时取增益序列**字典序最小**者（-12 < -11 < … < 12）。

算法（链式后向 DP + 贪心重建），O(n·G²) 时间、O(n·G) 空间
（n ≤ 300 帧、G = 25 个增益档，约 19 万次状态转移）：

* 每帧可行增益为升序整数区间 [-12, cap_i]，cap_i = min(12, -true_peak_dbfs_i)；
* 理想增益 ideal_i = target_lufs - loudness_lufs_i：节点代价
  |loudness_lufs_i + g - target_lufs| 即 |g - ideal_i|；
* bwd[i][k] 表示帧 i 取第 k 个可行增益时，后缀 [i, n) 的最小代价
  （含帧 i 节点代价与其后全部相邻变化代价），末层 bwd[n-1][k] 仅为
  节点代价，前层枚举“变化幅度不超过 max_step_db”的后继取最小；
* 重建从左向右：每步在当前可行增益中**升序**取第一个使
  “帧 i 节点代价 + 相邻变化代价 + bwd[i+1][k2]”恰等于剩余最优代价的
  增益——枚举顺序即取字典序最小的可行当前值，归纳可得整条增益序列
  字典序最小。
"""

from .errors import DomainError

MIN_GAIN_DB = -12           # 每帧增益下限（含）
MAX_GAIN_DB = 12            # 每帧增益上限（含）
MAX_FRAMES = 300            # 测量帧数上限
STEP_PENALTY = 2            # 每 1dB 相邻增益变化的代价权重

INF = 10**18


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def validate_frames(frames):
    """校验帧序列：非空、帧内 start_ms < end_ms、时间非负、相邻不倒序、不重叠。

    与 validate_words 的区别：测量帧要求**正时长**（start_ms 严格小于
    end_ms）；端点相接（下帧 start_ms == 上帧 end_ms）允许。
    """
    if not frames:
        _fail("empty_frames", "frames 不能为空")

    prev = None
    for idx, f in enumerate(frames):
        if f.start_ms < 0:
            _fail(
                "invalid_time_range",
                f"第 {idx} 个帧的 start_ms({f.start_ms}) 不能为负",
                frame_index=idx,
            )
        if f.end_ms <= f.start_ms:
            _fail(
                "invalid_time_range",
                f"第 {idx} 个帧的 end_ms({f.end_ms}) 必须严格大于 "
                f"start_ms({f.start_ms})",
                frame_index=idx,
            )
        if prev is not None:
            if f.start_ms < prev.start_ms:
                _fail(
                    "time_out_of_order",
                    f"第 {idx} 个帧的 start_ms({f.start_ms}) 早于前一个帧的 "
                    f"start_ms({prev.start_ms})，输入必须按时间排列",
                    frame_index=idx,
                )
            if f.start_ms < prev.end_ms:
                _fail(
                    "overlapping_time_range",
                    f"第 {idx} 个帧的 start_ms({f.start_ms}) 早于前一个帧的 "
                    f"end_ms({prev.end_ms})，相邻帧时间不允许重叠",
                    frame_index=idx,
                )
        prev = f


def plan_gain(frames, target_lufs, max_step_db):
    """为每个测量帧选择整数 dB 增益，返回 (out_frames, total_cost)。

    参数 frames 中每个对象需具备属性：start_ms / end_ms /
    loudness_lufs / true_peak_dbfs（响度、峰值、目标值均已由 Pydantic
    限定为整数，核心层再经 validate_frames 校验时间序）。

    out_frames 按帧顺序返回，每项为 dict：index / gain_db /
    resulting_loudness_lufs（loudness_lufs + gain_db）/
    resulting_true_peak_dbfs（true_peak_dbfs + gain_db，<= 0）。
    total_cost 为最小总成本
    Σ|loudness+gain-target| + 2·Σ|相邻增益差|。
    """
    validate_frames(frames)
    n = len(frames)

    # ---- 每帧可行增益升序区间 [-12, cap] 与理想增益 ----
    options = []
    ideals = []
    for f in frames:
        cap = min(MAX_GAIN_DB, -f.true_peak_dbfs)
        options.append(range(MIN_GAIN_DB, cap + 1))
        ideals.append(target_lufs - f.loudness_lufs)

    # ---- 后向 DP：bwd[i][k] = 帧 i 取其第 k 个可行增益时后缀 [i,n) 最小代价 ----
    bwd = [None] * n
    last = [abs(g - ideals[n - 1]) for g in options[n - 1]]
    bwd[n - 1] = last
    for i in range(n - 2, -1, -1):
        cur_opts = options[i]
        nxt_opts = options[i + 1]
        nxt_row = bwd[i + 1]
        row = [INF] * len(cur_opts)
        for k, g in enumerate(cur_opts):
            best = INF
            lo = g - max_step_db
            hi = g + max_step_db
            # 后继增益须落在 [lo, hi] 内；用边界夹出下标区间（区间均升序）
            k2_lo = max(0, lo - nxt_opts.start)
            k2_hi = min(len(nxt_opts) - 1, hi - nxt_opts.start)
            for k2 in range(k2_lo, k2_hi + 1):
                cand = STEP_PENALTY * abs(nxt_opts[k2] - g) + nxt_row[k2]
                if cand < best:
                    best = cand
            row[k] = abs(g - ideals[i]) + best
        bwd[i] = row

    total_cost = min(bwd[0])

    # ---- 贪心重建：自左向右每步取仍能达成剩余最优代价的最小增益 ----
    gains = [0] * n
    prev_gain = None
    remaining = total_cost
    for i in range(n):
        opts = options[i]
        chosen = None
        for k, g in enumerate(opts):
            if prev_gain is not None and abs(g - prev_gain) > max_step_db:
                continue
            edge = 0 if prev_gain is None else STEP_PENALTY * abs(g - prev_gain)
            node = abs(g - ideals[i])
            if i + 1 < n:
                # bwd[i][k] = 节点代价 + min(2·|后继增益-g| + bwd[i+1])
                suffix_cost = _best_successor(
                    bwd[i + 1], options[i + 1], g, max_step_db
                )
                if edge + node + suffix_cost != remaining:
                    continue
            elif edge + node != remaining:
                continue
            chosen = g
            remaining -= edge + node
            break
        gains[i] = chosen
        prev_gain = chosen

    out_frames = [
        {
            "index": i,
            "gain_db": gains[i],
            "resulting_loudness_lufs": frames[i].loudness_lufs + gains[i],
            "resulting_true_peak_dbfs": frames[i].true_peak_dbfs + gains[i],
        }
        for i in range(n)
    ]
    return out_frames, total_cost


def _best_successor(suffix_row, nxt_opts, gain, max_step_db):
    """取本帧增益 gain 时，后继部分的最小代价：

    min_{|g' - gain| <= max_step_db} 2·|g' - gain| + suffix_row[g']。
    与后向 DP 的转移口径一致（含到后继帧的相邻变化代价）。
    """
    lo = gain - max_step_db
    hi = gain + max_step_db
    k2_lo = max(0, lo - nxt_opts.start)
    k2_hi = min(len(nxt_opts) - 1, hi - nxt_opts.start)
    best = INF
    for k2 in range(k2_lo, k2_hi + 1):
        cand = STEP_PENALTY * abs(nxt_opts[k2] - gain) + suffix_row[k2]
        if cand < best:
            best = cand
    return best
