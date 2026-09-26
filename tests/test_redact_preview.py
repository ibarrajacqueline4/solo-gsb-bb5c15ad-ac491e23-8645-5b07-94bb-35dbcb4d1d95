"""敏感短语遮罩预览核心逻辑单元测试 + 独立朴素实现对拍（纯标准库）。

朴素实现 naive_redact 与核心算法相互独立：逐起点 startswith 扫描候选、
用被占用字符集合判定重叠、逐词行走映射命中区间，与 redact_preview 对比。
另对随机输入做性质校验：命中互不重叠、每个落选候选都与更高优先级的
已选命中重叠、遮罩文本与命中列表自洽；并对高重复输入（长串同一字符 +
多条嵌套短语）做功能与内存回归。
"""

import random
import tracemalloc
import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.redact_preview import REDACT_CHAR, redact_preview


def W(start, end, text, speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def naive_redact(words, phrases):
    """独立实现：返回 (redacted_words, hits)，结构与 redact_preview 相同。"""
    n = len(words)
    redact = [set() for _ in range(n)]  # 每个词内需遮罩的字符下标
    hits = []
    run_start = 0
    i = 1
    while i <= n:
        if i < n and words[i].speaker == words[i - 1].speaker:
            i += 1
            continue
        run_end = i
        run_text = "".join(w.text for w in words[run_start:run_end])
        offsets = []
        acc = 0
        for k in range(run_start, run_end):
            offsets.append(acc)
            acc += len(words[k].text)
        candidates = []
        for p_idx, phrase in enumerate(phrases):
            size = len(phrase)
            for pos in range(0, len(run_text) - size + 1):
                if run_text.startswith(phrase, pos):
                    candidates.append((pos, size, p_idx))
        candidates.sort(key=lambda c: (c[0], -c[1], c[2]))
        occupied = set()  # 已选命中占用的区间字符位
        for pos, size, p_idx in candidates:
            if any(p in occupied for p in range(pos, pos + size)):
                continue
            occupied.update(range(pos, pos + size))
            first_word = last_word = start_char = end_char = None
            for k in range(run_start, run_end):
                w_start = offsets[k - run_start]
                w_end = w_start + len(words[k].text)
                lo, hi = max(pos, w_start), min(pos + size, w_end)
                if lo >= hi:
                    continue
                redact[k].update(range(lo - w_start, hi - w_start))
                if first_word is None:
                    first_word, start_char = k, lo - w_start
                last_word, end_char = k, hi - w_start
            hits.append(
                {
                    "index": len(hits),
                    "phrase": phrases[p_idx],
                    "start_word": first_word,
                    "end_word": last_word,
                    "start_char": start_char,
                    "end_char": end_char,
                }
            )
        run_start = run_end
        i += 1
    redacted = [
        {
            "start_ms": w.start_ms,
            "end_ms": w.end_ms,
            "text": "".join(
                REDACT_CHAR if c in redact[k] else ch for c, ch in enumerate(w.text)
            ),
            "speaker": w.speaker,
        }
        for k, w in enumerate(words)
    ]
    return redacted, hits


class BasicRedactTests(unittest.TestCase):
    def setUp(self):
        # README 示例：跨词命中 + 整词命中，另一说话人不受影响
        self.words = [
            W(0, 800, "记得使用", "A"),
            W(800, 1400, "优惠码", "A"),
            W(1400, 2000, "八八八", "A"),
            W(2200, 3400, "别错过", "B"),
        ]

    def test_readme_example(self):
        redacted, hits = redact_preview(self.words, ["使用优惠码", "八八八"])
        self.assertEqual(
            [w["text"] for w in redacted],
            ["记得██", "███", "███", "别错过"],
        )
        self.assertEqual(
            hits,
            [
                {
                    "index": 0,
                    "phrase": "使用优惠码",
                    "start_word": 0,
                    "end_word": 1,
                    "start_char": 2,
                    "end_char": 3,
                },
                {
                    "index": 1,
                    "phrase": "八八八",
                    "start_word": 2,
                    "end_word": 2,
                    "start_char": 0,
                    "end_char": 3,
                },
            ],
        )
        # 词序、时间、说话人保持不变
        self.assertEqual(
            [(w["start_ms"], w["end_ms"], w["speaker"]) for w in redacted],
            [(0, 800, "A"), (800, 1400, "A"), (1400, 2000, "A"), (2200, 3400, "B")],
        )

    def test_single_word_hit(self):
        redacted, hits = redact_preview(
            [W(0, 500, "大家好"), W(500, 1000, "欢迎")], ["大家"]
        )
        self.assertEqual(redacted[0]["text"], "██好")
        self.assertEqual(redacted[1]["text"], "欢迎")
        self.assertEqual(
            hits,
            [
                {
                    "index": 0,
                    "phrase": "大家",
                    "start_word": 0,
                    "end_word": 0,
                    "start_char": 0,
                    "end_char": 2,
                }
            ],
        )

    def test_hit_cannot_cross_speaker(self):
        words = [W(0, 500, "动态", "A"), W(500, 1000, "规划", "B")]
        redacted, hits = redact_preview(words, ["动态规划"])
        self.assertEqual(hits, [])
        self.assertEqual([w["text"] for w in redacted], ["动态", "规划"])

    def test_no_hit_returns_original_words(self):
        redacted, hits = redact_preview(self.words, ["不存在的短语"])
        self.assertEqual(hits, [])
        self.assertEqual(
            redacted,
            [
                {"start_ms": w.start_ms, "end_ms": w.end_ms, "text": w.text,
                 "speaker": w.speaker}
                for w in self.words
            ],
        )

    def test_middle_word_fully_covered(self):
        words = [W(0, 100, "ab"), W(100, 200, "cd"), W(200, 300, "ef")]
        redacted, hits = redact_preview(words, ["bcde"])
        self.assertEqual([w["text"] for w in redacted], ["a█", "██", "█f"])
        self.assertEqual(
            hits[0],
            {
                "index": 0,
                "phrase": "bcde",
                "start_word": 0,
                "end_word": 2,
                "start_char": 1,
                "end_char": 1,
            },
        )

    def test_redact_char_per_character(self):
        # 逐字替换：3 字符短语 -> 恰好 3 个 █
        redacted, _ = redact_preview([W(0, 100, "xabcy")], ["abc"])
        self.assertEqual(redacted[0]["text"], "x███y")

    def test_emoji_counts_as_one_char(self):
        # 按 Unicode code point 计偏移：😀 算 1 个字符
        redacted, hits = redact_preview([W(0, 100, "a😀b😀c")], ["😀b"])
        self.assertEqual(redacted[0]["text"], "a██😀c")
        self.assertEqual((hits[0]["start_char"], hits[0]["end_char"]), (1, 3))


class OverlapResolutionTests(unittest.TestCase):
    def test_overlapping_occurrences_same_phrase(self):
        # "aa" 在 "aaaaa" 中有 4 条候选，贪心选出 [0,2) 与 [2,4)
        redacted, hits = redact_preview([W(0, 100, "aaaaa")], ["aa"])
        self.assertEqual(redacted[0]["text"], "████a")
        self.assertEqual(len(hits), 2)
        self.assertEqual(
            [(h["start_char"], h["end_char"]) for h in hits], [(0, 2), (2, 4)]
        )

    def test_same_start_longer_phrase_wins(self):
        redacted, hits = redact_preview([W(0, 100, "abcdef")], ["ab", "abcd"])
        self.assertEqual(redacted[0]["text"], "████ef")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["phrase"], "abcd")

    def test_earlier_start_wins_then_next_free(self):
        # 候选 [0,3) [1,4) [3,5)：起点最靠前的 [0,3) 优先，[1,4) 被挤掉，
        # [3,5) 与已选不重叠仍被选中
        redacted, hits = redact_preview([W(0, 100, "abcde")], ["abc", "bcd", "de"])
        self.assertEqual(redacted[0]["text"], "█████")
        self.assertEqual([h["phrase"] for h in hits], ["abc", "de"])

    def test_longer_same_start_can_block_later_hit(self):
        # 同起点取更长者：[0,5) 挤掉 [3,6)；若取 [0,3) 则 [3,6) 本可共存
        redacted, hits = redact_preview([W(0, 100, "abcdef")], ["abc", "abcde", "def"])
        self.assertEqual([h["phrase"] for h in hits], ["abcde"])
        self.assertEqual(redacted[0]["text"], "█████f")

    def test_duplicate_phrases_single_hit(self):
        # 重复短语产生相同候选，只选中一次
        _, hits = redact_preview([W(0, 100, "xab")], ["ab", "ab"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["phrase"], "ab")

    def test_hits_ordered_across_runs(self):
        words = [
            W(0, 100, "甲秘密", "A"),
            W(200, 300, "乙", "B"),
            W(300, 400, "丙秘密", "C"),
        ]
        _, hits = redact_preview(words, ["秘密"])
        self.assertEqual([h["index"] for h in hits], [0, 1])
        self.assertEqual(
            [(h["start_word"], h["start_char"]) for h in hits], [(0, 1), (2, 1)]
        )


class HighRepeatRegressionTests(unittest.TestCase):
    """高重复输入回归：长串重复字符配多条嵌套短语（"a"/"aa"/…）。

    同一说话人长文本全为同一字符时，每条嵌套短语几乎在每个字符起点都有
    命中，重叠候选数 ≈ 短语数 × 文本长度；旧实现把全部候选存入列表再排序，
    额外内存随之相乘放大。实现必须按字符起点只保留优先候选，额外工作内存
    随文本长度（与短语总长度）线性增长，不随全部重叠命中数增长。
    """

    def test_nested_phrases_longer_wins_at_same_start(self):
        # "aaaaa" 配嵌套短语：起点 0 处最长的 "aaa" 优先，余下 "aa" 恰好接上
        redacted, hits = redact_preview([W(0, 100, "aaaaa")], ["a", "aa", "aaa"])
        self.assertEqual(redacted[0]["text"], "█████")
        self.assertEqual(
            [(h["phrase"], h["start_char"], h["end_char"]) for h in hits],
            [("aaa", 0, 3), ("aa", 3, 5)],
        )

    def test_nested_phrases_tail_too_short_left_untouched(self):
        # 尾部不足任何短语长度的字符不被遮罩
        redacted, hits = redact_preview([W(0, 100, "aaaa")], ["aa", "aaa"])
        self.assertEqual(redacted[0]["text"], "███a")
        self.assertEqual([h["phrase"] for h in hits], ["aaa"])

    def test_nested_phrases_cross_word_not_speaker(self):
        # 说话人 A 连续区间内长短语可跨词命中；换说话人后从头重新匹配
        words = [
            W(0, 100, "aa", "A"),
            W(100, 200, "aa", "A"),
            W(200, 300, "ab", "B"),
        ]
        redacted, hits = redact_preview(words, ["a", "aaa"])
        self.assertEqual([w["text"] for w in redacted], ["██", "██", "█b"])
        self.assertEqual(
            [
                (
                    h["phrase"],
                    h["start_word"],
                    h["end_word"],
                    h["start_char"],
                    h["end_char"],
                )
                for h in hits
            ],
            [
                ("aaa", 0, 1, 0, 1),  # 跨词命中：覆盖词 0 全部 + 词 1 首字符
                ("a", 1, 1, 1, 2),
                ("a", 2, 2, 0, 1),  # 说话人 B 的区间独立匹配
            ],
        )

    def test_high_repeat_extra_memory_bounded(self):
        # 2000 字符全 "a"（20 词 × 100 字符）+ 50 条嵌套短语：
        # 重叠候选约 10 万条，旧“全量候选列表 + 排序”实现同输入峰值约 20MB；
        # 按字符起点保留优先候选后，额外工作内存应远低于 4MB（与候选数无关）。
        words = [
            W(i * 100, i * 100 + 100, "a" * 100, "A") for i in range(20)
        ]
        phrases = ["a" * k for k in range(1, 51)]

        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            redacted, hits = redact_preview(words, phrases)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # 最长短语（50 字符）铺满：2000 / 50 = 40 条命中，全部字符被遮罩
        self.assertEqual(len(hits), 40)
        self.assertTrue(all(h["phrase"] == phrases[-1] for h in hits))
        self.assertEqual(
            (
                hits[0]["start_word"],
                hits[0]["end_word"],
                hits[0]["start_char"],
                hits[0]["end_char"],
            ),
            (0, 0, 0, 50),
        )
        self.assertEqual(
            (
                hits[-1]["start_word"],
                hits[-1]["end_word"],
                hits[-1]["start_char"],
                hits[-1]["end_char"],
            ),
            (19, 19, 50, 100),
        )
        self.assertTrue(all(w["text"] == REDACT_CHAR * 100 for w in redacted))
        self.assertLess(peak, 4 * 1024 * 1024)


class ValidationReuseTests(unittest.TestCase):
    def test_empty_words_reused(self):
        with self.assertRaises(DomainError) as ctx:
            redact_preview([], ["甲"])
        self.assertEqual(ctx.exception.code, "empty_words")

    def test_time_out_of_order_reused(self):
        with self.assertRaises(DomainError) as ctx:
            redact_preview([W(1000, 2000, "甲"), W(900, 1500, "乙")], ["甲"])
        self.assertEqual(ctx.exception.code, "time_out_of_order")

    def test_overlap_reused(self):
        with self.assertRaises(DomainError) as ctx:
            redact_preview([W(0, 2000, "甲"), W(1500, 3000, "乙")], ["甲"])
        self.assertEqual(ctx.exception.code, "overlapping_time_range")

    def test_invalid_time_range_reused(self):
        with self.assertRaises(DomainError) as ctx:
            redact_preview([W(2000, 1000, "甲")], ["甲"])
        self.assertEqual(ctx.exception.code, "invalid_time_range")


class DifferentialTests(unittest.TestCase):
    def test_matches_naive_and_invariants(self):
        random.seed(20260925)
        phrase_pool = ["甲", "乙", "甲乙", "乙甲", "甲乙丙", "😀", "a😀"]
        text_pool = ["甲", "乙", "丙", "甲乙", "😀", "a", "b😀"]
        speakers = ["A", "A", "B"]
        hit_trials = 0
        for trial in range(1500):
            n = random.randint(1, 12)
            words = []
            t = 0
            for _ in range(n):
                t += random.choice([0, 0, 100, 400])
                dur = random.choice([100, 300, 600])
                words.append(
                    W(t, t + dur, random.choice(text_pool), random.choice(speakers))
                )
                t += dur
            phrases = random.sample(phrase_pool, random.randint(1, len(phrase_pool)))

            redacted, hits = redact_preview(words, phrases)
            exp_redacted, exp_hits = naive_redact(words, phrases)
            self.assertEqual(hits, exp_hits, f"trial {trial} hits mismatch")
            self.assertEqual(redacted, exp_redacted, f"trial {trial} words mismatch")

            # ---- 不变量校验 ----
            # 词数、词序、时间、说话人不变；text 长度不变（逐字替换）
            self.assertEqual(len(redacted), n)
            for k, w in enumerate(words):
                self.assertEqual(redacted[k]["start_ms"], w.start_ms)
                self.assertEqual(redacted[k]["end_ms"], w.end_ms)
                self.assertEqual(redacted[k]["speaker"], w.speaker)
                self.assertEqual(len(redacted[k]["text"]), len(w.text))
            if not hits:
                continue
            hit_trials += 1
            # 命中按位置升序、互不重叠（换算为全量拼接文本的字符区间）
            global_offsets = [0] * n
            for k in range(1, n):
                global_offsets[k] = global_offsets[k - 1] + len(words[k - 1].text)
            intervals = []
            for h in hits:
                begin = global_offsets[h["start_word"]] + h["start_char"]
                end = global_offsets[h["end_word"]] + h["end_char"]
                self.assertEqual(end - begin, len(h["phrase"]))
                self.assertIn(h["phrase"], phrases)
                intervals.append((begin, end))
            self.assertEqual(intervals, sorted(intervals))
            for (a_end, b_begin) in zip(
                [e for _, e in intervals], [b for b, _ in intervals[1:]]
            ):
                self.assertLessEqual(a_end, b_begin)
            # 遮罩文本与命中列表自洽：命中区间内全为 █，区间外无 █
            for k in range(n):
                covered = set()
                for h in hits:
                    if h["start_word"] <= k <= h["end_word"]:
                        lo = h["start_char"] if k == h["start_word"] else 0
                        hi = (
                            h["end_char"]
                            if k == h["end_word"]
                            else len(words[k].text)
                        )
                        covered.update(range(lo, hi))
                for c, ch in enumerate(redacted[k]["text"]):
                    self.assertEqual(ch == REDACT_CHAR, c in covered)
        self.assertGreater(hit_trials, 300)  # 确保有足够多的含命中对拍样本


if __name__ == "__main__":
    unittest.main()
