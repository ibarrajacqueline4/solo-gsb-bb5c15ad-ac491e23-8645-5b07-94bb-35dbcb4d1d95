"""HTTP 接口层测试。

依赖 fastapi + httpx（TestClient 基于 httpx）。
未安装时自动跳过；核心算法测试见 test_segmentation.py（纯标准库即可运行）。
"""

import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import app
    _HAS_DEPS = True
except ImportError:  # pragma: no cover
    _HAS_DEPS = False


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_basic_segmentation(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 0, "end_ms": 2000, "text": "第一句。", "speaker": "A"},
                    {"start_ms": 2000, "end_ms": 4200, "text": "第二句！", "speaker": "B"},
                    {"start_ms": 4200, "end_ms": 6200, "text": "第三句？", "speaker": "B"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_score"], 0)
        self.assertEqual(len(data["segments"]), 2)  # B 的两句合段更优（末段零分）
        self.assertIn("1\n00:00:00,000 --> 00:00:02,000", data["srt"])

    def test_overlap_rejected(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 0, "end_ms": 2000, "text": "甲", "speaker": "A"},
                    {"start_ms": 1500, "end_ms": 3000, "text": "乙", "speaker": "A"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "overlapping_time_range")

    def test_out_of_order_rejected(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 2000, "end_ms": 3000, "text": "甲", "speaker": "A"},
                    {"start_ms": 1000, "end_ms": 1500, "text": "乙", "speaker": "A"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_infeasible_returns_earliest_prefix(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 0, "end_ms": 800, "text": "短", "speaker": "A"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_segmentation")
        self.assertEqual(err["last_word_index"], 0)

    def test_invalid_schema(self):
        resp = self.client.post("/segments", json={"words": []})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class AdBreakApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 500, "text": "甲", "speaker": "A"},
        {"start_ms": 1300, "end_ms": 1800, "text": "乙", "speaker": "A"},
        {"start_ms": 2700, "end_ms": 3200, "text": "丙", "speaker": "B"},
        {"start_ms": 3700, "end_ms": 4200, "text": "丁", "speaker": "B"},
    ]
    # 三处静音：[500,1300]=800ms（中点 900）、[1800,2700]=900ms（中点 2250）、
    # [3200,3700]=500ms（中点 3450）

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {
            "words": self.WORDS,
            "break_count": 2,
            "min_silence_ms": 500,
            "min_spacing_ms": 0,
        }
        payload.update(overrides)
        return self.client.post("/ad-breaks", json=payload)

    def test_basic_ad_breaks(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 总静音最大：900 + 800（中点间隔 1350 与 1200 均不受 min_spacing=0 限制）
        self.assertEqual(data["total_silence_ms"], 1700)
        self.assertEqual(
            data["breaks"],
            [
                {
                    "index": 0,
                    "time_ms": 900,
                    "silence_start_ms": 500,
                    "silence_end_ms": 1300,
                    "silence_ms": 800,
                },
                {
                    "index": 1,
                    "time_ms": 2250,
                    "silence_start_ms": 1800,
                    "silence_end_ms": 2700,
                    "silence_ms": 900,
                },
            ],
        )

    def test_spacing_forces_spread(self):
        # 中点两两相距 1350/1200/2550：间隔 >= 2000 时只能取首尾
        resp = self.post(min_spacing_ms=2000)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([b["time_ms"] for b in data["breaks"]], [900, 3450])
        self.assertEqual(data["total_silence_ms"], 1300)

    def test_min_silence_filters_candidates(self):
        # 阈值 850 只剩 900ms 一处候选，选 2 个无解
        resp = self.post(min_silence_ms=850)
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_ad_breaks")
        self.assertEqual(err["candidate_count"], 1)

    def test_infeasible_spacing(self):
        resp = self.post(min_spacing_ms=100_000)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "infeasible_ad_breaks")

    def test_break_count_out_of_range(self):
        for bad in (0, 6):
            resp = self.post(break_count=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_negative_or_zero_thresholds_rejected(self):
        for field, bad in (("min_silence_ms", 0), ("min_spacing_ms", -1)):
            resp = self.post(**{field: bad})
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words, break_count=1)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class SpeakerReconcileApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 500, "text": "甲", "speaker": "A"},
        {"start_ms": 500, "end_ms": 1000, "text": "乙", "speaker": "A"},
        {"start_ms": 1000, "end_ms": 1500, "text": "丙", "speaker": "B"},
        {"start_ms": 1500, "end_ms": 2000, "text": "丁", "speaker": "A"},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS, "locks": [], "switch_cost": 5}
        payload.update(overrides)
        return self.client.post("/speaker-reconcile", json=payload)

    def test_basic_reconcile_smooths(self):
        # A A B A，switch_cost=5：保留原标注需 2 次换人（成本 10），
        # 词 2 改为 A 仅 1 次改动（成本 1）
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([w["speaker"] for w in data["words"]], ["A"] * 4)
        self.assertEqual(data["changed_word_indices"], [2])
        self.assertEqual(data["total_cost"], 1)
        # 词序、文本、时间不变
        self.assertEqual(
            [(w["start_ms"], w["end_ms"], w["text"]) for w in data["words"]],
            [(0, 500, "甲"), (500, 1000, "乙"), (1000, 1500, "丙"), (1500, 2000, "丁")],
        )

    def test_lock_respected(self):
        # 锁定词 2 为 B，switch_cost=2：AABB 与 BBBB 同为成本 3，取字典序小的 AABB
        resp = self.post(locks=[{"word_index": 2, "speaker": "B"}], switch_cost=2)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([w["speaker"] for w in data["words"]], ["A", "A", "B", "B"])
        self.assertEqual(data["changed_word_indices"], [3])
        self.assertEqual(data["total_cost"], 3)

    def test_lock_can_introduce_new_speaker(self):
        resp = self.post(locks=[{"word_index": 0, "speaker": "C"}], switch_cost=0)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["words"][0]["speaker"], "C")
        self.assertEqual(data["changed_word_indices"], [0])
        self.assertEqual(data["total_cost"], 1)

    def test_locks_may_be_omitted(self):
        resp = self.client.post(
            "/speaker-reconcile",
            json={"words": self.WORDS, "switch_cost": 5},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["changed_word_indices"], [2])

    def test_lock_index_out_of_range(self):
        for bad in (4, -1):
            resp = self.post(locks=[{"word_index": bad, "speaker": "A"}])
            self.assertEqual(resp.status_code, 422)
            err = resp.json()["error"]
            self.assertEqual(err["code"], "lock_index_out_of_range")
            self.assertEqual(err["word_index"], bad)

    def test_duplicate_lock(self):
        resp = self.post(
            locks=[{"word_index": 1, "speaker": "A"}, {"word_index": 1, "speaker": "B"}]
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "duplicate_lock")

    def test_too_many_speakers(self):
        words = [
            {"start_ms": i * 500, "end_ms": (i + 1) * 500, "text": "字", "speaker": s}
            for i, s in enumerate("ABCDEFGH")
        ]
        resp = self.post(words=words, locks=[{"word_index": 0, "speaker": "Z"}])
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "too_many_speakers")
        self.assertEqual(err["speaker_count"], 9)

    def test_switch_cost_out_of_range(self):
        for bad in (-1, 11):
            resp = self.post(switch_cost=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_lock_schema_invalid(self):
        resp = self.post(locks=[{"word_index": 0}])  # 缺 speaker
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class ChapterAlignApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 500, "text": "大家好", "speaker": "A"},
        {"start_ms": 600, "end_ms": 1200, "text": "今天聊", "speaker": "A"},
        {"start_ms": 1300, "end_ms": 2000, "text": "动态规划", "speaker": "A"},
        {"start_ms": 2200, "end_ms": 2800, "text": "明天见", "speaker": "B"},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {
            "words": self.WORDS,
            "chapters": [
                {"title": "开场", "cue": "大家", "approx_ms": 0},
                {"title": "主题", "cue": "动态", "approx_ms": 1000},
            ],
            "min_gap_ms": 500,
        }
        payload.update(overrides)
        return self.client.post("/chapter-align", json=payload)

    def test_basic_alignment(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_deviation_ms"], 300)
        self.assertEqual(
            data["chapters"],
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

    def test_cross_word_hit(self):
        words = [
            {"start_ms": 0, "end_ms": 500, "text": "动态", "speaker": "A"},
            {"start_ms": 500, "end_ms": 1000, "text": "规划", "speaker": "A"},
        ]
        resp = self.post(
            words=words,
            chapters=[{"title": "t", "cue": "动态规划", "approx_ms": 100}],
            min_gap_ms=1,
        )
        self.assertEqual(resp.status_code, 200)
        ch = resp.json()["chapters"][0]
        self.assertEqual(ch["time_ms"], 0)  # 候选时刻取命中首字符所在词
        self.assertEqual(ch["word_index"], 0)
        self.assertEqual([ch["char_start"], ch["char_end"]], [0, 2])  # 截断到词末

    def test_speaker_boundary_blocks_hit(self):
        words = [
            {"start_ms": 0, "end_ms": 100, "text": "ab", "speaker": "A"},
            {"start_ms": 100, "end_ms": 200, "text": "cd", "speaker": "B"},
        ]
        resp = self.post(
            words=words,
            chapters=[{"title": "t", "cue": "bc", "approx_ms": 0}],
            min_gap_ms=1,
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_chapter_alignment")
        self.assertEqual(err["chapter_index"], 0)

    def test_gap_infeasible(self):
        words = [
            {"start_ms": 0, "end_ms": 100, "text": "甲", "speaker": "A"},
            {"start_ms": 200, "end_ms": 300, "text": "乙", "speaker": "A"},
        ]
        resp = self.post(
            words=words,
            chapters=[
                {"title": "a", "cue": "甲", "approx_ms": 0},
                {"title": "b", "cue": "乙", "approx_ms": 0},
            ],
            min_gap_ms=1000,
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_chapter_alignment")
        self.assertEqual(err["chapter_count"], 2)
        self.assertEqual(err["min_gap_ms"], 1000)

    def test_min_gap_must_be_positive(self):
        for bad in (0, -1):
            resp = self.post(min_gap_ms=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_chapter_fields_required(self):
        # 空 chapters、空 cue、负 approx_ms 均为 422
        for chapters in (
            [],
            [{"title": "t", "cue": "", "approx_ms": 0}],
            [{"title": "t", "cue": "甲", "approx_ms": -1}],
            [{"title": "", "cue": "甲", "approx_ms": 0}],
        ):
            resp = self.post(chapters=chapters)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class DuplicatePassagesApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 800, "text": "欢迎收听", "speaker": "A"},
        {"start_ms": 800, "end_ms": 1600, "text": "小宇宙", "speaker": "A"},
        {"start_ms": 1600, "end_ms": 2400, "text": "播客", "speaker": "A"},
        {"start_ms": 2400, "end_ms": 4000, "text": "今天聊动态规划。", "speaker": "B"},
        {"start_ms": 5000, "end_ms": 5800, "text": "欢迎收听", "speaker": "A"},
        {"start_ms": 5800, "end_ms": 6600, "text": "小宇宙", "speaker": "A"},
        {"start_ms": 6600, "end_ms": 7400, "text": "播客", "speaker": "A"},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS, "min_words": 2, "min_gap_ms": 1000}
        payload.update(overrides)
        return self.client.post("/duplicate-passages", json=payload)

    def test_basic_match(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["match"],
            {
                "word_count": 3,
                "text": "欢迎收听小宇宙播客",
                "first": {
                    "start_word": 0,
                    "end_word": 2,
                    "start_ms": 0,
                    "end_ms": 2400,
                },
                "second": {
                    "start_word": 4,
                    "end_word": 6,
                    "start_ms": 5000,
                    "end_ms": 7400,
                },
            },
        )

    def test_match_null_when_below_min_words(self):
        resp = self.post(min_words=4)  # 最长重复为 3 词
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["match"])

    def test_match_null_when_gap_too_large(self):
        resp = self.post(min_gap_ms=6000)  # 两处首词仅相隔 5000ms
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["match"])

    def test_speaker_must_match(self):
        words = [dict(w) for w in self.WORDS]
        words[4]["speaker"] = "B"  # 破坏第二处的符号序列
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["match"])

    def test_min_words_out_of_range(self):
        for bad in (1, 21):
            resp = self.post(min_words=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_negative_min_gap_rejected(self):
        resp = self.post(min_gap_ms=-1)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_words_over_20000_rejected(self):
        words = [
            {"start_ms": i * 500, "end_ms": i * 500 + 400, "text": "字", "speaker": "A"}
            for i in range(20001)
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        # 20000 词恰好在上限内
        resp = self.post(words=words[:20000])
        self.assertEqual(resp.status_code, 200)

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class RedactPreviewApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 800, "text": "记得使用", "speaker": "A"},
        {"start_ms": 800, "end_ms": 1400, "text": "优惠码", "speaker": "A"},
        {"start_ms": 1400, "end_ms": 2000, "text": "八八八", "speaker": "A"},
        {"start_ms": 2200, "end_ms": 3400, "text": "别错过", "speaker": "B"},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS, "phrases": ["使用优惠码", "八八八"]}
        payload.update(overrides)
        return self.client.post("/redact-preview", json=payload)

    def test_basic_redact(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            [w["text"] for w in data["words"]],
            ["记得██", "███", "███", "别错过"],
        )
        # 词序、时间、说话人保持不变
        self.assertEqual(
            [(w["start_ms"], w["end_ms"], w["speaker"]) for w in data["words"]],
            [(0, 800, "A"), (800, 1400, "A"), (1400, 2000, "A"), (2200, 3400, "B")],
        )
        self.assertEqual(
            data["hits"],
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

    def test_hit_cannot_cross_speaker(self):
        resp = self.post(phrases=["码八"])  # 词 1 末与词 2 首同属 A，可跨词命中
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["hits"]), 1)
        resp = self.post(phrases=["八别"])  # 跨说话人边界，不得命中
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["hits"], [])

    def test_no_hit_returns_original_words(self):
        resp = self.post(phrases=["不存在"])
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["hits"], [])
        self.assertEqual(data["words"], self.WORDS)

    def test_overlap_resolution(self):
        words = [{"start_ms": 0, "end_ms": 100, "text": "aaaaa", "speaker": "A"}]
        resp = self.post(words=words, phrases=["aa"])
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["words"][0]["text"], "████a")
        self.assertEqual(
            [(h["start_char"], h["end_char"]) for h in data["hits"]], [(0, 2), (2, 4)]
        )
        # 同起点短语较长优先
        resp = self.post(words=words, phrases=["aa", "aaaa"])
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["words"][0]["text"], "████a")
        self.assertEqual([h["phrase"] for h in data["hits"]], ["aaaa"])

    def test_phrases_count_out_of_range(self):
        for bad in ([], ["甲"] * 51):
            resp = self.post(phrases=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_empty_phrase_rejected(self):
        resp = self.post(phrases=["甲", ""])
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_words_over_2000_rejected(self):
        words = [
            {"start_ms": i * 500, "end_ms": i * 500 + 400, "text": "字", "speaker": "A"}
            for i in range(2001)
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        # 2000 词恰好在上限内
        resp = self.post(words=words[:2000])
        self.assertEqual(resp.status_code, 200)

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class TrailerPlanApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 800, "text": "甲", "speaker": "A"},
        {"start_ms": 800, "end_ms": 1600, "text": "乙", "speaker": "A"},
        {"start_ms": 1600, "end_ms": 2400, "text": "丙", "speaker": "B"},
        {"start_ms": 2400, "end_ms": 3200, "text": "丁", "speaker": "B"},
        {"start_ms": 3200, "end_ms": 4000, "text": "戊", "speaker": "A"},
    ]
    CANDIDATES = [
        {"start_word": 0, "end_word": 1, "score": 5},   # A，1600ms
        {"start_word": 2, "end_word": 3, "score": 7},   # B，1600ms
        {"start_word": 4, "end_word": 4, "score": 3},   # A，800ms
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {
            "words": self.WORDS,
            "candidates": self.CANDIDATES,
            "budget_ms": 2400,
            "required_speakers": ["A", "B"],
        }
        payload.update(overrides)
        return self.client.post("/trailer-plan", json=payload)

    def test_basic_trailer_plan(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_score"], 10)
        self.assertEqual(data["total_duration_ms"], 2400)
        self.assertEqual(
            data["clips"],
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

    def test_infeasible_returns_422(self):
        resp = self.post(budget_ms=2399)  # 覆盖 A+B 至少需 2400ms
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_trailer_plan")
        self.assertEqual(err["candidate_count"], 3)
        self.assertEqual(err["budget_ms"], 2399)

    def test_required_speaker_absent_infeasible(self):
        resp = self.post(required_speakers=["C"])
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "infeasible_trailer_plan")

    def test_candidate_index_out_of_range(self):
        for bad in (
            {"start_word": -1, "end_word": 0, "score": 1},
            {"start_word": 0, "end_word": 5, "score": 1},
        ):
            resp = self.post(candidates=[bad])
            self.assertEqual(resp.status_code, 422)
            err = resp.json()["error"]
            self.assertEqual(err["code"], "candidate_index_out_of_range")
            self.assertEqual(err["candidate_index"], 0)

    def test_candidate_range_inverted(self):
        resp = self.post(candidates=[{"start_word": 2, "end_word": 1, "score": 1}])
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "candidate_range_inverted")

    def test_candidate_speaker_mixed(self):
        resp = self.post(candidates=[{"start_word": 1, "end_word": 2, "score": 1}])
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "candidate_speaker_mixed")

    def test_candidate_count_out_of_range(self):
        for bad in (
            [],
            [{"start_word": 0, "end_word": 0, "score": 1}] * 21,
        ):
            resp = self.post(candidates=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_negative_score_rejected(self):
        resp = self.post(candidates=[{"start_word": 0, "end_word": 0, "score": -1}])
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_budget_and_required_speakers_schema(self):
        for overrides in (
            {"budget_ms": -1},
            {"required_speakers": []},
            {"required_speakers": ["A", "B", "C", "D"]},
            {"required_speakers": ["A", ""]},
        ):
            resp = self.post(**overrides)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class PlaybackPlanApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 1000, "text": "开场白", "speaker": "A"},
        {"start_ms": 1000, "end_ms": 2000, "text": "欢迎收听", "speaker": "A"},
        {"start_ms": 3000, "end_ms": 4000, "text": "今天聊聊", "speaker": "B"},
        {"start_ms": 4000, "end_ms": 5000, "text": "动态规划", "speaker": "B"},
        {"start_ms": 5500, "end_ms": 6500, "text": "下期再见", "speaker": "A"},
    ]
    # 块：A 2000ms、B 2000ms、A 1000ms；块间静音 1000ms 与 500ms

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS, "max_duration_ms": 5700}
        payload.update(overrides)
        return self.client.post("/playback-plan", json=payload)

    def test_basic_playback_plan(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_cost"], 2_500_000)
        self.assertEqual(data["total_duration_ms"], 5700)
        self.assertEqual(
            data["blocks"],
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
                    "playback_start_ms": 2600,
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
                    "playback_start_ms": 4700,
                    "playback_end_ms": 5700,
                },
            ],
        )

    def test_infeasible_returns_422(self):
        resp = self.post(max_duration_ms=4834)  # 全 150% 也需 4835ms
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_playback_plan")
        self.assertEqual(err["block_count"], 3)
        self.assertEqual(err["max_duration_ms"], 4834)
        # 放宽到 4835 即可行
        resp = self.post(max_duration_ms=4835)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total_duration_ms"], 4835)

    def test_too_many_blocks(self):
        words = [
            {
                "start_ms": i * 500,
                "end_ms": i * 500 + 400,
                "text": "词",
                "speaker": "AB"[i % 2],
            }
            for i in range(21)
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "too_many_blocks")
        self.assertEqual(err["block_count"], 21)
        # 20 块恰好在上限内
        resp = self.post(words=words[:20])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["blocks"]), 20)

    def test_max_duration_schema(self):
        for bad in (-1, None):
            resp = self.post(max_duration_ms=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        # 缺省 max_duration_ms
        resp = self.client.post("/playback-plan", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class CrosstalkReportApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 1000, "end_ms": 3000, "text": "没错", "speaker": "A"},  # 故意乱序
        {"start_ms": 0, "end_ms": 2000, "text": "我觉得", "speaker": "A"},
        {"start_ms": 1500, "end_ms": 2500, "text": "对对对", "speaker": "B"},
        {"start_ms": 4000, "end_ms": 5000, "text": "下期再见", "speaker": "B"},
    ]
    # A 覆盖 [0,3000)；B 覆盖 [1500,2500) 与 [4000,5000)；叠话 [1500,2500)

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS}
        payload.update(overrides)
        return self.client.post("/crosstalk-report", json=payload)

    def test_basic_crosstalk_report(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            data["speakers"],
            [
                {"speaker": "A", "covered_ms": 3000},
                {"speaker": "B", "covered_ms": 2000},
            ],
        )
        self.assertEqual(
            data["segments"],
            [
                {
                    "index": 0,
                    "start_ms": 1500,
                    "end_ms": 2500,
                    "duration_ms": 1000,
                    "speakers": ["A", "B"],
                }
            ],
        )
        self.assertEqual(
            data["pair_overlaps"],
            [{"speaker_a": "A", "speaker_b": "B", "overlap_ms": 1000}],
        )

    def test_touching_endpoints_no_overlap(self):
        words = [
            {"start_ms": 0, "end_ms": 1000, "text": "甲", "speaker": "A"},
            {"start_ms": 1000, "end_ms": 2000, "text": "乙", "speaker": "B"},
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["segments"], [])
        self.assertEqual(data["pair_overlaps"], [])

    def test_zero_duration_word_ignored(self):
        words = self.WORDS + [
            {"start_ms": 2000, "end_ms": 2000, "text": "呃", "speaker": "C"}
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["segments"]), 1)  # 不产生新叠话
        self.assertEqual(
            data["speakers"],
            [
                {"speaker": "A", "covered_ms": 3000},
                {"speaker": "B", "covered_ms": 2000},
                {"speaker": "C", "covered_ms": 0},
            ],
        )

    def test_three_way_overlap_counts_three_pairs(self):
        words = [
            {"start_ms": 0, "end_ms": 3000, "text": "甲", "speaker": "A"},
            {"start_ms": 1000, "end_ms": 4000, "text": "乙", "speaker": "B"},
            {"start_ms": 2000, "end_ms": 5000, "text": "丙", "speaker": "C"},
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            [(s["start_ms"], s["end_ms"], s["speakers"]) for s in data["segments"]],
            [(1000, 2000, ["A", "B"]), (2000, 3000, ["A", "B", "C"]), (3000, 4000, ["B", "C"])],
        )
        self.assertEqual(
            data["pair_overlaps"],
            [
                {"speaker_a": "A", "speaker_b": "B", "overlap_ms": 2000},
                {"speaker_a": "A", "speaker_b": "C", "overlap_ms": 1000},
                {"speaker_a": "B", "speaker_b": "C", "overlap_ms": 2000},
            ],
        )

    def test_invalid_time_range(self):
        words = [
            {"start_ms": 0, "end_ms": 1000, "text": "甲", "speaker": "A"},
            {"start_ms": 900, "end_ms": 500, "text": "乙", "speaker": "B"},
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "invalid_time_range")
        self.assertEqual(err["word_index"], 1)

    def test_too_many_speakers(self):
        words = [
            {
                "start_ms": i * 100,
                "end_ms": i * 100 + 50,
                "text": "词",
                "speaker": f"S{i:02d}",
            }
            for i in range(21)
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "too_many_speakers")
        self.assertEqual(err["speaker_count"], 21)
        # 20 名恰好在上限内
        resp = self.post(words=words[:20])
        self.assertEqual(resp.status_code, 200)

    def test_words_count_out_of_range(self):
        resp = self.post(words=[])
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        words = [
            {"start_ms": i * 500, "end_ms": i * 500 + 400, "text": "字", "speaker": "A"}
            for i in range(2001)
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        # 2000 词恰好在上限内
        resp = self.post(words=words[:2000])
        self.assertEqual(resp.status_code, 200)

    def test_unordered_and_overlapping_accepted(self):
        # 乱序与交叠在本接口合法（其他接口会拒绝）
        resp = self.post()
        self.assertEqual(resp.status_code, 200)

    def test_segments_still_works(self):
        words = [
            {"start_ms": 0, "end_ms": 2000, "text": "第一句。", "speaker": "A"},
            {"start_ms": 2000, "end_ms": 4000, "text": "第二句。", "speaker": "B"},
        ]
        resp = self.client.post("/segments", json={"words": words})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class TranscriptRetimeApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 1000, "end_ms": 1500, "text": "大家好", "speaker": "A"},
        {"start_ms": 2000, "end_ms": 2500, "text": "今天", "speaker": "A"},
        {"start_ms": 3000, "end_ms": 3500, "text": "再见", "speaker": "B"},
        {"start_ms": 4000, "end_ms": 4500, "text": "拜拜", "speaker": "B"},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS, "revisions": [
            {"text": "嗯", "speaker": "A"},
            {"text": "今天", "speaker": "A"},
            {"text": "那个", "speaker": "A"},
            {"text": "再见", "speaker": "B"},
            {"text": "拜拜", "speaker": "B"},
        ]}
        payload.update(overrides)
        return self.client.post("/transcript-retime", json=payload)

    def test_basic_retime(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 命中“今天”(w1) 与“再见”(w2)、“拜拜”(w3) 中——LCS：今天/再见/拜拜 三命中
        self.assertEqual(data["matched_count"], 3)
        self.assertEqual(
            data["words"],
            [
                {"index": 0, "start_ms": 1000, "end_ms": 2000, "text": "嗯",
                 "speaker": "A", "source_word_index": None},
                {"index": 1, "start_ms": 2000, "end_ms": 2500, "text": "今天",
                 "speaker": "A", "source_word_index": 1},
                {"index": 2, "start_ms": 2500, "end_ms": 3000, "text": "那个",
                 "speaker": "A", "source_word_index": None},
                {"index": 3, "start_ms": 3000, "end_ms": 3500, "text": "再见",
                 "speaker": "B", "source_word_index": 2},
                {"index": 4, "start_ms": 4000, "end_ms": 4500, "text": "拜拜",
                 "speaker": "B", "source_word_index": 3},
            ],
        )

    def test_no_match_even_split(self):
        resp = self.post(revisions=[
            {"text": "丙", "speaker": "A"},
            {"text": "丁", "speaker": "A"},
        ])
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["matched_count"], 0)
        # 全长 [1000,4500) span=3500，2 词：1750 各
        self.assertEqual(
            [(w["start_ms"], w["end_ms"], w["source_word_index"]) for w in data["words"]],
            [(1000, 2750, None), (2750, 4500, None)],
        )

    def test_speaker_must_match(self):
        # “今天”说话人改成 B，与原稿 A 的“今天”不匹配
        resp = self.post(revisions=[
            {"text": "今天", "speaker": "B"},
            {"text": "再见", "speaker": "B"},
        ])
        self.assertEqual(resp.status_code, 200)
        words = resp.json()["words"]
        self.assertEqual(resp.json()["matched_count"], 1)
        self.assertEqual([w["source_word_index"] for w in words], [None, 2])

    def test_lexicographically_smallest_pairs(self):
        # A=[b@0,a@100,a@200]（前置无 room，故让首词带前置间隔）
        words = [
            {"start_ms": 0, "end_ms": 50, "text": "x", "speaker": "A"},
            {"start_ms": 100, "end_ms": 150, "text": "b", "speaker": "A"},
            {"start_ms": 200, "end_ms": 250, "text": "a", "speaker": "A"},
        ]
        revisions = [
            {"text": "a", "speaker": "A"},
            {"text": "b", "speaker": "A"},
            {"text": "a", "speaker": "A"},
        ]
        resp = self.client.post(
            "/transcript-retime", json={"words": words, "revisions": revisions}
        )
        self.assertEqual(resp.status_code, 200)
        out = resp.json()["words"]
        # 长度 2：索引对序列取 (1,1)(2,2)（首对 i 最小），修订 0 前置均分 [0,100)
        self.assertEqual([w["source_word_index"] for w in out], [None, 1, 2])
        self.assertEqual((out[0]["start_ms"], out[0]["end_ms"]), (0, 100))

    def test_insufficient_gap_returns_422_with_range(self):
        words = [
            {"start_ms": 0, "end_ms": 500, "text": "甲", "speaker": "A"},
            {"start_ms": 501, "end_ms": 1000, "text": "乙", "speaker": "A"},
        ]
        revisions = [
            {"text": "甲", "speaker": "A"},
            {"text": "x", "speaker": "A"},
            {"text": "y", "speaker": "A"},
            {"text": "乙", "speaker": "A"},
        ]
        resp = self.client.post(
            "/transcript-retime", json={"words": words, "revisions": revisions}
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_retime")
        self.assertEqual(err["revision_start_index"], 1)
        self.assertEqual(err["revision_end_index"], 2)
        self.assertEqual(err["revision_word_count"], 2)
        self.assertEqual(err["available_ms"], 1)
        self.assertEqual(err["required_ms"], 2)

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_count_limits_and_schema(self):
        # words / revisions 各 301 个均为 422；300 个恰好合法
        big = [
            {"start_ms": i * 10, "end_ms": i * 10 + 9, "text": "字", "speaker": "A"}
            for i in range(301)
        ]
        revs = [{"text": "字", "speaker": "A"} for _ in range(301)]
        resp = self.client.post("/transcript-retime", json={"words": big, "revisions": revs})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

        words300 = [
            {"start_ms": i * 10, "end_ms": i * 10 + 9, "text": "字", "speaker": "A"}
            for i in range(300)
        ]
        resp = self.client.post(
            "/transcript-retime",
            json={"words": words300, "revisions": [{"text": "字", "speaker": "A"}]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["matched_count"], 1)

        # 空 revisions、缺字段、空 text 均为 422
        for payload in (
            {"words": self.WORDS, "revisions": []},
            {"words": self.WORDS, "revisions": [{"speaker": "A"}]},
            {"words": self.WORDS, "revisions": [{"text": "", "speaker": "A"}]},
            {"words": self.WORDS},
        ):
            resp = self.client.post("/transcript-retime", json=payload)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class QaPairsApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 500, "text": "你觉得", "speaker": "A"},
        {"start_ms": 500, "end_ms": 1000, "text": "行吗？", "speaker": "A"},
        {"start_ms": 1000, "end_ms": 1500, "text": "我觉得", "speaker": "B"},
        {"start_ms": 1500, "end_ms": 2000, "text": "可以", "speaker": "B"},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS, "max_gap_ms": 1000}
        payload.update(overrides)
        return self.client.post("/qa-pairs", json=payload)

    def test_basic_qa_pairs(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "pairs": [
                    {
                        "index": 0,
                        "question": {
                            "start_word": 0,
                            "end_word": 1,
                            "start_ms": 0,
                            "end_ms": 1000,
                            "speaker": "A",
                            "text": "你觉得行吗？",
                        },
                        "answer": {
                            "start_word": 2,
                            "end_word": 3,
                            "start_ms": 1000,
                            "end_ms": 2000,
                            "speaker": "B",
                            "text": "我觉得可以",
                        },
                    }
                ],
                "unpaired_question_start_words": [],
            },
        )

    def test_gap_filters_answer(self):
        # 回答首词 start=1000、问题末词 end=1000，gap=0；上限 -1 非法、0 合法
        resp = self.post(max_gap_ms=-1)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        resp = self.post(max_gap_ms=0)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["pairs"]), 1)

    def test_no_candidate_returns_empty_pairs_with_unpaired(self):
        # 只有提问、没有其他说话人回答
        words = [
            {"start_ms": 0, "end_ms": 500, "text": "为什么？", "speaker": "A"},
            {"start_ms": 500, "end_ms": 1000, "text": "嗯", "speaker": "A"},
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["pairs"], [])
        self.assertEqual(data["unpaired_question_start_words"], [])
        # 相邻同说话人合并成一轮，整轮不以？结尾，故也没有提问

        # 提问落单：其它说话人回答在窗口之外
        words2 = [
            {"start_ms": 0, "end_ms": 500, "text": "在吗？", "speaker": "A"},
            {"start_ms": 900, "end_ms": 1000, "text": "在", "speaker": "B"},  # gap=400
        ]
        resp = self.post(words=words2, max_gap_ms=399)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["pairs"], [])
        self.assertEqual(data["unpaired_question_start_words"], [0])

    def test_no_questions_returns_empty(self):
        words = [
            {"start_ms": 0, "end_ms": 500, "text": "今天天气", "speaker": "A"},
            {"start_ms": 500, "end_ms": 1000, "text": "不错", "speaker": "B"},
        ]
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(), {"pairs": [], "unpaired_question_start_words": []}
        )

    def test_trailing_whitespace_question_detection(self):
        words = [
            {"start_ms": 0, "end_ms": 500, "text": "真的吗? ", "speaker": "A"},
            {"start_ms": 500, "end_ms": 1000, "text": "真的", "speaker": "B"},
        ]
        resp = self.post(words=words, max_gap_ms=0)
        self.assertEqual(resp.status_code, 200)
        pairs = resp.json()["pairs"]
        self.assertEqual(len(pairs), 1)
        # 返回文本保留尾部空白原样
        self.assertEqual(pairs[0]["question"]["text"], "真的吗? ")

    def test_words_over_300_rejected(self):
        big = [
            {"start_ms": i * 10, "end_ms": i * 10 + 9, "text": "字", "speaker": "A"}
            for i in range(301)
        ]
        resp = self.post(words=big)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        # 300 词恰好在上限内
        resp = self.post(words=big[:300], max_gap_ms=0)
        self.assertEqual(resp.status_code, 200)

    def test_max_gap_ms_required_and_non_negative(self):
        resp = self.client.post("/qa-pairs", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class GainPlanApiTests(unittest.TestCase):
    FRAMES = [
        {"start_ms": 0, "end_ms": 1000, "loudness_lufs": -14, "true_peak_dbfs": -2},
        {"start_ms": 1000, "end_ms": 2000, "loudness_lufs": -20, "true_peak_dbfs": -10},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"frames": self.FRAMES, "target_lufs": -16, "max_step_db": 2}
        payload.update(overrides)
        return self.client.post("/gain-plan", json=payload)

    def test_basic_gain_plan(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "frames": [
                    {
                        "index": 0,
                        "gain_db": -2,
                        "resulting_loudness_lufs": -16,
                        "resulting_true_peak_dbfs": -4,
                    },
                    {
                        "index": 1,
                        "gain_db": -2,
                        "resulting_loudness_lufs": -22,
                        "resulting_true_peak_dbfs": -12,
                    },
                ],
                "total_cost": 6,
            },
        )

    def test_zero_step_forces_equal_gains(self):
        resp = self.post(
            frames=[
                {"start_ms": 0, "end_ms": 10, "loudness_lufs": -20, "true_peak_dbfs": -10},
                {"start_ms": 10, "end_ms": 20, "loudness_lufs": -10, "true_peak_dbfs": -10},
            ],
            max_step_db=0,
        )
        self.assertEqual(resp.status_code, 200)
        gains = [f["gain_db"] for f in resp.json()["frames"]]
        self.assertEqual(gains, [-6, -6])  # |g-4|+|g+6| 在 [-6,4] 恒为 10，取字典序最小
        self.assertEqual(resp.json()["total_cost"], 10)

    def test_peak_safety_result_at_most_zero(self):
        # 真峰值 0 的帧只能衰减；增益后真峰值必须 <= 0
        resp = self.post(
            frames=[
                {"start_ms": 0, "end_ms": 10, "loudness_lufs": -16, "true_peak_dbfs": 0}
            ],
            target_lufs=-14,  # 理想增益 +2 被峰值约束压回 0
            max_step_db=2,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["frames"][0]["gain_db"], 0)
        self.assertEqual(data["frames"][0]["resulting_true_peak_dbfs"], 0)
        self.assertEqual(data["total_cost"], 2)

    def test_touching_endpoints_allowed(self):
        # 端点相接（下帧 start == 上帧 end）合法；响度恰为目标时增益 0
        resp = self.post(
            frames=[
                {"start_ms": 0, "end_ms": 100, "loudness_lufs": -16, "true_peak_dbfs": -3},
                {"start_ms": 100, "end_ms": 200, "loudness_lufs": -16, "true_peak_dbfs": -3},
            ],
            target_lufs=-16,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([f["gain_db"] for f in resp.json()["frames"]], [0, 0])

    def test_overlapping_frames_rejected(self):
        resp = self.post(
            frames=[
                {"start_ms": 0, "end_ms": 1000, "loudness_lufs": -14, "true_peak_dbfs": -2},
                {"start_ms": 500, "end_ms": 1500, "loudness_lufs": -20, "true_peak_dbfs": -10},
            ]
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "overlapping_time_range")
        self.assertEqual(err["frame_index"], 1)

    def test_out_of_order_frames_rejected(self):
        resp = self.post(
            frames=[
                {"start_ms": 1000, "end_ms": 2000, "loudness_lufs": -14, "true_peak_dbfs": -2},
                {"start_ms": 0, "end_ms": 900, "loudness_lufs": -20, "true_peak_dbfs": -10},
            ]
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "time_out_of_order")
        self.assertEqual(err["frame_index"], 1)

    def test_non_positive_duration_rejected(self):
        # end_ms == start_ms 由核心校验拒绝（invalid_time_range）；
        # end_ms 为负数在模型层 ge=0 即被拒（invalid_request）
        resp = self.post(
            frames=[
                {"start_ms": 100, "end_ms": 100, "loudness_lufs": -14, "true_peak_dbfs": -2}
            ]
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "invalid_time_range")
        self.assertEqual(err["frame_index"], 0)

        resp = self.post(
            frames=[
                {"start_ms": 0, "end_ms": -100, "loudness_lufs": -14, "true_peak_dbfs": -2}
            ]
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_value_ranges_enforced(self):
        bad_payloads = [
            {"frames": []},
            {"frames": [
                {"start_ms": 0, "end_ms": 1, "loudness_lufs": -20, "true_peak_dbfs": -3}
            ] * 301},
            {"frames": [
                {"start_ms": 0, "end_ms": 1, "loudness_lufs": -81, "true_peak_dbfs": -3}
            ]},
            {"frames": [
                {"start_ms": 0, "end_ms": 1, "loudness_lufs": -20, "true_peak_dbfs": 1}
            ]},
            {"target_lufs": -41},
            {"target_lufs": 1},
            {"max_step_db": -1},
            {"max_step_db": 13},
        ]
        for overrides in bad_payloads:
            payload = {
                "frames": [
                    {"start_ms": 0, "end_ms": 1, "loudness_lufs": -20, "true_peak_dbfs": -3}
                ],
                "target_lufs": -20,
                "max_step_db": 2,
            }
            payload.update(overrides)
            resp = self.client.post("/gain-plan", json=payload)
            self.assertEqual(resp.status_code, 422, payload)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request", payload)

    def test_integer_only_enforced(self):
        # 与其他接口一致使用普通 int（Pydantic 默认 lax 模式）：小数值拒绝；
        # 整数值浮点 -16.0 / 数字字符串会被 lax 模式宽松接受（项目既有约定）
        for bad in (-16.5, 0.5):
            resp = self.post(target_lufs=bad)
            self.assertEqual(resp.status_code, 422, bad)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")
        for bad in ({"max_step_db": 2.5}, {"frames": [
            {"start_ms": 0, "end_ms": 1.5, "loudness_lufs": -16, "true_peak_dbfs": -2}
        ]}):
            payload = {"frames": self.FRAMES, "target_lufs": -16, "max_step_db": 2}
            payload.update(bad)
            resp = self.client.post("/gain-plan", json=payload)
            self.assertEqual(resp.status_code, 422, bad)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_300_frames_accepted(self):
        # 300 帧恰好在上限内；帧 [i, i+1) 端点相接，响度恰为目标，全 0 增益
        frames = [
            {"start_ms": i, "end_ms": i + 1, "loudness_lufs": -20, "true_peak_dbfs": -3}
            for i in range(300)
        ]
        resp = self.post(frames=frames, target_lufs=-20)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["frames"]), 300)
        self.assertEqual(data["total_cost"], 0)
        self.assertTrue(all(f["gain_db"] == 0 for f in data["frames"]))

    def test_segments_still_works(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 0, "end_ms": 2000, "text": "第一句。", "speaker": "A"}
                ]
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


if __name__ == "__main__":
    unittest.main()