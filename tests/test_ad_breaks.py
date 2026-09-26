"""广告插播点核心逻辑单元测试 + 暴力枚举对拍（纯标准库）。

暴力枚举全部候选组合，校验恰选 break_count 个、相邻时间间隔达标，
按 (总静音降序, 时间序列字典序升序) 取最优，与 optimal_ad_breaks 对比。
"""

import itertools
import random
import unittest
from types import SimpleNamespace

from app.ad_breaks import optimal_ad_breaks
from app.errors import DomainError


def W(start, end, text="字", speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def make_words(gaps, dur=500, start=0):
    """按词间静音间隔序列构造词序列：每词时长 dur，gaps[i] 为词 i 与 i+1 之间的静音。"""
    words = []
    t = start
    for gap in gaps:
        words.append(W(t, t + dur))
        t += dur + gap
    words.append(W(t, t + dur))
    return words


def brute_force(words, break_count, min_silence_ms, min_spacing_ms):
    """返回 (总静音, 时间元组) 或 None（无可行方案）。"""
    cands = []
    for i in range(len(words) - 1):
        s, e = words[i].end_ms, words[i + 1].start_ms
        if e - s >= min_silence_ms:
            cands.append(((s + e) // 2, e - s))
    best = None  # (-总静音, 时间元组) 取最小即 (总静音最大, 字典序最小)
    for combo in itertools.combinations(range(len(cands)), break_count):
        times = tuple(cands[k][0] for k in combo)
        if any(b - a < min_spacing_ms for a, b in zip(times, times[1:])):
            continue
        total = sum(cands[k][1] for k in combo)
        key = (-total, times)
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return -best[0], best[1]


class CandidateTests(unittest.TestCase):
    def test_candidates_need_min_silence_and_use_midpoint(self):
        # 静音 800（达标）与 400（不达标，阈值 500）；中点 = (500+1300)//2 = 900
        words = make_words([800, 400], dur=500)
        breaks, total = optimal_ad_breaks(words, 1, 500, 0)
        self.assertEqual(len(breaks), 1)
        self.assertEqual(
            breaks[0],
            {
                "index": 0,
                "time_ms": 900,
                "silence_start_ms": 500,
                "silence_end_ms": 1300,
                "silence_ms": 800,
            },
        )
        self.assertEqual(total, 800)

    def test_odd_silence_midpoint_floors(self):
        # 静音区间 [500, 1301]，中点 (500+1301)//2 = 900（向下取整）
        words = make_words([801], dur=500)
        breaks, _ = optimal_ad_breaks(words, 1, 1, 0)
        self.assertEqual(breaks[0]["time_ms"], 900)
        self.assertEqual(breaks[0]["silence_ms"], 801)

    def test_touching_words_yield_no_candidates(self):
        # 端点相接（静音 0）不满足正整数阈值
        words = make_words([0, 0], dur=500)
        with self.assertRaises(DomainError) as ctx:
            optimal_ad_breaks(words, 1, 1, 0)
        self.assertEqual(ctx.exception.code, "infeasible_ad_breaks")
        self.assertEqual(ctx.exception.extra["candidate_count"], 0)


class SelectionTests(unittest.TestCase):
    def test_maximizes_total_silence(self):
        # 三处静音 300/900/500，选 2 个、无间隔限制 -> 900 + 500
        words = make_words([300, 900, 500], dur=500)
        breaks, total = optimal_ad_breaks(words, 2, 100, 0)
        self.assertEqual(total, 1400)
        self.assertEqual([b["silence_ms"] for b in breaks], [900, 500])

    def test_spacing_constraint_excludes_close_candidates(self):
        # 三处静音各 1000ms，中点间隔 1500ms；min_spacing 2000 -> 只能取首尾
        words = make_words([1000, 1000, 1000], dur=500)
        breaks, total = optimal_ad_breaks(words, 2, 100, 2000)
        self.assertEqual(total, 2000)
        times = [b["time_ms"] for b in breaks]
        self.assertEqual(times, [1000, 4000])
        self.assertGreaterEqual(times[1] - times[0], 2000)

    def test_spacing_infeasible(self):
        # 两处静音中点相距 1500ms，要求间隔 2000ms -> 选不出 2 个
        words = make_words([1000, 1000], dur=500)
        with self.assertRaises(DomainError) as ctx:
            optimal_ad_breaks(words, 2, 100, 2000)
        self.assertEqual(ctx.exception.code, "infeasible_ad_breaks")
        self.assertEqual(ctx.exception.extra["break_count"], 2)

    def test_not_enough_candidates(self):
        words = make_words([1000], dur=500)
        with self.assertRaises(DomainError) as ctx:
            optimal_ad_breaks(words, 2, 100, 0)
        self.assertEqual(ctx.exception.code, "infeasible_ad_breaks")
        self.assertEqual(ctx.exception.extra["candidate_count"], 1)

    def test_tie_break_prefers_earliest_times(self):
        # 四处等长静音、无间隔限制，选 2 个：总静音相同，取时间序列字典序最小
        words = make_words([800, 800, 800, 800], dur=500)
        breaks, total = optimal_ad_breaks(words, 2, 100, 0)
        self.assertEqual(total, 1600)
        self.assertEqual([b["time_ms"] for b in breaks], [900, 2200])

    def test_tie_break_with_spacing(self):
        # 五处等长静音，中点依次相距 1400ms，选 3 个、间隔 >= 1300：
        # 可行解众多且总静音相同，字典序最小为第 0、1、2 个候选
        words = make_words([600] * 5, dur=800)
        breaks, total = optimal_ad_breaks(words, 3, 100, 1300)
        self.assertEqual(total, 1800)
        self.assertEqual([b["time_ms"] for b in breaks], [1100, 2500, 3900])

    def test_exact_candidate_count(self):
        # 候选数恰等于 break_count：唯一可行解
        words = make_words([700, 900], dur=500)
        breaks, total = optimal_ad_breaks(words, 2, 100, 0)
        self.assertEqual(total, 1600)
        self.assertEqual([b["index"] for b in breaks], [0, 1])

    def test_break_count_upper_bound_accepted(self):
        words = make_words([100] * 5, dur=500)
        breaks, _ = optimal_ad_breaks(words, 5, 100, 0)
        self.assertEqual(len(breaks), 5)


class ValidationReuseTests(unittest.TestCase):
    def test_time_out_of_order_reused(self):
        words = [W(1000, 2000), W(900, 1500)]
        with self.assertRaises(DomainError) as ctx:
            optimal_ad_breaks(words, 1, 1, 0)
        self.assertEqual(ctx.exception.code, "time_out_of_order")

    def test_overlap_reused(self):
        words = [W(0, 2000), W(1500, 3000)]
        with self.assertRaises(DomainError) as ctx:
            optimal_ad_breaks(words, 1, 1, 0)
        self.assertEqual(ctx.exception.code, "overlapping_time_range")

    def test_invalid_word_range_reused(self):
        with self.assertRaises(DomainError) as ctx:
            optimal_ad_breaks([W(2000, 1000)], 1, 1, 0)
        self.assertEqual(ctx.exception.code, "invalid_time_range")

    def test_empty_words_reused(self):
        with self.assertRaises(DomainError) as ctx:
            optimal_ad_breaks([], 1, 1, 0)
        self.assertEqual(ctx.exception.code, "empty_words")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260924)
        checked = 0
        for trial in range(400):
            n = random.randint(2, 10)
            dur = random.choice([300, 500, 800])
            gaps = [random.choice([0, 100, 300, 600, 1000, 1500]) for _ in range(n - 1)]
            words = make_words(gaps, dur=dur)
            break_count = random.randint(1, 5)
            min_silence = random.choice([1, 200, 500, 900])
            min_spacing = random.choice([0, 400, 1000, 2500])

            expected = brute_force(words, break_count, min_silence, min_spacing)
            if expected is None:
                with self.assertRaises(DomainError) as ctx:
                    optimal_ad_breaks(words, break_count, min_silence, min_spacing)
                self.assertEqual(ctx.exception.code, "infeasible_ad_breaks")
            else:
                exp_total, exp_times = expected
                breaks, total = optimal_ad_breaks(
                    words, break_count, min_silence, min_spacing
                )
                times = tuple(b["time_ms"] for b in breaks)
                self.assertEqual(total, exp_total, f"trial {trial} total mismatch")
                self.assertEqual(times, exp_times, f"trial {trial} tie-break mismatch")
                # 额外校验：时间升序、间隔约束、静音字段自洽
                self.assertEqual(len(breaks), break_count)
                for b in breaks:
                    self.assertEqual(
                        b["silence_ms"], b["silence_end_ms"] - b["silence_start_ms"]
                    )
                    self.assertGreaterEqual(b["silence_ms"], min_silence)
                    self.assertEqual(
                        b["time_ms"], (b["silence_start_ms"] + b["silence_end_ms"]) // 2
                    )
                for a, b in zip(times, times[1:]):
                    self.assertGreaterEqual(b - a, min_spacing)
                checked += 1
        self.assertGreater(checked, 100)  # 确保有足够多的可行对拍样本


if __name__ == "__main__":
    unittest.main()
