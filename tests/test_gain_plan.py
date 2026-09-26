"""响度增益规划核心逻辑单元测试 + 暴力对拍（纯标准库）。

暴力侧用**记忆化递归**独立枚举全部可行增益路径：每帧可行增益为
[-12, min(12, -true_peak_dbfs)] 内的整数，相邻增益差超过 max_step_db 的
边被剪除；状态 (i, prev_gain) 返回后缀上 (总成本, 增益元组) 的最小者
（元组直接比较即天然实现“同成本取字典序最小”）。与被测实现的
“后向 DP 行 + 贪心重建”是两种独立写法，互验最优分与字典序裁决。
"""

import random
import unittest
from functools import lru_cache
from types import SimpleNamespace

from app.errors import DomainError
from app.gain_plan import (
    MAX_FRAMES,
    MAX_GAIN_DB,
    MIN_GAIN_DB,
    STEP_PENALTY,
    plan_gain,
    validate_frames,
)


def F(start, end, loudness, peak):
    return SimpleNamespace(
        start_ms=start,
        end_ms=end,
        loudness_lufs=loudness,
        true_peak_dbfs=peak,
    )


def brute_force(frames, target_lufs, max_step_db):
    """记忆化递归穷举全部路径，返回 (增益列表, 总成本)。恒有可行解。"""
    n = len(frames)
    domains = [
        tuple(range(MIN_GAIN_DB, min(MAX_GAIN_DB, -f.true_peak_dbfs) + 1))
        for f in frames
    ]
    NO_PREV = 99  # 落在 [-12, 12] 之外的哨兵

    @lru_cache(maxsize=None)
    def solve(i, prev_gain):
        """返回后缀 [i, n) 的 (最小总成本, 增益元组)；该前缀状态无解时为 None。"""
        if i == n:
            return 0, ()
        best = None
        for g in domains[i]:
            if prev_gain != NO_PREV and abs(g - prev_gain) > max_step_db:
                continue
            tail = solve(i + 1, g)
            if tail is None:
                continue
            edge = 0 if prev_gain == NO_PREV else STEP_PENALTY * abs(g - prev_gain)
            tail_cost, tail_seq = tail
            node = abs(frames[i].loudness_lufs + g - target_lufs)
            candidate = (node + edge + tail_cost, (g,) + tail_seq)
            if best is None or candidate < best:
                best = candidate
        return best

    total, seq = solve(0, NO_PREV)
    return list(seq), total


class BasicPlanTests(unittest.TestCase):
    def test_already_on_target(self):
        # 单帧已在目标响度：增益 0，成本 0
        out, total_cost = plan_gain([F(0, 1000, -20, -3)], -20, 0)
        self.assertEqual(total_cost, 0)
        self.assertEqual(
            out,
            [
                {
                    "index": 0,
                    "gain_db": 0,
                    "resulting_loudness_lufs": -20,
                    "resulting_true_peak_dbfs": -3,
                }
            ],
        )

    def test_peak_cap_blocks_positive_gain(self):
        # 峰值 -1、响度 -16、目标 -16：理想增益 0，增益后峰值 -1（<= 0）合法
        out, total_cost = plan_gain([F(0, 1000, -16, -1)], -16, 0)
        self.assertEqual([f["gain_db"] for f in out], [0])
        self.assertEqual(total_cost, 0)
        self.assertEqual(out[0]["resulting_true_peak_dbfs"], -1)

    def test_peak_cap_forces_negative_gain(self):
        # 响度 -14、峰值 -1、目标 -16：理想增益 -2，但峰值只允许 g <= 1，
        # -2 本就满足；构造峰值迫使增益封顶：响度 -16、峰值 0、目标 -14，
        # 理想增益 +2 被峰值约束压到 0，付出 2 LU 偏差
        out, total_cost = plan_gain([F(0, 1000, -16, 0)], -14, 0)
        self.assertEqual([f["gain_db"] for f in out], [0])
        self.assertEqual(total_cost, 2)
        self.assertEqual(out[0]["resulting_true_peak_dbfs"], 0)

    def test_two_frames_cost_and_outputs(self):
        # l=(-14,-20)、tp=(-2,-10)、目标 -16、步长 2；
        # (-2,-2)/(0,0)/(2,2) 同成本 6，字典序取 (-2,-2)
        frames = [F(0, 1000, -14, -2), F(1000, 2000, -20, -10)]
        out, total_cost = plan_gain(frames, -16, 2)
        self.assertEqual([f["gain_db"] for f in out], [-2, -2])
        self.assertEqual(total_cost, 6)
        self.assertEqual(
            [(f["resulting_loudness_lufs"], f["resulting_true_peak_dbfs"]) for f in out],
            [(-16, -4), (-22, -12)],
        )

    def test_step_zero_forces_equal_gains(self):
        # 理想增益分别为 4、-6，步长 0：g0=g1，|g-4|+|g+6| 在 [-6,4] 恒为 10，
        # 字典序取 -6
        frames = [F(0, 10, -20, -10), F(10, 20, -10, -10)]
        out, total_cost = plan_gain(frames, -16, 0)
        self.assertEqual([f["gain_db"] for f in out], [-6, -6])
        self.assertEqual(total_cost, 10)

    def test_step_limits_ramp(self):
        # 末帧需要大增益、首帧不需要：步长 2 迫使逐帧爬坡
        frames = [
            F(0, 10, -30, -12),
            F(10, 20, -30, -12),
            F(20, 30, -30, -12),
            F(30, 40, -16, -12),
        ]
        out, total_cost = plan_gain(frames, -16, 2)
        gains = [f["gain_db"] for f in out]
        for a, b in zip(gains, gains[1:]):
            self.assertLessEqual(abs(a - b), 2)
        # 与暴力结果一致
        exp_gains, exp_cost = brute_force(frames, -16, 2)
        self.assertEqual(gains, exp_gains)
        self.assertEqual(total_cost, exp_cost)

    def test_all_minus_twelve_when_target_very_low(self):
        # 目标 -40、帧响度 -80：理想 +40 不可达，增益封顶 +12；
        # 反过来目标 -40 而响度 0：理想 -40，增益只能到 -12
        out, total_cost = plan_gain([F(0, 10, 0, 0), F(10, 20, 0, 0)], -40, 12)
        self.assertEqual([f["gain_db"] for f in out], [-12, -12])
        self.assertEqual(total_cost, 2 * abs(0 - 12 + 40))

    def test_resulting_fields_consistent(self):
        frames = [F(0, 100, -23, -5), F(100, 200, -18, -2)]
        out, _ = plan_gain(frames, -20, 3)
        for i, f in enumerate(out):
            self.assertEqual(f["index"], i)
            self.assertEqual(
                f["resulting_loudness_lufs"], frames[i].loudness_lufs + f["gain_db"]
            )
            self.assertEqual(
                f["resulting_true_peak_dbfs"],
                frames[i].true_peak_dbfs + f["gain_db"],
            )
            self.assertLessEqual(f["resulting_true_peak_dbfs"], 0)
            self.assertGreaterEqual(f["gain_db"], MIN_GAIN_DB)
            self.assertLessEqual(f["gain_db"], MAX_GAIN_DB)


class TieBreakTests(unittest.TestCase):
    def test_lexicographically_smallest_on_plateau(self):
        # 两帧理想增益分别为 0、-1，峰值 0 把增益压到 <= 0，步长 12 无约束：
        # (0,0) 成本 |0|+|1|=1；(-1,-1) 成本 |-1|+|0|=1，并列取字典序更小者
        frames = [F(0, 10, -20, 0), F(10, 20, -19, 0)]
        out, total_cost = plan_gain(frames, -20, 12)
        self.assertEqual([f["gain_db"] for f in out], [-1, -1])
        self.assertEqual(total_cost, 1)

    def test_plateau_induced_by_step_cost(self):
        # 三帧理想增益 0/0/0，步长 0 强制三者相等，g=0 唯一最优；
        # 改为步长惩罚制造平台：理想 (1, 1) 且增益被峰值压到 <= 0——
        # (0,0) 成本 2、(-1,-1) 成本 4，不平；用理想 (0, 0)、步长 1：
        # (0,0) 成本 0 唯一。这里验证更一般的平台：
        # 两帧理想 (1, -1)、步长 0：|g-1|+|g+1| 在 g∈[-1,1] 恒为 2，
        # 增益被峰值压到 <= 0，故 (-1,-1)/(0,0) 同成本 2，取 (-1,-1)
        frames = [F(0, 10, -21, 0), F(10, 20, -19, 0)]
        out, total_cost = plan_gain(frames, -20, 0)
        self.assertEqual([f["gain_db"] for f in out], [-1, -1])
        self.assertEqual(total_cost, 2)

    def test_negative_gain_preferred_when_equal_distance(self):
        # 帧 0 理想 0、帧 1 理想 -1，步长 0 强制相等，g=-1 成本 1、g=0 成本 1，
        # 取字典序更小的 -1
        frames = [F(0, 10, -20, 0), F(10, 20, -19, 0)]
        out, total_cost = plan_gain(frames, -20, 0)
        self.assertEqual([f["gain_db"] for f in out], [-1, -1])
        self.assertEqual(total_cost, 1)


class ValidationTests(unittest.TestCase):
    def test_empty_frames(self):
        with self.assertRaises(DomainError) as ctx:
            validate_frames([])
        self.assertEqual(ctx.exception.code, "empty_frames")

    def test_negative_start(self):
        with self.assertRaises(DomainError) as ctx:
            plan_gain([F(-1, 10, -20, -3)], -20, 0)
        self.assertEqual(ctx.exception.code, "invalid_time_range")
        self.assertEqual(ctx.exception.extra["frame_index"], 0)

    def test_zero_duration_rejected(self):
        # 测量帧要求正时长：start_ms == end_ms 非法
        with self.assertRaises(DomainError) as ctx:
            plan_gain([F(100, 100, -20, -3)], -20, 0)
        self.assertEqual(ctx.exception.code, "invalid_time_range")
        self.assertEqual(ctx.exception.extra["frame_index"], 0)

    def test_inverted_duration_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            plan_gain([F(200, 100, -20, -3), F(100, 200, -20, -3)], -20, 0)
        self.assertEqual(ctx.exception.code, "invalid_time_range")
        self.assertEqual(ctx.exception.extra["frame_index"], 0)

    def test_out_of_order_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            plan_gain(
                [F(200, 300, -20, -3), F(0, 100, -20, -3)], -20, 0
            )
        self.assertEqual(ctx.exception.code, "time_out_of_order")
        self.assertEqual(ctx.exception.extra["frame_index"], 1)

    def test_overlap_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            plan_gain(
                [F(0, 200, -20, -3), F(100, 300, -20, -3)], -20, 0
            )
        self.assertEqual(ctx.exception.code, "overlapping_time_range")
        self.assertEqual(ctx.exception.extra["frame_index"], 1)

    def test_touching_endpoints_allowed(self):
        out, _ = plan_gain(
            [F(0, 100, -20, -3), F(100, 200, -20, -3)], -20, 0
        )
        self.assertEqual(len(out), 2)

    def test_300_frames_accepted_at_core(self):
        frames = [F(i * 10, i * 10 + 10, -20, -3) for i in range(MAX_FRAMES)]
        out, total_cost = plan_gain(frames, -20, 12)
        self.assertEqual(len(out), MAX_FRAMES)
        self.assertEqual(total_cost, 0)
        self.assertTrue(all(f["gain_db"] == 0 for f in out))


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260926)
        for trial in range(2000):
            n = random.randint(1, 8)
            frames = []
            t = random.randint(0, 500)
            for i in range(n):
                if i > 0:
                    t += random.choice([0, 0, 50, 200])  # 端点相接或留间隔
                dur = random.randint(1, 500)
                loudness = random.randint(-80, 0)
                # 峰值取值偏向 0/-1/-12 等：既覆盖宽松可行域，也控制枚举规模
                peak = random.choice(
                    [random.randint(-80, 0), 0, -1, -2, -12]
                )
                frames.append(F(t, t + dur, loudness, peak))
                t += dur
            target = random.randint(-40, 0)
            max_step = random.randint(0, 12)

            exp_gains, exp_cost = brute_force(frames, target, max_step)
            out, total_cost = plan_gain(frames, target, max_step)
            gains = [f["gain_db"] for f in out]
            self.assertEqual(gains, exp_gains, f"trial {trial}: {frames}")
            self.assertEqual(total_cost, exp_cost, f"trial {trial}")

            # 自洽校验：峰值安全、步长约束、增益范围
            for i, f in enumerate(out):
                self.assertGreaterEqual(f["gain_db"], MIN_GAIN_DB)
                self.assertLessEqual(f["gain_db"], MAX_GAIN_DB)
                self.assertLessEqual(
                    frames[i].true_peak_dbfs + f["gain_db"], 0, f"trial {trial}"
                )
            for a, b in zip(gains, gains[1:]):
                self.assertLessEqual(abs(a - b), max_step, f"trial {trial}")

    def test_extreme_frames_match_brute_force(self):
        # 边界值拉满：响度/峰值取 -80 与 0，目标取 -40 与 0，步长 0/12
        random.seed(99)
        for trial in range(200):
            n = random.randint(1, 6)
            frames = []
            t = 0
            for i in range(n):
                t += random.choice([0, 100])
                dur = random.randint(1, 300)
                frames.append(
                    F(
                        t,
                        t + dur,
                        random.choice([-80, -40, -20, 0]),
                        random.choice([-80, -12, -1, 0]),
                    )
                )
                t += dur
            target = random.choice([-40, -20, 0])
            max_step = random.choice([0, 1, 12])
            exp_gains, exp_cost = brute_force(frames, target, max_step)
            out, total_cost = plan_gain(frames, target, max_step)
            self.assertEqual(
                [f["gain_db"] for f in out], exp_gains, f"trial {trial}"
            )
            self.assertEqual(total_cost, exp_cost, f"trial {trial}")

    def test_long_sequence_runs_fast(self):
        # 300 帧规模下应快速完成且约束自洽
        frames = [
            F(i * 100, i * 100 + 90, -30 + (i % 25) - 12, -2 - (i % 3))
            for i in range(MAX_FRAMES)
        ]
        out, total_cost = plan_gain(frames, -18, 2)
        self.assertEqual(len(out), MAX_FRAMES)
        gains = [f["gain_db"] for f in out]
        for i, g in enumerate(gains):
            self.assertLessEqual(frames[i].true_peak_dbfs + g, 0)
            if i:
                self.assertLessEqual(abs(g - gains[i - 1]), 2)
        # 用定义直接复核总成本
        manual = sum(
            abs(frames[i].loudness_lufs + gains[i] + 18) for i in range(MAX_FRAMES)
        ) + STEP_PENALTY * sum(
            abs(gains[i] - gains[i - 1]) for i in range(1, MAX_FRAMES)
        )
        self.assertEqual(total_cost, manual)


if __name__ == "__main__":
    unittest.main()
