"""重复口播片段检测核心逻辑单元测试 + 暴力枚举对拍（纯标准库）。

暴力枚举全部起点对 (i, j)（i < j），直接计算 lcp 与可用长度
usable = min(lcp, j-i)，校验首词时间间隔，按 (词数降序, 起点对字典序升序)
取最优，与 find_duplicate_passages 对比。
"""

import random
import unittest
from types import SimpleNamespace

from app.duplicate_passages import find_duplicate_passages
from app.errors import DomainError


def W(start, end, text, speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def brute_force(words, min_words, min_gap_ms):
    """独立实现：枚举全部起点对，返回期望的 match dict 或 None。"""
    n = len(words)
    tokens = [(w.text, w.speaker) for w in words]
    best = None  # (-usable, i, j) 取最小即 (词数最多, 起点对字典序最小)
    for i in range(n):
        for j in range(i + 1, n):
            if words[j].start_ms - words[i].start_ms < min_gap_ms:
                continue
            lcp = 0
            while j + lcp < n and tokens[i + lcp] == tokens[j + lcp]:
                lcp += 1
            usable = min(lcp, j - i)
            if usable == 0:
                continue
            key = (-usable, i, j)
            if best is None or key < best:
                best = key
    if best is None or -best[0] < min_words:
        return None
    _, i, j = best
    length = -best[0]
    return {
        "word_count": length,
        "text": "".join(w.text for w in words[i : i + length]),
        "first": {
            "start_word": i,
            "end_word": i + length - 1,
            "start_ms": words[i].start_ms,
            "end_ms": words[i + length - 1].end_ms,
        },
        "second": {
            "start_word": j,
            "end_word": j + length - 1,
            "start_ms": words[j].start_ms,
            "end_ms": words[j + length - 1].end_ms,
        },
    }


class BasicMatchTests(unittest.TestCase):
    def setUp(self):
        # README 示例：词 0～2 与词 4～6 完全相同（开场白重复）
        self.words = [
            W(0, 800, "欢迎收听", "A"),
            W(800, 1600, "小宇宙", "A"),
            W(1600, 2400, "播客", "A"),
            W(2400, 4000, "今天聊动态规划。", "B"),
            W(5000, 5800, "欢迎收听", "A"),
            W(5800, 6600, "小宇宙", "A"),
            W(6600, 7400, "播客", "A"),
        ]

    def test_basic_match(self):
        match = find_duplicate_passages(self.words, 2, 1000)
        self.assertEqual(
            match,
            {
                "word_count": 3,
                "text": "欢迎收听小宇宙播客",
                "first": {"start_word": 0, "end_word": 2, "start_ms": 0, "end_ms": 2400},
                "second": {"start_word": 4, "end_word": 6, "start_ms": 5000, "end_ms": 7400},
            },
        )

    def test_match_null_when_below_min_words(self):
        # 最长重复为 3 词，min_words=4 时无解
        self.assertIsNone(find_duplicate_passages(self.words, 4, 1000))

    def test_min_gap_filters_close_pair(self):
        # 两处首词相隔 5000ms，阈值 6000 时无解
        self.assertIsNone(find_duplicate_passages(self.words, 2, 6000))
        # 阈值恰好 5000 时可行（至少相隔 min_gap_ms，含边界）
        self.assertIsNotNone(find_duplicate_passages(self.words, 2, 5000))

    def test_speaker_must_match(self):
        # 文本相同但说话人不同，符号不相等，无重复
        words = [
            W(0, 500, "欢迎", "A"),
            W(500, 1000, "收听", "A"),
            W(2000, 2500, "欢迎", "B"),
            W(2500, 3000, "收听", "B"),
        ]
        self.assertIsNone(find_duplicate_passages(words, 2, 0))

    def test_word_text_not_split(self):
        # 整词为最小单位："甲乙丙" 与 "甲乙" 不是相同符号
        words = [
            W(0, 500, "甲乙丙", "A"),
            W(1000, 1500, "甲乙", "A"),
            W(2000, 2500, "丙", "A"),
        ]
        self.assertIsNone(find_duplicate_passages(words, 2, 0))

    def test_no_repeat_at_all(self):
        words = [W(i * 500, i * 500 + 400, f"词{i}", "A") for i in range(6)]
        self.assertIsNone(find_duplicate_passages(words, 2, 0))


class SelectionTests(unittest.TestCase):
    def test_longest_wins(self):
        # "甲乙丙"（3 词）与 "丁"（1 词）均重复，取词数最多者
        words = [
            W(0, 100, "甲"), W(100, 200, "乙"), W(200, 300, "丙"),
            W(300, 400, "丁"),
            W(1000, 1100, "丁"),
            W(2000, 2100, "甲"), W(2100, 2200, "乙"), W(2200, 2300, "丙"),
        ]
        match = find_duplicate_passages(words, 2, 0)
        self.assertEqual(match["word_count"], 3)
        self.assertEqual(match["text"], "甲乙丙")
        self.assertEqual(match["first"]["start_word"], 0)
        self.assertEqual(match["second"]["start_word"], 5)

    def test_tie_break_start_index_pair(self):
        # "甲乙" 在 (0,3)、(0,6)、(3,6) 三处两两重复，同长度取 (0,3)
        words = [
            W(0, 100, "甲"), W(100, 200, "乙"), W(200, 300, "丙"),
            W(300, 400, "甲"), W(400, 500, "乙"), W(500, 600, "丁"),
            W(600, 700, "甲"), W(700, 800, "乙"),
        ]
        match = find_duplicate_passages(words, 2, 0)
        self.assertEqual(match["word_count"], 2)
        self.assertEqual(
            (match["first"]["start_word"], match["second"]["start_word"]), (0, 3)
        )
        self.assertEqual((match["first"]["end_word"], match["second"]["end_word"]), (1, 4))

    def test_tie_break_skips_gap_violating_pair(self):
        # "甲乙" 出现于词 0/2/4（时刻 0/100/5000）；min_gap=1000 时
        # (0,2) 间隔不足被排除，同长度可行对中取字典序最小的 (0,4)
        words = [
            W(0, 50, "甲"), W(50, 100, "乙"),
            W(100, 150, "甲"), W(150, 200, "乙"),
            W(5000, 5050, "甲"), W(5050, 5100, "乙"),
        ]
        match = find_duplicate_passages(words, 2, 1000)
        self.assertEqual(
            (match["first"]["start_word"], match["second"]["start_word"]), (0, 4)
        )

    def test_non_overlapping_intervals(self):
        # 5 个相同词：重叠的更长匹配不可用，最优为 [0,1] 与 [2,3]
        words = [W(i * 100, i * 100 + 50, "甲") for i in range(5)]
        match = find_duplicate_passages(words, 2, 0)
        self.assertEqual(match["word_count"], 2)
        self.assertEqual(
            (match["first"]["start_word"], match["first"]["end_word"]), (0, 1)
        )
        self.assertEqual(
            (match["second"]["start_word"], match["second"]["end_word"]), (2, 3)
        )

    def test_adjacent_passages_allowed(self):
        # 端点相接不算重叠：[0,2] 与 [3,5] 恰好相邻，整体为周期重复
        words = [
            W(0, 100, "甲"), W(100, 200, "乙"), W(200, 300, "丙"),
            W(300, 400, "甲"), W(400, 500, "乙"), W(500, 600, "丙"),
        ]
        match = find_duplicate_passages(words, 2, 0)
        self.assertEqual(match["word_count"], 3)
        self.assertEqual(
            (match["first"]["start_word"], match["second"]["start_word"]), (0, 3)
        )

    def test_min_gap_zero_allows_same_start_time(self):
        # 端点相接的词时间相同也允许（min_gap_ms=0）
        words = [
            W(0, 500, "甲"), W(500, 1000, "乙"),
            W(1000, 1500, "甲"), W(1500, 2000, "乙"),
        ]
        match = find_duplicate_passages(words, 2, 0)
        self.assertEqual(match["word_count"], 2)


class ValidationReuseTests(unittest.TestCase):
    def test_empty_words_reused(self):
        with self.assertRaises(DomainError) as ctx:
            find_duplicate_passages([], 2, 0)
        self.assertEqual(ctx.exception.code, "empty_words")

    def test_time_out_of_order_reused(self):
        with self.assertRaises(DomainError) as ctx:
            find_duplicate_passages(
                [W(1000, 2000, "甲"), W(900, 1500, "乙")], 2, 0
            )
        self.assertEqual(ctx.exception.code, "time_out_of_order")

    def test_overlap_reused(self):
        with self.assertRaises(DomainError) as ctx:
            find_duplicate_passages(
                [W(0, 2000, "甲"), W(1500, 3000, "乙")], 2, 0
            )
        self.assertEqual(ctx.exception.code, "overlapping_time_range")

    def test_invalid_time_range_reused(self):
        with self.assertRaises(DomainError) as ctx:
            find_duplicate_passages([W(2000, 1000, "甲")], 2, 0)
        self.assertEqual(ctx.exception.code, "invalid_time_range")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260925)
        texts = ["甲", "乙", "丙"]
        speakers = ["A", "A", "B"]
        feasible = 0
        for trial in range(1500):
            n = random.randint(1, 16)
            words = []
            t = 0
            for _ in range(n):
                t += random.choice([0, 0, 100, 400])
                dur = random.choice([100, 300, 600])
                words.append(
                    W(t, t + dur, random.choice(texts), random.choice(speakers))
                )
                t += dur
            min_words = random.randint(2, 4)
            min_gap_ms = random.choice([0, 0, 0, 50, 300, 2000])

            expected = brute_force(words, min_words, min_gap_ms)
            match = find_duplicate_passages(words, min_words, min_gap_ms)
            self.assertEqual(match, expected, f"trial {trial} mismatch")
            if match is not None:
                feasible += 1
                # 额外自洽校验：区间不重叠、词数达标、首词间隔达标、序列相同
                self.assertGreaterEqual(match["word_count"], min_words)
                self.assertLess(match["first"]["end_word"], match["second"]["start_word"])
                self.assertGreaterEqual(
                    match["second"]["start_ms"] - match["first"]["start_ms"],
                    min_gap_ms,
                )
                length = match["word_count"]
                a, b = match["first"]["start_word"], match["second"]["start_word"]
                self.assertEqual(
                    [(w.text, w.speaker) for w in words[a : a + length]],
                    [(w.text, w.speaker) for w in words[b : b + length]],
                )
        self.assertGreater(feasible, 150)  # 确保有足够多的可行对拍样本


class LargeScaleTests(unittest.TestCase):
    """规模上限（20000 词）下的确定性回归：索引算法须在可接受时间内给出精确解。"""

    def test_20000_words_all_same_token(self):
        # 全部相同符号：最优为前后各半的 (0, n//2)，词数 n//2
        n = 20000
        words = [W(i * 500, i * 500 + 400, "字") for i in range(n)]
        match = find_duplicate_passages(words, 2, 0)
        self.assertEqual(match["word_count"], n // 2)
        self.assertEqual(
            (match["first"]["start_word"], match["second"]["start_word"]),
            (0, n // 2),
        )
        self.assertEqual(match["text"], "字" * (n // 2))

    def test_20000_words_planted_block(self):
        # 唯一重复为词 100～899 与词 15100～15899 的 800 词块，其余符号均唯一
        n = 20000
        tokens = [f"词{k}" for k in range(n)]
        for k in range(800):
            tokens[15100 + k] = tokens[100 + k]
        words = [W(i * 500, i * 500 + 400, tokens[i]) for i in range(n)]
        match = find_duplicate_passages(words, 2, 1000)
        self.assertEqual(match["word_count"], 800)
        self.assertEqual(
            (match["first"]["start_word"], match["second"]["start_word"]), (100, 15100)
        )
        self.assertEqual(
            (match["first"]["end_word"], match["second"]["end_word"]), (899, 15899)
        )


if __name__ == "__main__":
    unittest.main()
