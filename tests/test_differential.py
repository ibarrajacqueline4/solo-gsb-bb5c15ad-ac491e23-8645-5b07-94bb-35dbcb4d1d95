"""暴力枚举对拍：在随机小输入上验证 DP 的最优分与字典序裁决完全正确。

暴力枚举全部可行分段（每段必须同说话人、<=18 字符、1000~6000ms），
按 (总分, 断点序列) 元组取最小，与 optimal_segmentation 对比。
"""

import random
import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.segmentation import (
    MAX_CHARS,
    MAX_DURATION_MS,
    MIN_DURATION_MS,
    NO_SENTENCE_ENDING_PENALTY,
    SHORT_DURATION_MS,
    SENTENCE_ENDINGS,
    optimal_segmentation,
)


def W(start, end, text, speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def brute_force_optimal(words):
    """返回 (score, cuts) 或 None（无可行分段）。cuts 为内部断点升序元组。"""
    n = len(words)

    # 预处理所有可行段 (a,b)，0<=a<b<=n
    valid = set()
    for a in range(n):
        chars = 0
        for b in range(a + 1, n + 1):
            chars += len(words[b - 1].text)
            if chars > MAX_CHARS:
                break
            duration = words[b - 1].end_ms - words[a].start_ms
            if duration < MIN_DURATION_MS:
                continue
            if duration > MAX_DURATION_MS:
                break
            if any(words[k].speaker != words[a].speaker for k in range(a, b)):
                continue
            valid.add((a, b))

    best = None  # (score, cuts)

    def recurse(a, cuts, score):
        nonlocal best
        # 枚举从 a 出发的下一个边界 b
        for b in range(a + 1, n + 1):
            if (a, b) not in valid:
                continue
            duration = words[b - 1].end_ms - words[a].start_ms
            if b == n:
                candidate = (score, tuple(cuts))
                if best is None or candidate < best:
                    best = candidate
            else:
                added = 0
                if duration < SHORT_DURATION_MS:
                    added += 1
                if words[b - 1].text[-1] not in SENTENCE_ENDINGS:
                    added += NO_SENTENCE_ENDING_PENALTY
                recurse(b, cuts + [b], score + added)

    recurse(0, [], 0)
    return best


class DifferentialTests(unittest.TestCase):
    def test_dp_matches_brute_force(self):
        random.seed(20260924)
        texts = ["字", "你好", "啊。", "好吗？", "对！", "嗯", "abc", "。"]
        checked = 0
        for trial in range(400):
            n = random.randint(1, 9)
            t = 0
            words = []
            for _ in range(n):
                gap = random.choice([0, 0, 0, 50])
                t += gap
                dur = random.choice([400, 700, 900, 1200, 1500, 2200, 3000])
                words.append(
                    W(t, t + dur, random.choice(texts), random.choice("AABB"))
                )
                t += dur

            expected = brute_force_optimal(words)
            if expected is None:
                with self.assertRaises(DomainError) as ctx:
                    optimal_segmentation(words)
                self.assertEqual(ctx.exception.code, "infeasible_segmentation")
            else:
                segments, score = optimal_segmentation(words)
                exp_score, exp_cuts = expected
                self.assertEqual(score, exp_score, f"trial {trial} score mismatch")
                cuts = [s["start_word"] for s in segments[1:]]
                self.assertEqual(
                    tuple(cuts), exp_cuts, f"trial {trial} tie-break mismatch"
                )
                # 额外校验覆盖性与硬约束
                covered = [
                    k for s in segments for k in range(s["start_word"], s["end_word"] + 1)
                ]
                self.assertEqual(covered, list(range(n)))
                for s in segments:
                    self.assertLessEqual(s["char_count"], MAX_CHARS)
                    self.assertGreaterEqual(s["duration_ms"], MIN_DURATION_MS)
                    self.assertLessEqual(s["duration_ms"], MAX_DURATION_MS)
                checked += 1
        self.assertGreater(checked, 100)  # 确保确实有足够多的可行对拍样本


if __name__ == "__main__":
    unittest.main()
