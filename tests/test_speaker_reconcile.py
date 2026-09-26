"""说话人校正核心逻辑单元测试 + 暴力枚举对拍（纯标准库）。

暴力枚举全部满足锁定的候选说话人序列，按 (总成本, 说话人序列字典序) 取最小，
与 reconcile_speakers 对比。
"""

import itertools
import random
import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.speaker_reconcile import reconcile_speakers


def W(start, end, text="字", speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def L(idx, speaker):
    return SimpleNamespace(word_index=idx, speaker=speaker)


def make_words(speakers, dur=500):
    """按说话人序列构造词序列：每词时长 dur，端点相接。"""
    return [
        W(i * dur, (i + 1) * dur, speaker=s) for i, s in enumerate(speakers)
    ]


def brute_force(words, locks, switch_cost):
    """返回 (成本, 说话人元组)：枚举全部满足锁定的指派取最优。"""
    candidates = sorted({w.speaker for w in words} | {l.speaker for l in locks})
    locked = {l.word_index: l.speaker for l in locks}
    n = len(words)
    best = None  # (成本, 说话人元组) 取最小即 (成本最小, 字典序最小)
    for assignment in itertools.product(candidates, repeat=n):
        if any(assignment[i] != spk for i, spk in locked.items()):
            continue
        changes = sum(1 for i in range(n) if assignment[i] != words[i].speaker)
        switches = sum(1 for i in range(1, n) if assignment[i] != assignment[i - 1])
        key = (changes + switch_cost * switches, assignment)
        if best is None or key < best:
            best = key
    return best


class SmoothingTests(unittest.TestCase):
    def test_empty_locks_smooth_isolated_flip(self):
        # A A B A，switch_cost=5：保留原标注需 2 次换人（成本 10），
        # 把词 2 改为 A 只付 1 次改动（成本 1）
        words = make_words(["A", "A", "B", "A"])
        revised, changed, cost = reconcile_speakers(words, [], 5)
        self.assertEqual([w["speaker"] for w in revised], ["A"] * 4)
        self.assertEqual(changed, [2])
        self.assertEqual(cost, 1)

    def test_zero_switch_cost_keeps_original(self):
        # 换人无代价：原标注 0 改动即最优
        words = make_words(["A", "B", "A"])
        revised, changed, cost = reconcile_speakers(words, [], 0)
        self.assertEqual([w["speaker"] for w in revised], ["A", "B", "A"])
        self.assertEqual(changed, [])
        self.assertEqual(cost, 0)

    def test_tie_break_picks_lexicographically_smallest(self):
        # A B，switch_cost=1：AB / AA / BB 同为成本 1，整条序列字典序最小为 AA
        words = make_words(["A", "B"])
        revised, changed, cost = reconcile_speakers(words, [], 1)
        self.assertEqual([w["speaker"] for w in revised], ["A", "A"])
        self.assertEqual(changed, [1])
        self.assertEqual(cost, 1)

    def test_no_change_needed(self):
        words = make_words(["A", "A", "A"])
        revised, changed, cost = reconcile_speakers(words, [], 3)
        self.assertEqual([w["speaker"] for w in revised], ["A", "A", "A"])
        self.assertEqual(changed, [])
        self.assertEqual(cost, 0)

    def test_single_word(self):
        words = make_words(["B"])
        revised, changed, cost = reconcile_speakers(words, [], 7)
        self.assertEqual([w["speaker"] for w in revised], ["B"])
        self.assertEqual(changed, [])
        self.assertEqual(cost, 0)


class LockTests(unittest.TestCase):
    def test_lock_forces_speaker_and_tie_break(self):
        # 锁定词 2 为 B，switch_cost=2：AABB 与 BBBB 同为成本 3，取字典序小的 AABB
        words = make_words(["A", "A", "B", "A"])
        revised, changed, cost = reconcile_speakers(words, [L(2, "B")], 2)
        self.assertEqual([w["speaker"] for w in revised], ["A", "A", "B", "B"])
        self.assertEqual(changed, [3])
        self.assertEqual(cost, 3)

    def test_lock_can_introduce_new_speaker(self):
        words = make_words(["A", "A"])
        revised, changed, cost = reconcile_speakers(words, [L(1, "C")], 0)
        self.assertEqual([w["speaker"] for w in revised], ["A", "C"])
        self.assertEqual(changed, [1])
        self.assertEqual(cost, 1)

    def test_lock_matching_original_keeps_sequence(self):
        words = make_words(["A", "B"])
        revised, changed, cost = reconcile_speakers(
            words, [L(0, "A"), L(1, "B")], 5
        )
        self.assertEqual([w["speaker"] for w in revised], ["A", "B"])
        self.assertEqual(changed, [])
        self.assertEqual(cost, 5)  # 一次相邻换人 × 5

    def test_all_words_locked(self):
        words = make_words(["A", "A", "A"])
        locks = [L(0, "B"), L(1, "B"), L(2, "A")]
        revised, changed, cost = reconcile_speakers(words, locks, 4)
        self.assertEqual([w["speaker"] for w in revised], ["B", "B", "A"])
        self.assertEqual(changed, [0, 1])
        self.assertEqual(cost, 2 + 4)  # 2 次改动 + 1 次换人 × 4


class OutputShapeTests(unittest.TestCase):
    def test_words_preserve_order_text_and_time(self):
        words = [
            W(0, 500, text="你", speaker="A"),
            W(500, 1000, text="好", speaker="B"),
            W(1000, 1500, text="吗", speaker="A"),
        ]
        revised, _, _ = reconcile_speakers(words, [], 10)
        self.assertEqual(
            [(w["start_ms"], w["end_ms"], w["text"]) for w in revised],
            [(0, 500, "你"), (500, 1000, "好"), (1000, 1500, "吗")],
        )


class ValidationTests(unittest.TestCase):
    def test_lock_index_out_of_range(self):
        words = make_words(["A", "B"])
        for bad in (2, -1, 100):
            with self.assertRaises(DomainError) as ctx:
                reconcile_speakers(words, [L(bad, "A")], 1)
            self.assertEqual(ctx.exception.code, "lock_index_out_of_range")
            self.assertEqual(ctx.exception.extra["word_index"], bad)

    def test_duplicate_lock(self):
        words = make_words(["A", "B"])
        with self.assertRaises(DomainError) as ctx:
            reconcile_speakers(words, [L(1, "A"), L(1, "B")], 1)
        self.assertEqual(ctx.exception.code, "duplicate_lock")
        self.assertEqual(ctx.exception.extra["word_index"], 1)

    def test_too_many_speakers(self):
        words = make_words(list("ABCDEFGH"))  # 8 个候选：未超限
        reconcile_speakers(words, [], 1)
        with self.assertRaises(DomainError) as ctx:  # 锁定引入第 9 个
            reconcile_speakers(words, [L(0, "Z")], 1)
        self.assertEqual(ctx.exception.code, "too_many_speakers")
        self.assertEqual(ctx.exception.extra["speaker_count"], 9)

    def test_word_validation_reused(self):
        words = [W(0, 2000), W(1500, 3000)]  # 时间重叠
        with self.assertRaises(DomainError) as ctx:
            reconcile_speakers(words, [], 1)
        self.assertEqual(ctx.exception.code, "overlapping_time_range")

    def test_empty_words_reused(self):
        with self.assertRaises(DomainError) as ctx:
            reconcile_speakers([], [], 1)
        self.assertEqual(ctx.exception.code, "empty_words")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260924)
        checked = 0
        for trial in range(300):
            n = random.randint(1, 6)
            pool = random.sample(["A", "B", "C", "D"], k=random.randint(1, 3))
            speakers = [random.choice(pool) for _ in range(n)]
            words = make_words(speakers)
            # 随机锁定：索引不重复，锁定值可能引入新说话人 "Z"（候选至多 4 个）
            lock_idx = random.sample(range(n), k=random.randint(0, n))
            locks = [L(i, random.choice(pool + ["Z"])) for i in lock_idx]
            switch_cost = random.randint(0, 10)

            exp_cost, exp_assignment = brute_force(words, locks, switch_cost)
            revised, changed, cost = reconcile_speakers(
                words, locks, switch_cost
            )
            got = tuple(w["speaker"] for w in revised)
            self.assertEqual(cost, exp_cost, f"trial {trial} cost mismatch")
            self.assertEqual(
                got, exp_assignment, f"trial {trial} tie-break mismatch"
            )
            # 额外校验：锁定严格满足、改动序号与总成本自洽
            for lock in locks:
                self.assertEqual(got[lock.word_index], lock.speaker)
            self.assertEqual(
                changed, [i for i in range(n) if got[i] != speakers[i]]
            )
            switches = sum(1 for i in range(1, n) if got[i] != got[i - 1])
            self.assertEqual(cost, len(changed) + switch_cost * switches)
            checked += 1
        self.assertGreater(checked, 100)  # 确保有足够多的对拍样本


if __name__ == "__main__":
    unittest.main()
