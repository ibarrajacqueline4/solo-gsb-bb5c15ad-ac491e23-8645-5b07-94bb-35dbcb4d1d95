"""播放变速规划核心逻辑单元测试 + 暴力枚举对拍（纯标准库）。

暴力枚举全部语速组合（每块 100% / 125% / 150%）：校验总播放时长（各块
播放时长 + 块间静音）不超 max_duration_ms，按 (总代价升序, 总时长降序,
语速序列字典序升序) 取最优，与 plan_playback 对比。
"""

import itertools
import random
import unittest
from types import SimpleNamespace

from app.errors import DomainError
from app.playback_plan import plan_playback

SPEEDS = (100, 125, 150)


def W(start, end, text="词", speaker="A"):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def ceil_div(a, b):
    return -(-a // b)


def brute_force(words, max_duration_ms):
    """独立实现：枚举全部语速组合，返回 (语速序列, 总代价, 总时长) 或 None。"""
    # 合并连续同一说话人的词为块（与实现不同的写法，互验）
    blocks = []
    for i, w in enumerate(words):
        if i == 0 or w.speaker != words[i - 1].speaker:
            blocks.append([i, i])
        else:
            blocks[-1][1] = i
    durations = [words[e].end_ms - words[s].start_ms for s, e in blocks]
    silences = sum(
        words[blocks[i + 1][0]].start_ms - words[blocks[i][1]].end_ms
        for i in range(len(blocks) - 1)
    )
    best = None
    best_speeds = None
    for combo in itertools.product(SPEEDS, repeat=len(blocks)):
        total = silences + sum(
            ceil_div(d * 100, s) for d, s in zip(durations, combo)
        )
        if total > max_duration_ms:
            continue
        cost = sum(d * (s - 100) ** 2 for d, s in zip(durations, combo))
        key = (cost, -total, combo)
        if best is None or key < best:
            best = key
            best_speeds = combo
    if best is None:
        return None
    return best_speeds, best[0], -best[1]


class BasicPlanTests(unittest.TestCase):
    def setUp(self):
        # 块：A=[0,1] 2000ms、B=[2,3] 2000ms、A=[4] 1000ms；
        # 块间静音 1000ms（[2000,3000]）与 500ms（[5000,5500]）
        self.words = [
            W(0, 1000, "开场白", "A"),
            W(1000, 2000, "欢迎收听", "A"),
            W(3000, 4000, "今天聊聊", "B"),
            W(4000, 5000, "动态规划", "B"),
            W(5500, 6500, "下期再见", "A"),
        ]

    def test_basic_plan(self):
        # 全 100% 需 6500ms 超目标；(125,125,100) 以代价 2500000 恰好压到 5700ms
        blocks, total_cost, total_duration = plan_playback(self.words, 5700)
        self.assertEqual(total_cost, 2_500_000)
        self.assertEqual(total_duration, 5700)
        self.assertEqual(
            blocks,
            [
                {
                    "index": 0,
                    "start_word": 0,
                    "end_word": 1,
                    "speaker": "A",
                    "source_start_ms": 0,
                    "source_end_ms": 2000,
                    "duration_ms": 2000,
                    "speed": 125,
                    "playback_duration_ms": 1600,
                    "playback_start_ms": 0,
                    "playback_end_ms": 1600,
                },
                {
                    "index": 1,
                    "start_word": 2,
                    "end_word": 3,
                    "speaker": "B",
                    "source_start_ms": 3000,
                    "source_end_ms": 5000,
                    "duration_ms": 2000,
                    "speed": 125,
                    "playback_duration_ms": 1600,
                    "playback_start_ms": 2600,  # 1600 + 静音 1000
                    "playback_end_ms": 4200,
                },
                {
                    "index": 2,
                    "start_word": 4,
                    "end_word": 4,
                    "speaker": "A",
                    "source_start_ms": 5500,
                    "source_end_ms": 6500,
                    "duration_ms": 1000,
                    "speed": 100,
                    "playback_duration_ms": 1000,
                    "playback_start_ms": 4700,  # 4200 + 静音 500
                    "playback_end_ms": 5700,
                },
            ],
        )

    def test_all_100_when_target_allows(self):
        # 目标不小于原时长 6500ms：全部 100%，代价 0
        blocks, total_cost, total_duration = plan_playback(self.words, 6500)
        self.assertEqual([b["speed"] for b in blocks], [100, 100, 100])
        self.assertEqual((total_cost, total_duration), (0, 6500))

    def test_all_150_when_target_tight(self):
        # 全 150% 才可行：播放 1334+1334+667=3335ms，总时长 4835ms
        blocks, total_cost, total_duration = plan_playback(self.words, 4835)
        self.assertEqual([b["speed"] for b in blocks], [150, 150, 150])
        self.assertEqual(
            [b["playback_duration_ms"] for b in blocks], [1334, 1334, 667]
        )
        self.assertEqual(total_cost, 2500 * 5000)
        self.assertEqual(total_duration, 4835)

    def test_infeasible_below_all_150(self):
        # 全 150% 也需 4835ms，目标 4834 不可行
        with self.assertRaises(DomainError) as ctx:
            plan_playback(self.words, 4834)
        err = ctx.exception
        self.assertEqual(err.code, "infeasible_playback_plan")
        self.assertEqual(err.extra["block_count"], 3)
        self.assertEqual(err.extra["max_duration_ms"], 4834)

    def test_ceil_playback_duration(self):
        words = [W(0, 1001, "甲", "A")]
        # 125%：ceil(1001*100/125) = ceil(800.8) = 801
        blocks, total_cost, _ = plan_playback(words, 801)
        self.assertEqual(blocks[0]["speed"], 125)
        self.assertEqual(blocks[0]["playback_duration_ms"], 801)
        self.assertEqual(total_cost, 1001 * 625)
        # 150%：ceil(1001*100/150) = ceil(667.33...) = 668
        blocks, total_cost, _ = plan_playback(words, 668)
        self.assertEqual(blocks[0]["speed"], 150)
        self.assertEqual(blocks[0]["playback_duration_ms"], 668)
        self.assertEqual(total_cost, 1001 * 2500)

    def test_silence_alone_exceeds_target(self):
        # 两个零时长块夹 1000ms 静音：目标 999 不可行，1000 可行
        words = [W(0, 0, "甲", "A"), W(1000, 1000, "乙", "B")]
        with self.assertRaises(DomainError) as ctx:
            plan_playback(words, 999)
        self.assertEqual(ctx.exception.code, "infeasible_playback_plan")
        blocks, total_cost, total_duration = plan_playback(words, 1000)
        self.assertEqual([b["speed"] for b in blocks], [100, 100])
        self.assertEqual((total_cost, total_duration), (0, 1000))


class TieBreakTests(unittest.TestCase):
    def test_same_cost_prefers_longer_duration(self):
        # 块 d=10（A）与 d=40（B），目标 47：代价 25000 有
        # (150,100) 47ms 与 (100,125) 42ms 两种，取总时长更长的 (150,100)
        words = [W(0, 10, "甲", "A"), W(10, 50, "乙", "B")]
        blocks, total_cost, total_duration = plan_playback(words, 47)
        self.assertEqual([b["speed"] for b in blocks], [150, 100])
        self.assertEqual((total_cost, total_duration), (25_000, 47))

    def test_same_cost_same_duration_lexicographic(self):
        # 块 d=6（A）与 d=6（B），目标 9：(150,125) 与 (125,150) 同代价同时长，
        # 取语速序列字典序最小的 (125,150)
        words = [W(0, 6, "甲", "A"), W(6, 12, "乙", "B")]
        blocks, total_cost, total_duration = plan_playback(words, 9)
        self.assertEqual([b["speed"] for b in blocks], [125, 150])
        self.assertEqual((total_cost, total_duration), (18_750, 9))

    def test_zero_duration_block_keeps_100(self):
        # 零时长块的语速不影响代价与时长，字典序裁决取 100
        words = [W(0, 0, "甲", "A"), W(0, 500, "乙", "B")]
        blocks, total_cost, total_duration = plan_playback(words, 400)
        self.assertEqual([b["speed"] for b in blocks], [100, 125])
        self.assertEqual((total_cost, total_duration), (500 * 625, 400))


class BlockValidationTests(unittest.TestCase):
    def test_too_many_blocks(self):
        # 21 个单词各自换说话人 => 21 块，超上限
        words = [
            W(i * 500, i * 500 + 400, "词", "AB"[i % 2]) for i in range(21)
        ]
        with self.assertRaises(DomainError) as ctx:
            plan_playback(words, 10**9)
        err = ctx.exception
        self.assertEqual(err.code, "too_many_blocks")
        self.assertEqual(err.extra["block_count"], 21)
        # 20 块恰好在上限内
        blocks, _, _ = plan_playback(words[:20], 10**9)
        self.assertEqual(len(blocks), 20)

    def test_single_word_single_block(self):
        blocks, total_cost, total_duration = plan_playback(
            [W(100, 600, "甲", "A")], 10**9
        )
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["start_word"], blocks[0]["end_word"])
        self.assertEqual(blocks[0]["duration_ms"], 500)
        self.assertEqual((total_cost, total_duration), (0, 500))

    def test_word_validation_reused(self):
        with self.assertRaises(DomainError) as ctx:
            plan_playback([], 0)
        self.assertEqual(ctx.exception.code, "empty_words")
        with self.assertRaises(DomainError) as ctx:
            plan_playback([W(1000, 2000, "甲"), W(900, 1500, "乙")], 0)
        self.assertEqual(ctx.exception.code, "time_out_of_order")
        with self.assertRaises(DomainError) as ctx:
            plan_playback([W(0, 2000, "甲"), W(1500, 3000, "乙")], 0)
        self.assertEqual(ctx.exception.code, "overlapping_time_range")
        with self.assertRaises(DomainError) as ctx:
            plan_playback([W(500, 400, "甲")], 0)
        self.assertEqual(ctx.exception.code, "invalid_time_range")


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(20260925)
        feasible = 0
        for trial in range(1500):
            # 随机词序列：1～6 个说话人区间（= 块数），每区间 1～3 词
            words = []
            t = 0
            for run in range(random.randint(1, 6)):
                speaker = chr(ord("A") + run)
                t += random.choice([0, 0, 200, 500])  # 块间静音
                for _ in range(random.randint(1, 3)):
                    dur = random.choice([0, 0, 100, 300, 700])
                    words.append(W(t, t + dur, "词", speaker))
                    t += dur
            max_duration = random.choice([0, 100, 800, 3000, 10**9])

            expected = brute_force(words, max_duration)
            if expected is None:
                with self.assertRaises(DomainError) as ctx:
                    plan_playback(words, max_duration)
                self.assertEqual(
                    ctx.exception.code, "infeasible_playback_plan", f"trial {trial}"
                )
                continue
            feasible += 1
            exp_speeds, exp_cost, exp_total = expected
            blocks, total_cost, total_duration = plan_playback(words, max_duration)
            self.assertEqual(total_cost, exp_cost, f"trial {trial}")
            self.assertEqual(total_duration, exp_total, f"trial {trial}")
            self.assertEqual(
                tuple(b["speed"] for b in blocks), exp_speeds, f"trial {trial}"
            )
            # 额外自洽校验：播放时刻从 0 累加、块间静音原样保留、时长不超目标
            cursor = 0
            for i, b in enumerate(blocks):
                self.assertEqual(b["playback_start_ms"], cursor)
                self.assertEqual(
                    b["playback_duration_ms"],
                    ceil_div(b["duration_ms"] * 100, b["speed"]),
                )
                self.assertEqual(
                    b["playback_end_ms"],
                    b["playback_start_ms"] + b["playback_duration_ms"],
                )
                self.assertEqual(
                    b["duration_ms"], b["source_end_ms"] - b["source_start_ms"]
                )
                cursor = b["playback_end_ms"]
                if i + 1 < len(blocks):
                    cursor += (
                        blocks[i + 1]["source_start_ms"] - b["source_end_ms"]
                    )
            self.assertEqual(cursor, total_duration)
            self.assertLessEqual(total_duration, max_duration)
        self.assertGreater(feasible, 300)  # 确保有足够多的可行对拍样本


if __name__ == "__main__":
    unittest.main()
