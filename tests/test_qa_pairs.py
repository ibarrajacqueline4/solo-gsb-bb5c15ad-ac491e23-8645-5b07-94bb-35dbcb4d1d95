"""访谈问答索引核心逻辑单元测试 + 暴力对拍（纯标准库）。

轮次合并、提问判定（去尾部空白后以 ？/? 结尾）、其他说话人 + 间隔窗口的
候选边、以及 (配对数最大, 回答字符总数最大, (问题首词, 回答首词) 序列字典序
最小) 的三级目标，先用小例子手工校验，再用暴力 DFS 枚举全部保序一对一匹配
在随机输入上对拍。
"""

import random
import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.qa_pairs import build_qa_pairs


def W(start, end, text, speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def pair_keys(pairs):
    return tuple(
        (p["question"]["start_word"], p["answer"]["start_word"]) for p in pairs
    )


def merge_turns(words):
    """测试用：与实现一致的相邻同说话人合并。"""
    turns = []
    run_start = 0
    n = len(words)
    for i in range(1, n + 1):
        if i == n or words[i].speaker != words[run_start].speaker:
            text = "".join(w.text for w in words[run_start:i])
            turns.append(
                dict(
                    start_word=run_start,
                    end_word=i - 1,
                    start_ms=words[run_start].start_ms,
                    end_ms=words[i - 1].end_ms,
                    speaker=words[run_start].speaker,
                    text=text,
                    char_count=len(text),
                )
            )
            run_start = i
    return turns


def is_question(turn):
    stripped = turn["text"].rstrip()
    return bool(stripped) and stripped[-1] in "？?"


def brute_force(words, max_gap_ms):
    """枚举全部保序一对一匹配，返回最优 (配对数, 字符总数, 首词序号对元组)。"""
    turns = merge_turns(words)
    questions = [t for t in turns if is_question(t)]
    answers = [t for t in turns if not is_question(t)]
    p, r = len(questions), len(answers)
    edge = [[False] * r for _ in range(p)]
    for i, q in enumerate(questions):
        for j, a in enumerate(answers):
            gap = a["start_ms"] - q["end_ms"]
            if a["speaker"] != q["speaker"] and 0 <= gap <= max_gap_ms:
                edge[i][j] = True

    best = None

    def rec(i, lo, chars, seq):
        nonlocal best
        if i == p:
            key = (-len(seq), -chars, tuple(seq))
            if best is None or key < best:
                best = key
            return
        rec(i + 1, lo, chars, seq)  # 问题 i 不配对
        for j in range(lo, r):
            if edge[i][j]:
                rec(
                    i + 1,
                    j + 1,
                    chars + answers[j]["char_count"],
                    seq + [(questions[i]["start_word"], answers[j]["start_word"])],
                )

    rec(0, 0, 0, [])
    neg_count, neg_chars, seq = best
    return -neg_count, -neg_chars, seq, questions


class TurnMergingAndBasicTests(unittest.TestCase):
    def test_adjacent_same_speaker_merged(self):
        words = [
            W(0, 500, "你好", "A"),
            W(500, 1000, "吗？", "A"),
            W(1000, 1500, "很好", "B"),
            W(1500, 2000, "谢谢", "B"),
        ]
        pairs, unpaired = build_qa_pairs(words, 1000)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["index"], 0)
        self.assertEqual(
            pair["question"],
            {
                "start_word": 0,
                "end_word": 1,
                "start_ms": 0,
                "end_ms": 1000,
                "speaker": "A",
                "text": "你好吗？",
            },
        )
        self.assertEqual(
            pair["answer"],
            {
                "start_word": 2,
                "end_word": 3,
                "start_ms": 1000,
                "end_ms": 2000,
                "speaker": "B",
                "text": "很好谢谢",
            },
        )
        self.assertEqual(unpaired, [])

    def test_half_width_question_mark_and_trailing_whitespace(self):
        # 半角问号 + 尾部空白（含 tab/换行）也算提问；返回文本保留原样
        for suffix in ("? ", "?\t", "？\n", "?　"):  # 末者含全角空格
            words = [
                W(0, 100, "行吗" + suffix, "A"),
                W(100, 200, "可以", "B"),
            ]
            pairs, unpaired = build_qa_pairs(words, 0)
            self.assertEqual(len(pairs), 1, suffix)
            self.assertEqual(pairs[0]["question"]["text"], "行吗" + suffix)
            self.assertEqual(unpaired, [])

    def test_question_mark_must_be_last_after_stripping(self):
        # 问号后还有非空白文本：合并轮次不再以问号结尾，不是提问
        words = [
            W(0, 100, "好吗？", "A"),
            W(100, 200, "嗯", "A"),
            W(200, 300, "陈述", "B"),
        ]
        pairs, unpaired = build_qa_pairs(words, 1000)
        self.assertEqual(pairs, [])
        self.assertEqual(unpaired, [])

    def test_all_whitespace_text_is_not_question(self):
        words = [W(0, 100, "   ", "A"), W(100, 200, "\t\n", "B")]
        self.assertEqual(build_qa_pairs(words, 100), ([], []))


class CandidateRulesTests(unittest.TestCase):
    def test_answer_must_be_other_speaker(self):
        # 只有一个提问 Q0(A) 与一个回答轮 A0(A)，二者同说话人：时间间隔再小也不配。
        # 用一个 B 的回答垫在中间会让两 A 轮分开，但 B 也会答 Q0；
        # 故这里让两 A 词相邻（合并为单个非提问轮），再无其它说话人——无任何配对。
        words = [
            W(0, 100, "q0？", "A"),
            W(100, 200, "嗯嗯", "A"),  # 与问题合并为一轮，整轮不以？结尾 => 非提问
        ]
        pairs, unpaired = build_qa_pairs(words, 1000)
        self.assertEqual(pairs, [])
        self.assertEqual(unpaired, [])

    def test_other_speaker_answer_is_required(self):
        # Q0(A) 后唯一可能的回答轮也是 A（用一个 B 提问隔成独立轮）。
        # Q0(A) 与 A0(A) 同说话人，不可配；而 B 提问 Q1 与 A0(A) 说话人不同，
        # 反而配成一对——恰好证明同说话人限制只作用于 Q0。
        words = [
            W(0, 100, "q0？", "A"),
            W(100, 200, "垫话？", "B"),  # B 提问（不作回答），隔开两个 A 轮
            W(200, 300, "嗯嗯", "A"),   # 回答轮：与 Q1(B) 不同说话人，答 Q1
        ]
        pairs, unpaired = build_qa_pairs(words, 1000)
        self.assertEqual(pair_keys(pairs), ((1, 2),))
        self.assertEqual(unpaired, [0])  # Q0(A) 因唯一回答同说话人而落单

    def test_gap_window_filters_other_speaker_answer(self):
        words = [
            W(0, 100, "行吗？", "A"),
            W(200, 300, "可以", "B"),  # gap=100
        ]
        self.assertEqual(build_qa_pairs(words, 99), ([], [0]))
        self.assertEqual(pair_keys(build_qa_pairs(words, 100)[0]), ((0, 1),))

    def test_gap_zero_allows_touching_but_not_before(self):
        words = [
            W(0, 100, "行吗？", "A"),
            W(100, 200, "可以", "B"),  # 端点相接 gap=0
        ]
        self.assertEqual(pair_keys(build_qa_pairs(words, 0)[0]), ((0, 1),))

        words_gap = [
            W(0, 100, "行吗？", "A"),
            W(101, 200, "可以", "B"),  # gap=1
        ]
        self.assertEqual(build_qa_pairs(words_gap, 0)[0], [])
        self.assertEqual(build_qa_pairs(words_gap, 0)[1], [0])

    def test_negative_gap_never_matches_even_when_bound_allows(self):
        # 时间上回答早于问题结束（本接口词不重叠，但仍校验 gap>=0 语义）
        words = [
            W(0, 100, "前话", "B"),
            W(100, 200, "为何？", "A"),
        ]
        # B 的轮次在问题之前：start 100 < end 200，gap=-100，即便大窗口也不配
        pairs, unpaired = build_qa_pairs(words, 10_000)
        self.assertEqual(pairs, [])
        self.assertEqual(unpaired, [1])

    def test_no_questions_returns_empty(self):
        words = [W(0, 100, "陈述句", "A"), W(100, 200, "对的", "B")]
        self.assertEqual(build_qa_pairs(words, 100), ([], []))


class OptimizationTests(unittest.TestCase):
    def test_single_question_prefers_longer_answer_text(self):
        words = [
            W(0, 100, "问题？", "A"),
            W(100, 200, "短", "B"),
            W(200, 300, "长长长长", "C"),  # 与 B 不同说话人，分别成轮
        ]
        pairs, _ = build_qa_pairs(words, 1000)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["answer"]["start_word"], 2)
        self.assertEqual(pairs[0]["answer"]["text"], "长长长长")

    def test_max_pair_count_outranks_answer_chars(self):
        # 严格 A/B 交错，每个轮次恰为一个词：
        #   Q0(A,w0,"q0？" [0,100])
        #   A0(B,w1,"短"   [100,200])  短回答，1 字
        #   Q1(A,w2,"q1？" [200,300])
        #   A1(B,w3,"很长很长很长" [300,400])  长回答，6 字
        # 边（说话人都不同，窗口 1000 全放开）：Q0-A0(gap0)、Q0-A1(gap200)、
        #   Q1-A1(gap0)；Q1 不能配更早的 A0（回答须始于问题结束后）。
        # 方案一：Q0->A1，Q1 无答 => 1 对、6 字；
        # 方案二：Q0->A0、Q1->A1 => 2 对、7 字。配对数优先取方案二。
        words = [
            W(0, 100, "q0？", "A"),
            W(100, 200, "短", "B"),
            W(200, 300, "q1？", "A"),
            W(300, 400, "很长很长很长", "B"),
        ]
        pairs, unpaired = build_qa_pairs(words, 1000)
        self.assertEqual(pair_keys(pairs), ((0, 1), (2, 3)))
        self.assertEqual(sum(len(p["answer"]["text"]) for p in pairs), 7)
        self.assertEqual(unpaired, [])

    def test_char_total_breaks_tie_on_pair_count(self):
        # 都只能配 1 对：配 j1（长，4 字）优于 j0（短，1 字），但 j1 说话人需合法。
        # Q0(A)；j0=B 短（gap 0,1字），j1=C 长（gap 0,4字）
        words = [
            W(0, 100, "问题？", "A"),
            W(100, 200, "短", "B"),
            W(200, 300, "长长的答", "C"),
        ]
        pairs, _ = build_qa_pairs(words, 0)
        # gap 窗口：j0 gap=0 合法；j1 start=200，q end=100，gap=100 不在窗口 0
        self.assertEqual(pair_keys(pairs), ((0, 1),))
        pairs, _ = build_qa_pairs(words, 100)
        self.assertEqual(pair_keys(pairs), ((0, 2),))  # 同为 1 对，取字符多者

    def test_lex_tie_prefers_earlier_question_then_earlier_answer(self):
        # 用一个 B 的提问把两个 A 提问隔成独立轮；末尾唯一回答（B）只能答 A 的问题。
        # 轮次：Q0(A,w0)、Q1(B,w1，与回答同说话人不可配)、Q2(A,w2)、A0(B,w3)。
        # Q0 与 Q2 都可配 A0（B），但一对一只能配 1 对（4 字），字典序取较早问题 Q0。
        words = [
            W(0, 100, "q0？", "A"),
            W(100, 200, "插话？", "B"),
            W(200, 300, "q1？", "A"),
            W(300, 400, "统一回答", "B"),  # Q0 gap300、Q2 gap0；Q1(B) 同说话人不可配
        ]
        pairs, unpaired = build_qa_pairs(words, 1000)
        self.assertEqual(pair_keys(pairs), ((0, 3),))  # 较早问题 w0
        self.assertEqual(unpaired, [1, 2])

    def test_lex_tie_on_answer_index(self):
        # 同一问题 Q0(A) 有两个等长回答 j0(B)、j1(C)，同字符数时取较早回答。
        words = [
            W(0, 100, "问题？", "A"),
            W(100, 200, "甲乙", "B"),
            W(200, 300, "丙丁", "C"),
        ]
        pairs, _ = build_qa_pairs(words, 1000)
        self.assertEqual(pair_keys(pairs), ((0, 1),))

    def test_pairs_sorted_by_question_and_unpaired_sorted(self):
        # Q0(A,w0,"q0？")、Q1(B,w1,"q1？")、A0(A,w2,"给q1的答"，仅答 Q1)、
        # Q2(A,w4,"落空？") 与 A0 同说话人相邻会合并，故用 B 提问 w3 隔开。
        words = [
            W(0, 100, "q0？", "A"),
            W(100, 200, "q1？", "B"),
            W(200, 300, "给q1的答", "A"),  # 回答：与 Q0 同说话人不可配，答 Q1
            W(300, 400, "垫？", "B"),      # B 提问，隔开两个 A 轮
            W(400, 500, "落空？", "A"),    # Q2，其后无回答
        ]
        pairs, unpaired = build_qa_pairs(words, 1000)
        self.assertEqual(pair_keys(pairs), ((1, 2),))
        self.assertEqual(unpaired, [0, 3, 4])


class ValidationTests(unittest.TestCase):
    def test_empty_words_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            build_qa_pairs([], 0)
        self.assertEqual(ctx.exception.code, "empty_words")

    def test_overlapping_rejected(self):
        words = [W(0, 200, "甲", "A"), W(150, 300, "乙", "A")]
        with self.assertRaises(DomainError) as ctx:
            build_qa_pairs(words, 0)
        self.assertEqual(ctx.exception.code, "overlapping_time_range")

    def test_out_of_order_rejected(self):
        words = [W(2000, 3000, "甲", "A"), W(1000, 1500, "乙", "A")]
        with self.assertRaises(DomainError) as ctx:
            build_qa_pairs(words, 0)
        self.assertEqual(ctx.exception.code, "time_out_of_order")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260925)
        texts = [
            "字", "你好", "行吗？", "真的?", "好！", "嗯", "ab", "为什么？",
            "   ", "很长的文本", "?", "。", "什么？  ", "？\t", "呢？\n", "？",
        ]
        speakers = ["A", "B", "C"]
        checked = 0
        for trial in range(5000):
            n = random.randint(1, 10)
            t = 0
            words = []
            for _ in range(n):
                t += random.choice([0, 0, 0, 150])
                dur = random.choice([100, 200, 300])
                words.append(W(t, t + dur, random.choice(texts), random.choice(speakers)))
                t += dur
            gap = random.choice([0, 50, 150, 300, 1000])

            exp_count, exp_chars, exp_seq, questions = brute_force(words, gap)
            pairs, unpaired = build_qa_pairs(words, gap)

            self.assertEqual(len(pairs), exp_count, f"trial {trial}: count mismatch")
            self.assertEqual(
                sum(len(p["answer"]["text"]) for p in pairs),
                exp_chars,
                f"trial {trial}: char total mismatch",
            )
            self.assertEqual(pair_keys(pairs), exp_seq, f"trial {trial}: lex mismatch")

            matched = {p["question"]["start_word"] for p in pairs}
            exp_unpaired = [
                q["start_word"] for q in questions if q["start_word"] not in matched
            ]
            self.assertEqual(unpaired, exp_unpaired, f"trial {trial}: unpaired mismatch")
            checked += 1
        self.assertGreater(checked, 4000)


if __name__ == "__main__":
    unittest.main()
