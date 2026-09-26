"""叠话统计报告核心逻辑单元测试 + 暴力对拍（纯标准库）。

暴力实现：把所有非零时长词的边界时刻离散化，对每个基本区间逐词判定活跃
说话人集合（词的左闭右开区间 [start_ms, end_ms) 覆盖该区间左端点即可，
基本区间上活跃集合恒定），再汇总发声覆盖、叠话区间与说话人对重叠，
与 build_crosstalk_report 对比。
"""

import random
import unittest
from collections import Counter
from itertools import combinations
from types import SimpleNamespace

from app.crosstalk_report import build_crosstalk_report
from app.errors import DomainError


def W(start, end, text="词", speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def brute_force(words):
    """独立实现：返回 (covered, segments, pair_overlap)，结构与实现不同，互验。"""
    points = sorted(
        {w.start_ms for w in words if w.end_ms > w.start_ms}
        | {w.end_ms for w in words if w.end_ms > w.start_ms}
    )
    covered = Counter()
    pair_overlap = Counter()
    segments = []  # [start_ms, end_ms, speakers]
    for a, b in zip(points, points[1:]):
        active = sorted({w.speaker for w in words if w.start_ms <= a < w.end_ms})
        duration = b - a
        for s in active:
            covered[s] += duration
        if len(active) >= 2:
            for pair in combinations(active, 2):
                pair_overlap[pair] += duration
            if segments and segments[-1][1] == a and segments[-1][2] == active:
                segments[-1][1] = b
            else:
                segments.append([a, b, active])
    return covered, segments, pair_overlap


class BasicReportTests(unittest.TestCase):
    def test_basic_overlap_with_unordered_input(self):
        # A: [0,2000) ∪ [1000,3000) => 覆盖 [0,3000)；B: [1500,2500) ∪ [4000,5000)
        words = [
            W(1000, 3000, "没错", "A"),  # 故意乱序
            W(0, 2000, "我觉得", "A"),
            W(1500, 2500, "对对对", "B"),
            W(4000, 5000, "下期再见", "B"),
        ]
        speakers, segments, pair_overlaps = build_crosstalk_report(words)
        self.assertEqual(
            speakers,
            [
                {"speaker": "A", "covered_ms": 3000},
                {"speaker": "B", "covered_ms": 2000},
            ],
        )
        # [1500,2000) 与 [2000,2500) 活跃集合都是 {A,B}，端点相接合并为一段
        self.assertEqual(
            segments,
            [
                {
                    "index": 0,
                    "start_ms": 1500,
                    "end_ms": 2500,
                    "duration_ms": 1000,
                    "speakers": ["A", "B"],
                }
            ],
        )
        self.assertEqual(
            pair_overlaps,
            [{"speaker_a": "A", "speaker_b": "B", "overlap_ms": 1000}],
        )

    def test_touching_endpoints_do_not_overlap(self):
        # 端点相接（左闭右开）：A 结束于 1000 时 B 才开始，不计重叠
        words = [W(0, 1000, "甲", "A"), W(1000, 2000, "乙", "B")]
        speakers, segments, pair_overlaps = build_crosstalk_report(words)
        self.assertEqual(
            speakers,
            [
                {"speaker": "A", "covered_ms": 1000},
                {"speaker": "B", "covered_ms": 1000},
            ],
        )
        self.assertEqual(segments, [])
        self.assertEqual(pair_overlaps, [])

    def test_zero_duration_words_ignored(self):
        # 零时长词不参与统计：B 的零时长词落在 A 的区间内也不产生叠话
        words = [
            W(0, 1000, "甲", "A"),
            W(500, 500, "乙", "B"),
            W(2000, 2000, "丙", "C"),
        ]
        speakers, segments, pair_overlaps = build_crosstalk_report(words)
        self.assertEqual(
            speakers,
            [
                {"speaker": "A", "covered_ms": 1000},
                {"speaker": "B", "covered_ms": 0},
                {"speaker": "C", "covered_ms": 0},
            ],
        )
        self.assertEqual(segments, [])
        self.assertEqual(pair_overlaps, [])

    def test_same_speaker_overlap_counted_once(self):
        # 同一说话人重叠词只计一次：A 覆盖 [0,3000) 共 3000ms；
        # B 与 A 叠话 [1000,2000) 一段，不因 A 的重叠词重复计
        words = [
            W(0, 2000, "甲", "A"),
            W(1000, 3000, "乙", "A"),
            W(1000, 2000, "丙", "B"),
        ]
        speakers, segments, pair_overlaps = build_crosstalk_report(words)
        self.assertEqual(
            speakers,
            [
                {"speaker": "A", "covered_ms": 3000},
                {"speaker": "B", "covered_ms": 1000},
            ],
        )
        self.assertEqual(
            [(s["start_ms"], s["end_ms"], s["speakers"]) for s in segments],
            [(1000, 2000, ["A", "B"])],
        )
        self.assertEqual(
            pair_overlaps,
            [{"speaker_a": "A", "speaker_b": "B", "overlap_ms": 1000}],
        )

    def test_three_way_overlap_counts_three_pairs(self):
        # [2000,3000) 三人同时发声，时长分别计入 AB、AC、BC 三对
        words = [
            W(0, 3000, "甲", "A"),
            W(1000, 4000, "乙", "B"),
            W(2000, 5000, "丙", "C"),
        ]
        speakers, segments, pair_overlaps = build_crosstalk_report(words)
        self.assertEqual(
            speakers,
            [
                {"speaker": "A", "covered_ms": 3000},
                {"speaker": "B", "covered_ms": 3000},
                {"speaker": "C", "covered_ms": 3000},
            ],
        )
        self.assertEqual(
            [(s["start_ms"], s["end_ms"], s["speakers"]) for s in segments],
            [(1000, 2000, ["A", "B"]), (2000, 3000, ["A", "B", "C"]), (3000, 4000, ["B", "C"])],
        )
        self.assertEqual(
            pair_overlaps,
            [
                {"speaker_a": "A", "speaker_b": "B", "overlap_ms": 2000},
                {"speaker_a": "A", "speaker_b": "C", "overlap_ms": 1000},
                {"speaker_a": "B", "speaker_b": "C", "overlap_ms": 2000},
            ],
        )

    def test_segments_not_merged_across_single_speaker_gap(self):
        # 两段 {A,B} 之间隔着只有 A 的区间：不合并
        words = [
            W(0, 1000, "甲", "A"),
            W(0, 500, "乙", "B"),
            W(800, 1000, "丙", "B"),
        ]
        _, segments, pair_overlaps = build_crosstalk_report(words)
        self.assertEqual(
            [(s["index"], s["start_ms"], s["end_ms"]) for s in segments],
            [(0, 0, 500), (1, 800, 1000)],
        )
        # 同一对的重叠毫秒数跨段累加
        self.assertEqual(
            pair_overlaps,
            [{"speaker_a": "A", "speaker_b": "B", "overlap_ms": 700}],
        )

    def test_segment_merged_when_set_unchanged_across_event(self):
        # t=1000 处 A 一词结束、一词开始，活跃集合恒为 {A,B}：合并为一段
        words = [
            W(0, 1000, "甲", "A"),
            W(1000, 2000, "乙", "A"),
            W(500, 1500, "丙", "B"),
        ]
        _, segments, _ = build_crosstalk_report(words)
        self.assertEqual(
            segments,
            [
                {
                    "index": 0,
                    "start_ms": 500,
                    "end_ms": 1500,
                    "duration_ms": 1000,
                    "speakers": ["A", "B"],
                }
            ],
        )

    def test_speaker_ids_sorted(self):
        # 说话人按标识排序（字符串序，与输入顺序无关）
        words = [W(0, 1000, "甲", "b"), W(500, 800, "乙", "A"), W(600, 900, "丙", "10")]
        speakers, segments, pair_overlaps = build_crosstalk_report(words)
        self.assertEqual([s["speaker"] for s in speakers], ["10", "A", "b"])
        self.assertEqual(
            [s["speakers"] for s in segments],
            [["A", "b"], ["10", "A", "b"], ["10", "b"]],
        )
        self.assertEqual(
            [(p["speaker_a"], p["speaker_b"]) for p in pair_overlaps],
            [("10", "A"), ("10", "b"), ("A", "b")],
        )


class ValidationTests(unittest.TestCase):
    def test_invalid_time_range_uses_input_order(self):
        words = [W(500, 1000, "甲"), W(300, 100, "乙")]  # 第 2 个词 end < start
        with self.assertRaises(DomainError) as ctx:
            build_crosstalk_report(words)
        err = ctx.exception
        self.assertEqual(err.code, "invalid_time_range")
        self.assertEqual(err.extra["word_index"], 1)

    def test_unordered_and_overlapping_accepted(self):
        # 乱序 + 交叠均合法
        words = [W(1000, 2000, "甲", "A"), W(0, 1500, "乙", "B")]
        _, segments, _ = build_crosstalk_report(words)
        self.assertEqual(len(segments), 1)

    def test_too_many_speakers(self):
        words = [
            W(i * 100, i * 100 + 50, "词", f"S{i:02d}") for i in range(21)
        ]
        with self.assertRaises(DomainError) as ctx:
            build_crosstalk_report(words)
        err = ctx.exception
        self.assertEqual(err.code, "too_many_speakers")
        self.assertEqual(err.extra["speaker_count"], 21)
        # 20 名恰好在上限内
        speakers, _, _ = build_crosstalk_report(words[:20])
        self.assertEqual(len(speakers), 20)

    def test_zero_duration_speaker_still_counts_toward_limit(self):
        # 说话人上限按输入词统计，零时长词的说话人也计入
        words = [W(i * 100, i * 100 + 50, "词", f"S{i:02d}") for i in range(20)]
        words.append(W(0, 0, "词", "S20"))
        with self.assertRaises(DomainError) as ctx:
            build_crosstalk_report(words)
        self.assertEqual(ctx.exception.code, "too_many_speakers")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260925)
        with_overlap = 0
        for trial in range(2000):
            n = random.randint(1, 8)
            pool = [f"S{i}" for i in range(random.randint(1, 4))]
            words = [
                W(
                    (start := random.randint(0, 30)),
                    start + random.choice([0, 0, 1, 2, 5, 8]),
                    "词",
                    random.choice(pool),
                )
                for _ in range(n)
            ]
            random.shuffle(words)  # 输入允许乱序

            exp_covered, exp_segments, exp_pairs = brute_force(words)
            speakers, segments, pair_overlaps = build_crosstalk_report(words)

            # 各说话人发声覆盖（仅含零时长词的说话人覆盖为 0）
            self.assertEqual(
                {s["speaker"]: s["covered_ms"] for s in speakers},
                {s: exp_covered.get(s, 0) for s in sorted({w.speaker for w in words})},
                f"trial {trial}",
            )
            # 叠话区间
            self.assertEqual(
                [(s["start_ms"], s["end_ms"], s["speakers"]) for s in segments],
                [(a, b, sp) for a, b, sp in exp_segments],
                f"trial {trial}",
            )
            # 说话人对重叠
            self.assertEqual(
                {(p["speaker_a"], p["speaker_b"]): p["overlap_ms"] for p in pair_overlaps},
                dict(exp_pairs),
                f"trial {trial}",
            )
            # 输出自洽性：index 连续、时长为正、说话人至少两人且按标识排序、对重叠为正
            for i, s in enumerate(segments):
                self.assertEqual(s["index"], i)
                self.assertEqual(s["duration_ms"], s["end_ms"] - s["start_ms"])
                self.assertGreater(s["duration_ms"], 0)
                self.assertGreaterEqual(len(s["speakers"]), 2)
                self.assertEqual(s["speakers"], sorted(s["speakers"]))
            for p in pair_overlaps:
                self.assertLess(p["speaker_a"], p["speaker_b"])
                self.assertGreater(p["overlap_ms"], 0)
            if segments:
                with_overlap += 1
        self.assertGreater(with_overlap, 500)  # 确保有足够多的叠话样本


if __name__ == "__main__":
    unittest.main()
