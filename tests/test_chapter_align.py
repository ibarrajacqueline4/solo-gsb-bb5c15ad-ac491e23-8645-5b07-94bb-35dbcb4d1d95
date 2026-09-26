"""章节时刻对齐核心逻辑单元测试 + 暴力枚举对拍（纯标准库）。

暴力枚举每章全部同说话人连续词内的 cue 命中（允许跨词、允许重叠），
枚举各章候选的笛卡尔组合并校验相邻时刻间隔，按
(总绝对偏差, 命中起始全局字符位序列) 取最优，与 align_chapters 对比。
"""

import itertools
import random
import unittest
from types import SimpleNamespace

from app.chapter_align import align_chapters
from app.errors import DomainError


def W(start, end, text, speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def C(title, cue, approx):
    return SimpleNamespace(title=title, cue=cue, approx_ms=approx)


def brute_force(words, chapters, min_gap_ms):
    """独立实现候选枚举与组合搜索，返回 (results, total) 或 None。"""
    n = len(words)
    counts = [len(w.text) for w in words]
    global_offsets = [0] * n
    for i in range(1, n):
        global_offsets[i] = global_offsets[i - 1] + counts[i - 1]

    runs = []
    start = 0
    for i in range(1, n + 1):
        if i == n or words[i].speaker != words[i - 1].speaker:
            runs.append((start, i))
            start = i

    all_cands = []
    for ch in chapters:
        cands = []
        for r_start, r_end in runs:
            pieces = [words[k].text for k in range(r_start, r_end)]
            run_text = "".join(pieces)
            offsets = [0]
            for piece in pieces[:-1]:
                offsets.append(offsets[-1] + len(piece))
            pos = 0
            while True:
                hit = run_text.find(ch.cue, pos)
                if hit < 0:
                    break
                k = 0
                while k + 1 < len(offsets) and offsets[k + 1] <= hit:
                    k += 1
                word_index = r_start + k
                char_start = hit - offsets[k]
                gpos = global_offsets[word_index] + char_start
                cands.append(
                    (words[word_index].start_ms, word_index, char_start, gpos)
                )
                pos = hit + 1
        if not cands:
            return None
        all_cands.append(cands)

    best = None  # (总偏差, 全局字符位元组, 候选元组)
    for combo in itertools.product(*[range(len(c)) for c in all_cands]):
        times = [all_cands[j][p][0] for j, p in enumerate(combo)]
        if any(b - a < min_gap_ms for a, b in zip(times, times[1:])):
            continue
        total = sum(
            abs(all_cands[j][p][0] - chapters[j].approx_ms)
            for j, p in enumerate(combo)
        )
        positions = tuple(all_cands[j][p][3] for j, p in enumerate(combo))
        key = (total, positions)
        if best is None or key < best[:2]:
            best = (total, positions, combo)
    if best is None:
        return None

    total, _, combo = best
    results = []
    for j, p in enumerate(combo):
        time, word_index, char_start, _ = all_cands[j][p]
        char_end = min(char_start + len(chapters[j].cue), counts[word_index])
        results.append(
            {
                "index": j,
                "title": chapters[j].title,
                "time_ms": time,
                "word_index": word_index,
                "char_start": char_start,
                "char_end": char_end,
            }
        )
    return results, total


class BasicAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.words = [
            W(0, 500, "大家好", "A"),
            W(600, 1200, "今天聊", "A"),
            W(1300, 2000, "动态规划", "A"),
            W(2200, 2800, "明天见", "B"),
        ]

    def test_basic_alignment(self):
        chapters = [C("开场", "大家", 0), C("主题", "动态", 1000)]
        results, total = align_chapters(self.words, chapters, 500)
        self.assertEqual(
            results,
            [
                {
                    "index": 0,
                    "title": "开场",
                    "time_ms": 0,
                    "word_index": 0,
                    "char_start": 0,
                    "char_end": 2,
                },
                {
                    "index": 1,
                    "title": "主题",
                    "time_ms": 1300,
                    "word_index": 2,
                    "char_start": 0,
                    "char_end": 2,
                },
            ],
        )
        self.assertEqual(total, 300)  # |0-0| + |1300-1000|

    def test_candidate_time_uses_first_char_word(self):
        # cue 横跨词 0、1；候选时刻取词 0 的 start_ms
        words = [W(0, 500, "动态", "A"), W(600, 1200, "规划", "A"),
                 W(1300, 1800, "动态规划", "A")]
        results, total = align_chapters(words, [C("x", "动态规划", 0)], 1)
        self.assertEqual(results[0]["time_ms"], 0)
        self.assertEqual(results[0]["word_index"], 0)
        self.assertEqual(results[0]["char_start"], 0)
        # 跨词命中：词内区间截断到词 0 词末
        self.assertEqual(results[0]["char_end"], 2)
        self.assertEqual(total, 0)

    def test_char_end_covers_full_cue_when_within_one_word(self):
        words = [W(100, 500, "今天聊聊动态规划", "A")]
        results, _ = align_chapters(words, [C("x", "聊聊", 0)], 1)
        self.assertEqual(results[0]["word_index"], 0)
        self.assertEqual(results[0]["char_start"], 2)
        self.assertEqual(results[0]["char_end"], 4)

    def test_overlapping_hits_all_enumerated(self):
        # cue "aa" 在 "aaa" 中有两个重叠命中：偏移 0 与 1，候选时刻相同，
        # 同分裁决取全局字符位更小者（偏移 0）
        words = [W(0, 100, "aaa", "A"), W(200, 300, "aaab", "A")]
        results, _ = align_chapters(words, [C("c", "aa", 50)], 1)
        self.assertEqual(results[0]["word_index"], 0)
        self.assertEqual(results[0]["char_start"], 0)
        self.assertEqual(results[0]["time_ms"], 0)

    def test_unicode_code_points(self):
        # emoji 与中文字符均按 1 个 code point 计
        words = [W(0, 500, "嗨😀好", "A"), W(600, 900, "😀再见", "A")]
        results, _ = align_chapters(words, [C("c", "😀", 300)], 1)
        # 两个命中（词 0 偏移 1、词 1 偏移 0），距 300 偏差分别为 300/300，取位小者
        self.assertEqual(results[0]["word_index"], 0)
        self.assertEqual(results[0]["char_start"], 1)
        self.assertEqual(results[0]["char_end"], 2)

    def test_speaker_boundary_blocks_hit(self):
        # "bc" 横跨 A/B 边界，不构成命中
        words = [W(0, 100, "ab", "A"), W(100, 200, "cd", "B"),
                 W(200, 300, "ef", "A")]
        with self.assertRaises(DomainError) as ctx:
            align_chapters(words, [C("c", "bc", 0)], 1)
        self.assertEqual(ctx.exception.code, "infeasible_chapter_alignment")
        self.assertEqual(ctx.exception.extra["chapter_index"], 0)

    def test_hit_within_run_spanning_words(self):
        # 同一说话人连续两词，命中跨词合法
        words = [W(0, 100, "甲乙", "A"), W(100, 200, "丙丁", "A")]
        results, _ = align_chapters(words, [C("c", "乙丙", 50)], 1)
        self.assertEqual(results[0]["word_index"], 0)
        self.assertEqual(results[0]["char_start"], 1)
        self.assertEqual(results[0]["char_end"], 2)


class SelectionTests(unittest.TestCase):
    def test_gap_forces_later_candidate(self):
        # 词 0(t=0) 与词 1(t=1000) 均含 cue；两章 approx 都偏向词 1，
        # 但间隔 600 迫使首章取词 0
        words = [W(0, 200, "甲", "A"), W(1000, 1200, "甲", "A")]
        chapters = [C("a", "甲", 1000), C("b", "甲", 1000)]
        results, total = align_chapters(words, chapters, 600)
        self.assertEqual([r["time_ms"] for r in results], [0, 1000])
        self.assertEqual(total, 1000)  # |0-1000| + 0

    def test_infeasible_gap(self):
        words = [W(0, 100, "甲", "A"), W(200, 300, "乙", "A")]
        with self.assertRaises(DomainError) as ctx:
            align_chapters(words, [C("a", "甲", 0), C("b", "乙", 0)], 1000)
        self.assertEqual(ctx.exception.code, "infeasible_chapter_alignment")
        self.assertEqual(ctx.exception.extra["chapter_count"], 2)
        self.assertEqual(ctx.exception.extra["min_gap_ms"], 1000)

    def test_greedy_backtracks_to_feasible_candidate(self):
        # 首章 cue 命中词 0(t=0) 与词 2(t=400)；若只看偏差会取 400（偏差 0），
        # 但它无法与次章唯一命中 t=100 组成间隔 >=50 的方案，必须回退取 t=0。
        words = [W(0, 100, "甲", "A"), W(100, 200, "乙", "B"),
                 W(400, 500, "甲", "A")]
        chapters = [C("a", "甲", 400), C("b", "乙", 100)]
        results, total = align_chapters(words, chapters, 50)
        self.assertEqual([r["time_ms"] for r in results], [0, 100])
        self.assertEqual(total, 400)

    def test_tie_break_global_position_tuple(self):
        # 词 0(t=0,"甲甲")、词 1(t=100,"甲")：cue 甲 全局位 0/1(t0)、2(t100)。
        # 两章间隔 100：(章0 位0, 章1 位2) 与 (章0 位1, 章1 位2) 同分，取前者
        words = [W(0, 50, "甲甲", "A"), W(100, 150, "甲", "A")]
        chapters = [C("a", "甲", 0), C("b", "甲", 100)]
        results, total = align_chapters(words, chapters, 100)
        self.assertEqual(total, 0)
        self.assertEqual(
            [(r["word_index"], r["char_start"]) for r in results],
            [(0, 0), (1, 0)],
        )

    def test_three_chapter_chain(self):
        words = [W(i * 100, i * 100 + 50, "字", "A") for i in range(5)]
        chapters = [C(str(i), "字", approx)
                    for i, approx in enumerate((0, 200, 400))]
        results, total = align_chapters(words, chapters, 100)
        times = [r["time_ms"] for r in results]
        self.assertEqual(times, [0, 200, 400])
        self.assertEqual([r["word_index"] for r in results], [0, 2, 4])
        self.assertEqual(total, 0)

    def test_single_chapter_picks_nearest_hit(self):
        # |500-560|=60，|600-560|=40：最近命中为 t=600（词 2）
        words = [W(0, 100, "字", "A"), W(500, 600, "字", "A"),
                 W(600, 700, "字", "A")]
        results, total = align_chapters(words, [C("c", "字", 560)], 1)
        self.assertEqual(results[0]["time_ms"], 600)
        self.assertEqual(results[0]["word_index"], 2)
        self.assertEqual(total, 40)


class ValidationReuseTests(unittest.TestCase):
    def test_time_out_of_order_reused(self):
        with self.assertRaises(DomainError) as ctx:
            align_chapters([W(1000, 2000, "甲", "A"), W(900, 1500, "乙", "A")],
                           [C("a", "甲", 0)], 1)
        self.assertEqual(ctx.exception.code, "time_out_of_order")

    def test_overlap_reused(self):
        with self.assertRaises(DomainError) as ctx:
            align_chapters([W(0, 2000, "甲", "A"), W(1500, 3000, "乙", "A")],
                           [C("a", "甲", 0)], 1)
        self.assertEqual(ctx.exception.code, "overlapping_time_range")

    def test_empty_words_reused(self):
        with self.assertRaises(DomainError) as ctx:
            align_chapters([], [C("a", "甲", 0)], 1)
        self.assertEqual(ctx.exception.code, "empty_words")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260924)
        texts = ["字", "甲", "甲乙", "乙甲", "甲甲", "丙", "乙", "a😀"]
        speakers = ["A", "A", "B", "B"]
        cues = ["甲", "乙", "甲乙", "甲甲", "丙", "字", "乙甲", "丁", "😀"]
        feasible = 0
        for trial in range(600):
            n = random.randint(1, 8)
            words = []
            t = 0
            for i in range(n):
                t += random.choice([0, 0, 100, 400])
                dur = random.choice([100, 300, 600])
                words.append(W(t, t + dur, random.choice(texts), speakers[i % 4]))
                t += dur
            chapter_count = random.choice([1, 1, 2, 3])
            titles = [f"第{k}章" for k in range(chapter_count)]
            chapters = [
                C(titles[k], random.choice(cues), random.randint(0, 3000))
                for k in range(chapter_count)
            ]
            min_gap = random.choice([1, 1, 50, 200])

            expected = brute_force(words, chapters, min_gap)
            if expected is None:
                with self.assertRaises(DomainError) as ctx:
                    align_chapters(words, chapters, min_gap)
                self.assertEqual(ctx.exception.code, "infeasible_chapter_alignment")
            else:
                exp_results, exp_total = expected
                results, total = align_chapters(words, chapters, min_gap)
                self.assertEqual(total, exp_total, f"trial {trial} total mismatch")
                self.assertEqual(results, exp_results, f"trial {trial} result mismatch")
                # 额外自洽校验
                self.assertEqual(len(results), chapter_count)
                for a, b in zip(results, results[1:]):
                    self.assertGreaterEqual(b["time_ms"] - a["time_ms"], min_gap)
                feasible += 1
        self.assertGreater(feasible, 150)  # 确保有足够多的可行对拍样本


if __name__ == "__main__":
    unittest.main()
