"""核心分段逻辑单元测试（纯标准库：python -m unittest discover -s tests -v）。"""

import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.segmentation import (
    MAX_CHARS,
    MAX_DURATION_MS,
    MIN_DURATION_MS,
    optimal_segmentation,
)
from app.srt import render_srt


def W(start, end, text, speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def boundaries(segments):
    return [(s["start_word"], s["end_word"]) for s in segments]


class SegmentationHappyPathTests(unittest.TestCase):
    def test_single_word_segment(self):
        # 单词 1.2 秒、2 个字符 —— 单段即可
        words = [W(0, 1200, "你好")]
        segments, score = optimal_segmentation(words)
        self.assertEqual(boundaries(segments), [(0, 0)])
        self.assertEqual(score, 0)  # 末段不记句末标点分
        self.assertEqual(segments[0]["char_count"], 2)
        self.assertEqual(segments[0]["text"], "你好")

    def test_emoji_counted_as_one_code_point(self):
        # 😀 为 1 个 Unicode code point（虽然 UTF-16 下是代理对）
        words = [W(0, 1200, "😀" * MAX_CHARS)]
        segments, _ = optimal_segmentation(words)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["char_count"], MAX_CHARS)

    def test_sentence_ending_avoids_penalty(self):
        # 三段全部以句末标点结尾、时长 >=2s，不同说话人强制三断：总分 0
        words = [
            W(0, 2000, "第一句。", "A"),
            W(2000, 4200, "第二句！", "B"),
            W(4200, 6200, "第三句？", "C"),
        ]
        segments, score = optimal_segmentation(words)
        self.assertEqual(boundaries(segments), [(0, 0), (1, 1), (2, 2)])
        self.assertEqual(score, 0)

    def test_same_score_prefers_fewer_segments(self):
        # 同说话人、都带句末标点时所有切法零分；全合段 6200ms 超上限不可行，
        # (1,) 是 (1,2) 的字典序前缀 -> 两段
        words = [
            W(0, 2000, "第一句。"),
            W(2000, 4200, "第二句！"),
            W(4200, 6200, "第三句？"),
        ]
        segments, score = optimal_segmentation(words)
        self.assertEqual(boundaries(segments), [(0, 0), (1, 2)])
        self.assertEqual(score, 0)

    def test_no_sentence_ending_penalized_non_final(self):
        # 两段都 >=2s 且合段会超 6s 强制断开；首段无句末标点 +2，末段不计
        words = [W(0, 3500, "甲甲乙"), W(3500, 7000, "丙丁戊")]
        segments, score = optimal_segmentation(words)
        self.assertEqual(boundaries(segments), [(0, 0), (1, 1)])
        self.assertEqual(score, 2)

    def test_short_segment_penalty(self):
        # 合段 6500ms 超上限强制断开；首段 1.5s（<2s）+1 且无标点 +2 = 3；末段不计
        words = [W(0, 1500, "甲"), W(1500, 6500, "乙")]
        segments, score = optimal_segmentation(words)
        self.assertEqual(boundaries(segments), [(0, 0), (1, 1)])
        self.assertEqual(score, 3)

    def test_text_concatenated_without_spaces(self):
        words = [W(0, 1100, "你"), W(1100, 2200, "好", )]
        # 注意：合成一段才达 1000ms 下限
        segments, _ = optimal_segmentation(words)
        self.assertEqual(segments[0]["text"], "你好")
        self.assertEqual(segments[0]["duration_ms"], 2200)


class SpeakerChangeTests(unittest.TestCase):
    def test_speaker_change_forces_break(self):
        # A、B 时长各 2.4s，字符都很少；即使合段可行也必须换人断段
        words = [W(0, 2400, "你好", "A"), W(2400, 4800, "世界", "B")]
        segments, score = optimal_segmentation(words)
        self.assertEqual(boundaries(segments), [(0, 0), (1, 1)])
        self.assertEqual([s["speaker"] for s in segments], ["A", "B"])
        self.assertEqual(score, 2)  # 首段无句末标点 +2

    def test_speaker_runs(self):
        words = [
            W(0, 1200, "甲", "A"),
            W(1200, 2400, "乙", "A"),
            W(2400, 3600, "丙", "B"),
            W(3600, 5000, "丁", "B"),
        ]
        segments, _ = optimal_segmentation(words)
        # 任何段都不得跨越 1/2 之间的说话人边界
        for seg in segments:
            speakers = {words[i].speaker for i in range(seg["start_word"], seg["end_word"] + 1)}
            self.assertEqual(len(speakers), 1)
        self.assertEqual(boundaries(segments), [(0, 1), (2, 3)])


class TieBreakTests(unittest.TestCase):
    def test_lexicographically_smallest_breakpoint_sequence(self):
        # 20 个词，每词 500ms、1 个字符、同一说话人、无句末标点。
        # 可行段长（按词数，时长 500k ∈ [1000,6000] -> k∈[2..12]，字符数 k<=18）。
        # 任意完整切分每个断点都是 +2，段数 = 词数/k；20 不可被 2~12 中任何 k 整除，
        # 最小段数组合是 2 段（8+12 / 10+10 / 12+8），均为 2 分，
        # 断点序列分别为 (8,)、(10,)、(12,)，字典序最小 -> 8。
        words = [W(i * 500, i * 500 + 500, "字") for i in range(20)]
        segments, score = optimal_segmentation(words)
        self.assertEqual(score, 2)
        self.assertEqual(boundaries(segments), [(0, 7), (8, 19)])

    def test_fewer_segments_preferred_via_score(self):
        # 能一段装下时，单段为末段不记分，必然最优
        words = [W(i * 300, i * 300 + 300, "字") for i in range(6)]
        segments, score = optimal_segmentation(words)
        self.assertEqual(boundaries(segments), [(0, 5)])
        self.assertEqual(score, 0)


class InfeasibleTests(unittest.TestCase):
    def test_word_too_long_chars(self):
        # 单词 19 个字符，任何分段都放不下
        words = [W(0, 1500, "字" * (MAX_CHARS + 1))]
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation(words)
        self.assertEqual(ctx.exception.code, "infeasible_segmentation")
        self.assertEqual(ctx.exception.extra["last_word_index"], 0)

    def test_word_too_short_forever(self):
        # 单词 800ms 且说话人前后不同 -> 无法与邻词组合，永远不可行
        words = [
            W(0, 1100, "首句。", "A"),
            W(1100, 1900, "短", "B"),
            W(1900, 3000, "末句。", "C"),
        ]
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation(words)
        err = ctx.exception
        self.assertEqual(err.code, "infeasible_segmentation")
        self.assertEqual(err.extra["last_word_index"], 1)

    def test_earliest_unreachable_prefix(self):
        # 第 0 个词 900ms 且独占说话人：连第一个前缀都不可达
        words = [W(0, 900, "字", "A"), W(900, 3000, "后续内容", "B")]
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation(words)
        self.assertEqual(ctx.exception.extra["last_word_index"], 0)

    def test_overlong_duration_gap(self):
        # 词内间隔巨大：两个词各 >=6s，且无法合并（合并超 6s）
        # 单词本身 6500ms 已超上限，第一个词即不可行
        words = [W(0, 6500, "长句内容", "A"), W(6500, 13000, "另一句", "B")]
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation(words)
        self.assertEqual(ctx.exception.extra["last_word_index"], 0)
        self.assertLessEqual(MAX_DURATION_MS, 6000)


class ValidationTests(unittest.TestCase):
    def test_time_out_of_order(self):
        words = [W(1000, 2000, "甲"), W(900, 1500, "乙")]
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation(words)
        self.assertEqual(ctx.exception.code, "time_out_of_order")
        self.assertEqual(ctx.exception.extra["word_index"], 1)

    def test_overlapping_with_nondecreasing_start(self):
        # start 不倒序，但早于前一个词的 end
        words = [W(0, 2000, "甲"), W(1500, 3000, "乙")]
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation(words)
        self.assertEqual(ctx.exception.code, "overlapping_time_range")
        self.assertEqual(ctx.exception.extra["word_index"], 1)

    def test_touching_endpoints_allowed(self):
        # end == 下一个 start 不算重叠；合段 7s 超上限故仍分两段
        words = [W(0, 3500, "甲。"), W(3500, 7000, "乙。")]
        segments, score = optimal_segmentation(words)
        self.assertEqual(len(segments), 2)
        self.assertEqual(score, 0)

    def test_word_end_before_start(self):
        words = [W(2000, 1000, "甲")]
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation(words)
        self.assertEqual(ctx.exception.code, "invalid_time_range")

    def test_empty_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            optimal_segmentation([])
        self.assertEqual(ctx.exception.code, "empty_words")


class SrtTests(unittest.TestCase):
    def test_render_srt(self):
        # 合段 7s 超上限，强制两段
        words = [W(0, 3500, "你好。"), W(3500, 7000, "世界！")]
        segments, _ = optimal_segmentation(words)
        srt = render_srt(segments)
        lines = srt.splitlines()
        self.assertEqual(lines[0], "1")
        self.assertEqual(lines[1], "00:00:00,000 --> 00:00:03,500")
        self.assertEqual(lines[2], "你好。")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], "2")
        self.assertEqual(lines[5], "00:00:03,500 --> 00:00:07,000")
        self.assertTrue(srt.endswith("\n"))

    def test_timestamp_hour_carry(self):
        words = [W(3_600_000, 3_600_000 + 1200, "你好")]
        segments, _ = optimal_segmentation(words)
        srt = render_srt(segments)
        self.assertIn("01:00:00,000 --> 01:00:01,200", srt)


if __name__ == "__main__":
    unittest.main()
