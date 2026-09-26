"""修订稿重对齐逐词时间核心逻辑单元测试 + LCS 暴力对拍（纯标准库）。

匹配部分用暴力 DFS 枚举全部保序匹配，按 (匹配数 desc, 索引对序列) 取最优，
与 retime_transcript 内部重建出的匹配对照；定时部分用小例子手工校验
均分、余数前置、首尾虚拟锚点、无匹配全长均分与 422 范围。
"""

import random
import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.transcript_retime import retime_transcript


def W(start, end, text, speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def R(text, speaker="A"):
    return SimpleNamespace(text=text, speaker=speaker)


def brute_force_best_matching(keys_a, keys_b):
    """枚举全部保序匹配，返回 (最大匹配数, 字典序最小索引对元组)。"""
    n, m = len(keys_a), len(keys_b)
    best_len = -1
    best_pairs = None

    def dfs(i, j, chosen):
        nonlocal best_len, best_pairs
        if i == n or j == m:
            length = len(chosen)
            if length > best_len or (length == best_len and
                                     (best_pairs is None or tuple(chosen) < best_pairs)):
                best_len = length
                best_pairs = tuple(chosen)
            return
        dfs(i + 1, j, chosen)
        dfs(i, j + 1, chosen)
        if keys_a[i] == keys_b[j]:
            dfs(i + 1, j + 1, chosen + [(i, j)])

    dfs(0, 0, [])
    return best_len, best_pairs


def matching_from_output(out):
    return tuple(
        (o["source_word_index"], o["index"])
        for o in out
        if o["source_word_index"] is not None
    )


def assert_timing_valid(test, words, revisions, out):
    """自洽校验：命中沿用原时间；未命中 run 均分锚点间隔且每词 >=1ms。"""
    n = len(words)
    m = len(revisions)
    for k, o in enumerate(out):
        test.assertEqual(o["index"], k)
        test.assertEqual(o["text"], revisions[k].text)
        test.assertEqual(o["speaker"], revisions[k].speaker)
        test.assertGreaterEqual(o["end_ms"], o["start_ms"])
        if k:
            test.assertGreaterEqual(o["start_ms"], out[k - 1]["end_ms"])
    full_start = words[0].start_ms
    full_end = words[n - 1].end_ms
    pairs = matching_from_output(out)

    # 构造未命中 run：(修订起, 修订止, 左锚点, 右锚点)
    seq = []
    last = -1
    last_src = None
    for si, rj in pairs:
        if rj > last + 1:
            left = words[last_src].end_ms if last >= 0 else full_start
            seq.append((last + 1, rj - 1, left, words[si].start_ms))
        last, last_src = rj, si
    if last < m - 1:
        left = words[last_src].end_ms if last >= 0 else full_start
        seq.append((last + 1, m - 1, left, full_end))
    if not pairs:
        seq = [(0, m - 1, full_start, full_end)]

    for r0, r1, left, right in seq:
        count = r1 - r0 + 1
        span = right - left
        test.assertGreaterEqual(span, count)
        base, rem = divmod(span, count)
        cursor = left
        for p in range(count):
            o = out[r0 + p]
            dur = base + (1 if p < rem else 0)  # 余数前置
            test.assertEqual((o["start_ms"], o["end_ms"]), (cursor, cursor + dur))
            cursor += dur
        test.assertEqual(cursor, right)


class MatchingTests(unittest.TestCase):
    def test_speaker_must_match(self):
        words = [W(0, 100, "甲", "A"), W(100, 200, "乙", "A")]
        revisions = [R("甲", "A"), R("甲", "B")]  # 第二个甲说话人不同，不匹配，后置均分
        out, matched = retime_transcript(words, revisions)
        self.assertEqual(matched, 1)
        self.assertEqual([o["source_word_index"] for o in out], [0, None])
        # 未匹配词落在 [w0.end=100, 末词 end=200)
        self.assertEqual((out[1]["start_ms"], out[1]["end_ms"]), (100, 200))

    def test_lex_min_over_first_index(self):
        # A=[x,b,a]，B=[a,b,a]：长度 2 的匹配可取 (1,1)(2,2) 或 (0? 无)——
        # B 的首 a 只能配 A 的 a（i=1 或 2）；配 i=1 时后续 b 无处可配，
        # 故首对最优为 (1,1)，B0 作前置未命中。
        words = [W(0, 50, "x"), W(100, 150, "b"), W(200, 250, "a")]
        revisions = [R("a"), R("b"), R("a")]
        out, matched = retime_transcript(words, revisions)
        self.assertEqual(matched, 2)
        self.assertEqual(matching_from_output(out), ((1, 1), (2, 2)))

    def test_lex_min_over_second_index(self):
        # A=[b,a]，B=[b,b,a]：长度 2 的匹配 (0,0)(1,2) 与 (0,1)(1,2)，
        # 索引对序列字典序取 (0,0) 更小者。
        words = [W(0, 50, "b"), W(100, 150, "a")]
        revisions = [R("b"), R("b"), R("a")]
        out, matched = retime_transcript(words, revisions)
        self.assertEqual(matched, 2)
        self.assertEqual(matching_from_output(out), ((0, 0), (1, 2)))

    def test_all_match_keeps_original_times(self):
        words = [W(0, 100, "甲"), W(100, 200, "乙"), W(300, 400, "丙")]
        revisions = [R("甲"), R("乙"), R("丙")]
        out, matched = retime_transcript(words, revisions)
        self.assertEqual(matched, 3)
        self.assertEqual(
            [(o["start_ms"], o["end_ms"], o["source_word_index"]) for o in out],
            [(0, 100, 0), (100, 200, 1), (300, 400, 2)],
        )


class TimingTests(unittest.TestCase):
    def test_no_match_even_split_remainder_front_loaded(self):
        # 全长 [0,2000)，3 词：base=666 rem=2 -> 667、667、666
        words = [W(0, 500, "甲"), W(1500, 2000, "乙")]
        revisions = [R("丙"), R("丁"), R("戊")]
        out, matched = retime_transcript(words, revisions)
        self.assertEqual(matched, 0)
        self.assertEqual(
            [(o["start_ms"], o["end_ms"]) for o in out],
            [(0, 667), (667, 1334), (1334, 2000)],
        )
        self.assertTrue(all(o["source_word_index"] is None for o in out))

    def test_middle_run_even_split(self):
        # 锚点 w0.end=500、w1.start=502，间隔 2ms 恰好放 2 词各 1ms
        words = [W(0, 500, "甲"), W(502, 1000, "乙")]
        revisions = [R("甲"), R("x"), R("y"), R("乙")]
        out, _ = retime_transcript(words, revisions)
        self.assertEqual(
            [(o["start_ms"], o["end_ms"], o["source_word_index"]) for o in out],
            [(0, 500, 0), (500, 501, None), (501, 502, None), (502, 1000, 1)],
        )

    def test_leading_and_trailing_virtual_anchors(self):
        words = [
            W(1000, 1500, "大家好"), W(2000, 2500, "今天"),
            W(3000, 3500, "再见"), W(4000, 4500, "尾"),
        ]
        revisions = [R("嗯"), R("今天"), R("那个"), R("再见"), R("啦")]
        out, matched = retime_transcript(words, revisions)
        self.assertEqual(matched, 2)
        # 前置 [首词 start=1000, w1.start=2000)；中段 [w1.end=2500, w2.start=3000)；
        # 后置 [w2.end=3500, 末词 end=4500)
        self.assertEqual(
            [(o["start_ms"], o["end_ms"], o["source_word_index"]) for o in out],
            [
                (1000, 2000, None),
                (2000, 2500, 1),
                (2500, 3000, None),
                (3000, 3500, 2),
                (3500, 4500, None),
            ],
        )

    def test_middle_run_insufficient_returns_422_with_range(self):
        words = [W(0, 500, "甲"), W(501, 1000, "乙")]  # 间隔 1ms
        revisions = [R("甲"), R("x"), R("y"), R("乙")]  # 需 2ms
        with self.assertRaises(DomainError) as ctx:
            retime_transcript(words, revisions)
        self.assertEqual(ctx.exception.code, "infeasible_retime")
        self.assertEqual(ctx.exception.extra["revision_start_index"], 1)
        self.assertEqual(ctx.exception.extra["revision_end_index"], 2)
        self.assertEqual(ctx.exception.extra["revision_word_count"], 2)
        self.assertEqual(ctx.exception.extra["available_ms"], 1)
        self.assertEqual(ctx.exception.extra["required_ms"], 2)

    def test_leading_run_insufficient_returns_422(self):
        words = [W(0, 500, "甲"), W(1000, 1500, "乙")]
        revisions = [R("x"), R("甲")]  # 前置 [0,0) span 0
        with self.assertRaises(DomainError) as ctx:
            retime_transcript(words, revisions)
        self.assertEqual(ctx.exception.code, "infeasible_retime")
        self.assertEqual(
            (ctx.exception.extra["revision_start_index"],
             ctx.exception.extra["revision_end_index"]),
            (0, 0),
        )

    def test_no_match_insufficient_full_span_returns_422(self):
        words = [W(0, 1, "甲")]
        revisions = [R("a"), R("b")]
        with self.assertRaises(DomainError) as ctx:
            retime_transcript(words, revisions)
        self.assertEqual(ctx.exception.code, "infeasible_retime")
        self.assertEqual(
            (ctx.exception.extra["revision_start_index"],
             ctx.exception.extra["revision_end_index"]),
            (0, 1),
        )
        self.assertEqual(ctx.exception.extra["available_ms"], 1)
        self.assertEqual(ctx.exception.extra["required_ms"], 2)

    def test_validation_reused(self):
        with self.assertRaises(DomainError) as ctx:
            retime_transcript([W(1000, 2000, "甲"), W(900, 1500, "乙")], [R("甲")])
        self.assertEqual(ctx.exception.code, "time_out_of_order")


class DifferentialTests(unittest.TestCase):
    def test_matching_matches_brute_force(self):
        random.seed(20260925)
        symbols = ["甲", "乙", "a", "b", "甲甲"]
        speakers = ["A", "B"]
        timed_ok = 0
        for trial in range(700):
            n = random.randint(1, 7)
            m = random.randint(1, 7)
            keys_a = [(random.choice(symbols), random.choice(speakers)) for _ in range(n)]
            keys_b = [(random.choice(symbols), random.choice(speakers)) for _ in range(m)]

            # 构造一个时间轴：词较长、间隔充裕，尽量让所有未命中 run 都可分
            words = []
            t = 1000  # 起点留白，前置 run 有 room
            for (text, sp) in keys_a:
                t += random.choice([0, 500, 2000])
                dur = random.choice([100, 400])
                words.append(W(t, t + dur, text, sp))
                t += dur
            t += 5000  # 末尾留白，后置 run 有 room
            words[-1] = W(words[-1].start_ms, t, words[-1].text, words[-1].speaker)

            revisions = [R(text, sp) for (text, sp) in keys_b]
            exp_len, exp_pairs = brute_force_best_matching(keys_a, keys_b)
            try:
                out, matched = retime_transcript(words, revisions)
            except DomainError as exc:
                # 即使间隔不足，匹配仍须与暴力解一致（422 在匹配之后才触发）
                self.assertEqual(exc.code, "infeasible_retime")
                continue
            self.assertEqual(matched, exp_len, f"trial {trial}: length mismatch")
            self.assertEqual(
                matching_from_output(out), exp_pairs,
                f"trial {trial}: tie-break mismatch {keys_a} vs {keys_b}",
            )
            assert_timing_valid(self, words, revisions, out)
            timed_ok += 1
        self.assertGreater(timed_ok, 200)


if __name__ == "__main__":
    unittest.main()
