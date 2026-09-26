"""请求/响应 Pydantic 模型。"""

from typing import Annotated

from pydantic import BaseModel, Field


class WordIn(BaseModel):
    start_ms: int = Field(..., ge=0, description="词起始时间（毫秒）")
    end_ms: int = Field(..., ge=0, description="词结束时间（毫秒）")
    text: str = Field(..., min_length=1, description="词文本；段文本为词文本原样拼接")
    speaker: str = Field(..., min_length=1, description="说话人标识；换人处强制断段")


class SegmentRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")


class SegmentOut(BaseModel):
    index: int
    start_word: int = Field(..., description="段首词序号（0-based，含）")
    end_word: int = Field(..., description="段末词序号（0-based，含）")
    start_ms: int
    end_ms: int
    duration_ms: int
    speaker: str
    text: str
    char_count: int = Field(..., description="Unicode 字符数（code point）")


class SegmentResponse(BaseModel):
    segments: list[SegmentOut]
    total_score: int
    srt: str


class AdBreakRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    break_count: int = Field(..., ge=1, le=5, description="插播点个数（1～5）")
    min_silence_ms: int = Field(..., ge=1, description="候选静音时长下限（毫秒，正整数）")
    min_spacing_ms: int = Field(..., ge=0, description="相邻插播点最小间隔（毫秒，非负整数）")


class AdBreakOut(BaseModel):
    index: int
    time_ms: int = Field(..., description="插播时间：静音区间的整数中点")
    silence_start_ms: int = Field(..., description="静音起点（前词 end_ms）")
    silence_end_ms: int = Field(..., description="静音终点（后词 start_ms）")
    silence_ms: int = Field(..., description="该处静音时长（毫秒）")


class AdBreakResponse(BaseModel):
    breaks: list[AdBreakOut] = Field(..., description="按时间升序的插播点")
    total_silence_ms: int = Field(..., description="选中静音的总毫秒数")


class SpeakerLockIn(BaseModel):
    word_index: int = Field(..., description="被锁定的词序号（0-based）")
    speaker: str = Field(..., min_length=1, description="该词必须使用的说话人标识")


class SpeakerReconcileRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    locks: list[SpeakerLockIn] = Field(
        default_factory=list,
        description="人工锁定项：指定某些词必须使用的说话人；可为空或省略",
    )
    switch_cost: int = Field(..., ge=0, le=10, description="相邻换人的单位代价（0～10）")


class SpeakerReconcileResponse(BaseModel):
    words: list[WordIn] = Field(
        ..., description="重指派说话人后的词序列；词序、文本、时间与输入一致"
    )
    changed_word_indices: list[int] = Field(
        ..., description="说话人被改动的词序号（0-based，升序）"
    )
    total_cost: int = Field(
        ..., description="最小总成本：改动词数 + switch_cost × 相邻换人次数"
    )


class ChapterIn(BaseModel):
    title: str = Field(..., min_length=1, description="章节标题")
    cue: str = Field(
        ..., min_length=1, description="章节定位文本；在同一说话人连续词文本中查找命中"
    )
    approx_ms: int = Field(..., ge=0, description="章节预计起点（毫秒，非负整数）")


class ChapterAlignRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    chapters: list[ChapterIn] = Field(
        ..., min_length=1, description="按章节顺序排列的章节（标题、非空 cue、预计起点）"
    )
    min_gap_ms: int = Field(..., ge=1, description="相邻章节命中时刻的最小间隔（毫秒，正整数）")


class ChapterAlignOut(BaseModel):
    index: int
    title: str = Field(..., description="章节标题（按输入章节顺序返回）")
    time_ms: int = Field(..., description="实际时刻：命中首字符所在词的 start_ms")
    word_index: int = Field(..., description="命中首字符所在词序号（0-based）")
    char_start: int = Field(
        ..., description="命中首字符在该词内的 Unicode 字符偏移（0-based，含）"
    )
    char_end: int = Field(
        ...,
        description="词内字符偏移区间右端（不含）；命中跨词时截断到命中首字符所在词词末",
    )


class ChapterAlignResponse(BaseModel):
    chapters: list[ChapterAlignOut] = Field(..., description="按章节顺序排列的对齐结果")
    total_deviation_ms: int = Field(
        ..., description="最小总绝对偏差：各章实际时刻与 approx_ms 的绝对偏差之和"
    )


class DuplicatePassagesRequest(BaseModel):
    words: list[WordIn] = Field(
        ..., min_length=1, max_length=20000, description="按时间排列的词序列（至多 20000 词）"
    )
    min_words: int = Field(..., ge=2, le=20, description="重复片段的最短词数（2～20）")
    min_gap_ms: int = Field(
        ..., ge=0, description="两处首词 start_ms 的最小间隔（毫秒，非负整数）"
    )


class DuplicatePassageOut(BaseModel):
    start_word: int = Field(..., description="片段首词序号（0-based，含）")
    end_word: int = Field(..., description="片段末词序号（0-based，含）")
    start_ms: int
    end_ms: int


class DuplicatePassageMatch(BaseModel):
    word_count: int = Field(..., description="片段词数（两处相同）")
    text: str = Field(..., description="共同文本：词 text 原样拼接，不插空格")
    first: DuplicatePassageOut = Field(..., description="起始词索引较小的一处")
    second: DuplicatePassageOut = Field(..., description="起始词索引较大的一处")


class DuplicatePassagesResponse(BaseModel):
    match: DuplicatePassageMatch | None = Field(
        ..., description="词数最多的重复片段对；不足 min_words 时为 null"
    )


class RedactPreviewRequest(BaseModel):
    words: list[WordIn] = Field(
        ..., min_length=1, max_length=2000, description="按时间排列的词序列（至多 2000 词）"
    )
    phrases: list[Annotated[str, Field(min_length=1)]] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="敏感短语列表：1～50 个，每项为非空字符串",
    )


class RedactHitOut(BaseModel):
    index: int
    phrase: str = Field(..., description="命中的敏感短语")
    start_word: int = Field(..., description="命中首字符所在词序号（0-based，含）")
    end_word: int = Field(..., description="命中末字符所在词序号（0-based，含）")
    start_char: int = Field(
        ..., description="命中首字符在首词内的 Unicode 字符偏移（0-based，含）"
    )
    end_char: int = Field(
        ..., description="命中末字符在末词内的字符偏移区间右端（不含）"
    )


class RedactPreviewResponse(BaseModel):
    words: list[WordIn] = Field(
        ..., description="遮罩后的词序列；词序、时间、说话人与输入一致，仅 text 被改写"
    )
    hits: list[RedactHitOut] = Field(
        ..., description="按命中位置升序的命中列表；无命中时为空数组"
    )


class TrailerCandidateIn(BaseModel):
    start_word: int = Field(..., description="候选片段首词序号（0-based，含）")
    end_word: int = Field(..., description="候选片段末词序号（0-based，含）")
    score: int = Field(..., ge=0, description="候选片段评分（非负整数）")


class TrailerPlanRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    candidates: list[TrailerCandidateIn] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="候选片段：1～20 个，每个须为单一说话人的连续词区间",
    )
    budget_ms: int = Field(
        ..., ge=0, description="预告片总时长上限（毫秒，非负整数）"
    )
    required_speakers: list[Annotated[str, Field(min_length=1)]] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="必选说话人：1～3 个非空标识，每个至少在选中片段中出现一次",
    )


class TrailerClipOut(BaseModel):
    index: int
    candidate_index: int = Field(..., description="原候选序号（0-based）")
    speaker: str
    score: int
    start_word: int = Field(..., description="片段首词序号（0-based，含）")
    end_word: int = Field(..., description="片段末词序号（0-based，含）")
    source_start_ms: int = Field(..., description="片段源起点：首词 start_ms")
    source_end_ms: int = Field(..., description="片段源终点：末词 end_ms")
    duration_ms: int = Field(..., description="片段时长：source_end_ms - source_start_ms")
    trailer_start_ms: int = Field(..., description="预告片内起点：从 0 起按片段时长累加")
    trailer_end_ms: int = Field(..., description="预告片内终点：trailer_start_ms + duration_ms")


class TrailerPlanResponse(BaseModel):
    clips: list[TrailerClipOut] = Field(..., description="按源时间升序的选中片段")
    total_score: int = Field(..., description="选中片段的总评分（最大化）")
    total_duration_ms: int = Field(
        ..., description="选中片段的总时长（同分时最小化；不超过 budget_ms）"
    )


class PlaybackPlanRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    max_duration_ms: int = Field(
        ..., ge=0, description="目标最长播放时长（毫秒，非负整数）"
    )


class PlaybackBlockOut(BaseModel):
    index: int
    start_word: int = Field(..., description="块首词序号（0-based，含）")
    end_word: int = Field(..., description="块末词序号（0-based，含）")
    speaker: str
    source_start_ms: int = Field(..., description="块源起点：首词 start_ms")
    source_end_ms: int = Field(..., description="块源终点：末词 end_ms")
    duration_ms: int = Field(..., description="块原始时长：source_end_ms - source_start_ms")
    speed: int = Field(..., description="语速：100 / 125 / 150（百分比）")
    playback_duration_ms: int = Field(
        ..., description="块播放时长：ceil(原时长 × 100 / 语速)"
    )
    playback_start_ms: int = Field(..., description="播放时间轴上的起点")
    playback_end_ms: int = Field(
        ..., description="播放时间轴上的终点：playback_start_ms + playback_duration_ms"
    )


class PlaybackPlanResponse(BaseModel):
    blocks: list[PlaybackBlockOut] = Field(..., description="按时间升序的语速块")
    total_cost: int = Field(
        ..., description="最小总变速代价：Σ 原时长 × (语速 - 100)²"
    )
    total_duration_ms: int = Field(
        ..., description="总播放时长：各块播放时长与块间静音之和（不超过 max_duration_ms）"
    )


class CrosstalkReportRequest(BaseModel):
    words: list[WordIn] = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="词序列（至多 2000 词）；允许乱序与交叠，仅逐词校验 end_ms >= start_ms",
    )


class CrosstalkSpeakerOut(BaseModel):
    speaker: str
    covered_ms: int = Field(
        ..., description="发声覆盖总毫秒数：词区间并集时长，同一说话人重叠词只计一次"
    )


class CrosstalkSegmentOut(BaseModel):
    index: int
    start_ms: int
    end_ms: int
    duration_ms: int
    speakers: list[str] = Field(
        ..., description="该段同时发声的说话人（至少两人，按标识排序）"
    )


class CrosstalkPairOverlapOut(BaseModel):
    speaker_a: str = Field(..., description="说话人对中标识较小者")
    speaker_b: str = Field(..., description="说话人对中标识较大者")
    overlap_ms: int = Field(..., description="该对同时发声的总毫秒数（> 0）")


class CrosstalkReportResponse(BaseModel):
    speakers: list[CrosstalkSpeakerOut] = Field(
        ..., description="按标识排序的各说话人发声覆盖"
    )
    segments: list[CrosstalkSegmentOut] = Field(
        ..., description="按时间升序的叠话区间（至少两人同时发声）；无叠话时为空数组"
    )
    pair_overlaps: list[CrosstalkPairOverlapOut] = Field(
        ..., description="按说话人对排序的总重叠毫秒数；仅含 overlap_ms > 0 的对"
    )


class TranscriptRetimeRequest(BaseModel):
    words: list[WordIn] = Field(
        ...,
        min_length=1,
        max_length=300,
        description="原稿词序列（至多 300 词）：按时间排列且互不重叠，提供时间锚点",
    )
    revisions: list["TranscriptRevisionIn"] = Field(
        ...,
        min_length=1,
        max_length=300,
        description="修订词序列（至多 300 词）：每项含非空 text/speaker，无需时间",
    )


class TranscriptRevisionIn(BaseModel):
    text: str = Field(..., min_length=1, description="修订词文本；按 (text, speaker) 与原稿匹配")
    speaker: str = Field(..., min_length=1, description="修订词说话人；匹配时须与原稿一致")


class TranscriptRetimeWordOut(BaseModel):
    index: int
    start_ms: int = Field(..., description="重定时后的词起始时间（毫秒）")
    end_ms: int = Field(..., description="重定时后的词结束时间（毫秒，不早于 start_ms）")
    text: str = Field(..., description="修订词文本（原样返回）")
    speaker: str = Field(..., description="修订词说话人（原样返回）")
    source_word_index: int | None = Field(
        ...,
        description="匹配到的原稿词序号（0-based），命中词沿用该原稿词的时间；"
        "未匹配的修订词为 null（时间由相邻锚点间隔均分得出）",
    )


class TranscriptRetimeResponse(BaseModel):
    words: list[TranscriptRetimeWordOut] = Field(
        ..., description="按修订顺序返回的完整逐词时间"
    )
    matched_count: int = Field(
        ..., description="保序匹配命中的词对数（未匹配词数为修订总数减去该值）"
    )


class QaPairsRequest(BaseModel):
    words: list[WordIn] = Field(
        ...,
        min_length=1,
        max_length=300,
        description="按时间排列的词序列（至多 300 词）：相邻同一说话人合并为发言轮次",
    )
    max_gap_ms: int = Field(
        ...,
        ge=0,
        description="回答首词 start_ms 与问题末词 end_ms 的最大间隔（毫秒，非负整数）",
    )


class QaTurnOut(BaseModel):
    start_word: int = Field(..., description="轮次首词序号（0-based，含）")
    end_word: int = Field(..., description="轮次末词序号（0-based，含）")
    start_ms: int = Field(..., description="轮次起始时间：首词 start_ms")
    end_ms: int = Field(..., description="轮次结束时间：末词 end_ms")
    speaker: str = Field(..., description="轮次说话人（轮内所有词相同）")
    text: str = Field(..., description="轮次文本：词 text 原样拼接，不插空格")


class QaPairOut(BaseModel):
    index: int
    question: QaTurnOut = Field(..., description="提问轮次（去尾部空白后以 ？/? 结尾）")
    answer: QaTurnOut = Field(..., description="配对回答轮次（其他说话人）")


class QaPairsResponse(BaseModel):
    pairs: list[QaPairOut] = Field(
        ..., description="按问题时间升序的问答对；无候选时为空数组"
    )
    unpaired_question_start_words: list[int] = Field(
        ..., description="未配上回答的提问首词序号（0-based，升序）"
    )


class GainFrameIn(BaseModel):
    start_ms: int = Field(..., ge=0, description="测量帧起始时间（毫秒，非负整数）")
    end_ms: int = Field(..., ge=0, description="测量帧结束时间（毫秒，须严格大于 start_ms）")
    loudness_lufs: int = Field(
        ..., ge=-80, le=0, description="帧积分响度（LUFS，-80～0 的整数）"
    )
    true_peak_dbfs: int = Field(
        ..., ge=-80, le=0, description="帧真峰值（dBTP/dBFS，-80～0 的整数）"
    )


class GainPlanRequest(BaseModel):
    frames: list[GainFrameIn] = Field(
        ...,
        min_length=1,
        max_length=300,
        description="按时间排列且互不重叠的测量帧（1～300 个，允许端点相接）",
    )
    target_lufs: int = Field(
        ..., ge=-40, le=0, description="目标响度（LUFS，-40～0 的整数）"
    )
    max_step_db: int = Field(
        ..., ge=0, le=12, description="相邻帧增益差绝对值上限（0～12 的整数 dB）"
    )


class GainFrameOut(BaseModel):
    index: int
    gain_db: int = Field(
        ..., description="该帧增益（-12～12 的整数 dB）"
    )
    resulting_loudness_lufs: int = Field(
        ..., description="增益后响度：loudness_lufs + gain_db"
    )
    resulting_true_peak_dbfs: int = Field(
        ..., description="增益后真峰值：true_peak_dbfs + gain_db（<= 0）"
    )


class GainPlanResponse(BaseModel):
    frames: list[GainFrameOut] = Field(..., description="逐帧增益（按输入帧顺序）")
    total_cost: int = Field(
        ...,
        description="最小总成本：Σ|loudness_lufs+gain_db-target_lufs| "
        "+ 2·Σ|相邻 gain_db 之差|",
    )
