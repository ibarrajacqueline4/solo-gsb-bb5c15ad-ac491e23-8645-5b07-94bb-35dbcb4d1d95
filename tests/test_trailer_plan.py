"""预告片片段组合核心逻辑单元测试 + 暴力枚举对拍（纯标准库）。

暴力枚举全部候选子集：校验词区间互不重叠、总时长不超预算、必选说话人
全覆盖，按 (总评分降序, 总时长升序, 时间顺序候选序号序列字典序升序)
取最优，与 plan_trailer 对比。
"""

import itertools
import random
import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.trailer_plan import plan_trailer


def W(start, end, text="词", speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def C(start_word, end_word, score):
    return SimpleNamespace(start_word=start_word, end_word=end_word, score=score)


def brute_force(words, candidates, budget_ms, required_speakers):
    """独立实现：枚举全部子集，返回 (候选序号序列, 总评分, 总时长) 或 None。"""
    m = len(candidates)
    required = set(required_speakers)
    durs = [
        words[c.end_word].end_ms - words[c.start_word].start_ms for c in candidates
    ]
    speakers = [words[c.start_word].speaker for c in candidates]
    best_key = None
    best_seq = None
    for bits in itertools.product((0, 1), repeat=m):
        chosen = [t for t in range(m) if bits[t]]
        if not required <= {speakers[t] for t in chosen}:
            continue
        # 按时间顺序排列；词区间互不重叠 <=> 相邻区间前一末词早于后一首词
        ordered = sorted(
            chosen,
            key=lambda t: (candidates[t].start_word, candidates[t].end_word, t),
        )
        if any(
            candidates[b].start_word <= candidates[a].end_word
            for a, b in zip(ordered, ordered[1:])
        ):
            continue
        dur = sum(durs[t] for t in chosen)
        if dur > budget_ms:
            continue
        score = sum(candidates[t].score for t in chosen)
        seq = tuple(ordered)  # 原时间顺序的候选序号序列
        key = (-score, dur, seq)
        if best_key is None or key < best_key:
            best_key = key
            best_seq = seq
    if best_seq is None:
        return None
    return best_seq, -best_key[0], best_key[1]


class BasicPlanTests(unittest.TestCase):
    def setUp(self):
        # 说话人区间：A=[0,1]、B=[2,3]、A=[4]
        self.words = [
            W(0, 800, "甲", "A"),
            W(800, 1600, "乙", "A"),
            W(1600, 2400, "丙", "B"),
            W(2400, 3200, "丁", "B"),
            W(3200, 4000, "戊", "A"),
        ]

    def test_basic_plan(self):
        # c0=A[0,1] 1600ms/5 分，c1=B[2,3] 1600ms/7 分，c2=A[4] 800ms/3 分；
        # 预算 2400 覆盖 A+B：c0+c1 超预算，唯一可行最优为 c1+c2（10 分/2400ms）
        candidates = [C(0, 1, 5), C(2, 3, 7), C(4, 4, 3)]
        clips, total_score, total_duration = plan_trailer(
            self.words, candidates, 2400, ["A", "B"]
        )
        self.assertEqual(total_score, 10)
        self.assertEqual(total_duration, 2400)
        self.assertEqual(
            clips,
            [
                {
                    "index": 0,
                    "candidate_index": 1,
                    "speaker": "B",
                    "score": 7,
                    "start_word": 2,
                    "end_word": 3,
                    "source_start_ms": 1600,
                    "source_end_ms": 3200,
                    "duration_ms": 1600,
                    "trailer_start_ms": 0,
                    "trailer_end_ms": 1600,
                },
                {
                    "index": 1,
                    "candidate_index": 2,
                    "speaker": "A",
                    "score": 3,
                    "start_word": 4,
                    "end_word": 4,
                    "source_start_ms": 3200,
                    "source_end_ms": 4000,
                    "duration_ms": 800,
                    "trailer_start_ms": 1600,
                    "trailer_end_ms": 2400,
                },
            ],
        )

    def test_overlap_excluded(self):
        # c0 与 c1 词区间重叠（共享词 1），不能同时选；取分更高的 c1
        candidates = [C(0, 1, 5), C(1, 1, 9)]
        clips, total_score, _ = plan_trailer(self.words, candidates, 10**9, ["A"])
        self.assertEqual([c["candidate_index"] for c in clips], [1])
        self.assertEqual(total_score, 9)

    def test_budget_enforced(self):
        # 覆盖 A+B 必须 c0+c1 两段都选（共 3200ms），预算 3000 时不可行
        candidates = [C(0, 1, 5), C(2, 3, 7)]
        with self.assertRaises(DomainError) as ctx:
            plan_trailer(self.words, candidates, 3000, ["A", "B"])
        self.assertEqual(ctx.exception.code, "infeasible_trailer_plan")
        # 预算放宽到 3200 即可行
        _, total_score, total_duration = plan_trailer(
            self.words, candidates, 3200, ["A", "B"]
        )
        self.assertEqual((total_score, total_duration), (12, 3200))

    def test_required_speaker_forces_low_score_clip(self):
        # B 仅出现在 1 分的 c1，为覆盖 B 必须放弃更高分的纯 A 组合
        candidates = [C(0, 1, 100), C(2, 3, 1), C(4, 4, 100)]
        clips, total_score, _ = plan_trailer(self.words, candidates, 10**9, ["B"])
        self.assertEqual([c["candidate_index"] for c in clips], [0, 1, 2])
        self.assertEqual(total_score, 201)

    def test_optional_speaker_free_to_add(self):
        # 非必选说话人 C 的片段可自由加入以抬高总分
        words = self.words + [W(4000, 4800, "己", "C")]
        candidates = [C(0, 1, 1), C(5, 5, 2)]
        clips, total_score, _ = plan_trailer(words, candidates, 10**9, ["A"])
        self.assertEqual([c["candidate_index"] for c in clips], [0, 1])
        self.assertEqual(total_score, 3)

    def test_infeasible_when_required_speaker_absent(self):
        candidates = [C(0, 1, 5), C(4, 4, 3)]
        with self.assertRaises(DomainError) as ctx:
            plan_trailer(self.words, candidates, 10**9, ["C"])
        err = ctx.exception
        self.assertEqual(err.code, "infeasible_trailer_plan")
        self.assertEqual(err.extra["candidate_count"], 2)
        self.assertEqual(err.extra["budget_ms"], 10**9)

    def test_infeasible_when_budget_too_small(self):
        # 覆盖 B 至少需要 1600ms，预算 1599 不可行
        candidates = [C(2, 3, 7)]
        with self.assertRaises(DomainError) as ctx:
            plan_trailer(self.words, candidates, 1599, ["B"])
        self.assertEqual(ctx.exception.code, "infeasible_trailer_plan")
        # 预算恰好 1600 可行
        _, total_score, total_duration = plan_trailer(
            self.words, candidates, 1600, ["B"]
        )
        self.assertEqual((total_score, total_duration), (7, 1600))

    def test_duplicate_required_speakers_deduped(self):
        candidates = [C(0, 1, 5)]
        clips, total_score, _ = plan_trailer(self.words, candidates, 10**9, ["A", "A"])
        self.assertEqual(len(clips), 1)
        self.assertEqual(total_score, 5)


class TieBreakTests(unittest.TestCase):
    def setUp(self):
        self.words = [
            W(0, 500, "甲", "A"),
            W(500, 1000, "乙", "A"),
            W(1000, 1500, "丙", "A"),
            W(1500, 2000, "丁", "A"),
        ]

    def test_same_score_min_duration(self):
        # c0 与 c1 重叠（共享词 0）且同分（8），c1 时长更短（500 < 1000），取 c1
        candidates = [C(0, 1, 8), C(0, 0, 8)]
        clips, total_score, total_duration = plan_trailer(
            self.words, candidates, 10**9, ["A"]
        )
        self.assertEqual([c["candidate_index"] for c in clips], [1])
        self.assertEqual((total_score, total_duration), (8, 500))

    def test_same_score_same_duration_lexicographic(self):
        # c0、c1 区间完全相同且同分同时长：序列 (0,) < (1,)
        candidates = [C(0, 1, 8), C(0, 1, 8)]
        clips, _, _ = plan_trailer(self.words, candidates, 10**9, ["A"])
        self.assertEqual([c["candidate_index"] for c in clips], [0])

    def test_lexicographic_prefers_earlier_original_index(self):
        # 预算 500 使 c0（词 2）与 c1（词 0）只能二选一；同分同时长，
        # 序号序列 (0,) < (1,)，取原序号更小的 c0（尽管它时间上更靠后）
        candidates = [C(2, 2, 8), C(0, 0, 8)]
        clips, _, _ = plan_trailer(self.words, candidates, 500, ["A"])
        self.assertEqual([c["candidate_index"] for c in clips], [0])

    def test_lexicographic_prefers_prefix(self):
        # c1 为零分零时长候选：序列 (0,) 是 (0,1) 的前缀，取 (0,)
        words = [W(0, 500, "甲", "A"), W(500, 500, "乙", "A")]
        candidates = [C(0, 0, 5), C(1, 1, 0)]
        clips, total_score, total_duration = plan_trailer(
            words, candidates, 10**9, ["A"]
        )
        self.assertEqual([c["candidate_index"] for c in clips], [0])
        self.assertEqual((total_score, total_duration), (5, 500))

    def test_zero_duration_required_coverage(self):
        # 必选说话人 B 只有零分零时长候选：必须选上（目标 (0,0) 但 mask 未覆盖）
        words = [W(0, 0, "甲", "B"), W(0, 500, "乙", "A")]
        candidates = [C(0, 0, 0), C(1, 1, 5)]
        clips, total_score, total_duration = plan_trailer(words, candidates, 0, ["B"])
        self.assertEqual([c["candidate_index"] for c in clips], [0])
        self.assertEqual((total_score, total_duration), (0, 0))

    def test_budget_zero_allows_zero_duration_only(self):
        words = [W(0, 0, "甲", "A"), W(0, 500, "乙", "A")]
        candidates = [C(0, 0, 3), C(1, 1, 9)]
        clips, total_score, total_duration = plan_trailer(words, candidates, 0, ["A"])
        self.assertEqual([c["candidate_index"] for c in clips], [0])
        self.assertEqual((total_score, total_duration), (3, 0))


class CandidateValidationTests(unittest.TestCase):
    def setUp(self):
        self.words = [
            W(0, 500, "甲", "A"),
            W(500, 1000, "乙", "B"),
            W(1000, 1500, "丙", "B"),
        ]

    def plan(self, candidates):
        return plan_trailer(self.words, candidates, 10**9, ["A"])

    def test_candidate_index_out_of_range(self):
        for bad in (C(-1, 0, 1), C(0, 3, 1), C(3, 3, 1)):
            with self.assertRaises(DomainError) as ctx:
                self.plan([bad])
            err = ctx.exception
            self.assertEqual(err.code, "candidate_index_out_of_range")
            self.assertEqual(err.extra["candidate_index"], 0)

    def test_candidate_range_inverted(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan([C(2, 1, 1)])
        err = ctx.exception
        self.assertEqual(err.code, "candidate_range_inverted")
        self.assertEqual(err.extra["candidate_index"], 0)

    def test_candidate_speaker_mixed(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan([C(0, 1, 1)])  # 词 0 为 A、词 1 为 B
        err = ctx.exception
        self.assertEqual(err.code, "candidate_speaker_mixed")
        self.assertEqual(err.extra["candidate_index"], 0)

    def test_single_word_candidate(self):
        clips, _, _ = self.plan([C(0, 0, 4)])
        self.assertEqual(clips[0]["duration_ms"], 500)
        self.assertEqual(clips[0]["speaker"], "A")

    def test_word_validation_reused(self):
        with self.assertRaises(DomainError) as ctx:
            plan_trailer([], [C(0, 0, 1)], 0, ["A"])
        self.assertEqual(ctx.exception.code, "empty_words")
        with self.assertRaises(DomainError) as ctx:
            plan_trailer(
                [W(1000, 2000, "甲"), W(900, 1500, "乙")], [C(0, 0, 1)], 0, ["A"]
            )
        self.assertEqual(ctx.exception.code, "time_out_of_order")
        with self.assertRaises(DomainError) as ctx:
            plan_trailer(
                [W(0, 2000, "甲"), W(1500, 3000, "乙")], [C(0, 0, 1)], 0, ["A"]
            )
        self.assertEqual(ctx.exception.code, "overlapping_time_range")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260925)
        feasible = 0
        for trial in range(1500):
            # 随机词序列：2～4 个说话人区间，每区间 1～4 词
            words = []
            t = 0
            for run in range(random.randint(2, 4)):
                speaker = chr(ord("A") + run)
                for _ in range(random.randint(1, 4)):
                    t += random.choice([0, 0, 100, 300])
                    dur = random.choice([0, 100, 400, 700])
                    words.append(W(t, t + dur, "词", speaker))
                    t += dur
            n = len(words)
            # 每个词所属说话人区间的右开边界
            run_end = [0] * n
            boundary = n
            for i in range(n - 1, -1, -1):
                if i == n - 1 or words[i].speaker != words[i + 1].speaker:
                    boundary = i + 1
                run_end[i] = boundary
            # 随机合法候选（单一说话人连续区间）
            m = random.randint(1, 8)
            candidates = []
            for _ in range(m):
                s = random.randrange(n)
                e = random.randrange(s, run_end[s])
                candidates.append(C(s, e, random.randint(0, 9)))
            budget = random.choice([0, 200, 800, 2000, 10**9])
            present = sorted({w.speaker for w in words})
            k = random.randint(1, 3)
            required = [random.choice(present + ["Z"]) for _ in range(k)]

            expected = brute_force(words, candidates, budget, required)
            if expected is None:
                with self.assertRaises(DomainError) as ctx:
                    plan_trailer(words, candidates, budget, required)
                self.assertEqual(
                    ctx.exception.code, "infeasible_trailer_plan", f"trial {trial}"
                )
                continue
            feasible += 1
            exp_seq, exp_score, exp_dur = expected
            clips, total_score, total_duration = plan_trailer(
                words, candidates, budget, required
            )
            self.assertEqual(total_score, exp_score, f"trial {trial}")
            self.assertEqual(total_duration, exp_dur, f"trial {trial}")
            self.assertEqual(
                [c["candidate_index"] for c in clips], list(exp_seq), f"trial {trial}"
            )
            # 额外自洽校验：源时间升序、预告片时刻从 0 累加、时长不超预算
            cursor = 0
            prev_source_end = -1
            for clip in clips:
                self.assertEqual(clip["trailer_start_ms"], cursor)
                cursor += clip["duration_ms"]
                self.assertEqual(clip["trailer_end_ms"], cursor)
                self.assertGreaterEqual(clip["source_start_ms"], prev_source_end)
                prev_source_end = clip["source_end_ms"]
                self.assertEqual(
                    clip["duration_ms"],
                    clip["source_end_ms"] - clip["source_start_ms"],
                )
            self.assertEqual(cursor, total_duration)
            self.assertLessEqual(total_duration, budget)
        self.assertGreater(feasible, 300)  # 确保有足够多的可行对拍样本


if __name__ == "__main__":
    unittest.main()
