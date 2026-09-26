"""FastAPI 入口：POST /segments 字幕分段、POST /ad-breaks 广告插播点、
POST /speaker-reconcile 说话人校正、POST /chapter-align 章节时刻对齐、
POST /duplicate-passages 重复口播片段检测、POST /redact-preview 敏感短语
遮罩预览、POST /trailer-plan 预告片片段组合、POST /playback-plan 播放变速
规划、POST /crosstalk-report 叠话统计报告、POST /transcript-retime 修订稿
重新对齐逐词时间、POST /qa-pairs 访谈问答可回听索引、POST /gain-plan
响度增益规划服务。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .ad_breaks import optimal_ad_breaks
from .chapter_align import align_chapters
from .crosstalk_report import build_crosstalk_report
from .duplicate_passages import find_duplicate_passages
from .errors import DomainError
from .gain_plan import plan_gain
from .playback_plan import plan_playback
from .qa_pairs import build_qa_pairs
from .redact_preview import redact_preview
from .schemas import (
    AdBreakRequest,
    AdBreakResponse,
    ChapterAlignRequest,
    ChapterAlignResponse,
    CrosstalkReportRequest,
    CrosstalkReportResponse,
    DuplicatePassagesRequest,
    DuplicatePassagesResponse,
    GainPlanRequest,
    GainPlanResponse,
    PlaybackPlanRequest,
    PlaybackPlanResponse,
    QaPairsRequest,
    QaPairsResponse,
    RedactPreviewRequest,
    RedactPreviewResponse,
    SegmentRequest,
    SegmentResponse,
    SpeakerReconcileRequest,
    SpeakerReconcileResponse,
    TrailerPlanRequest,
    TrailerPlanResponse,
    TranscriptRetimeRequest,
    TranscriptRetimeResponse,
)
from .segmentation import optimal_segmentation
from .speaker_reconcile import reconcile_speakers
from .srt import render_srt
from .trailer_plan import plan_trailer
from .transcript_retime import retime_transcript

app = FastAPI(
    title="Podcast Subtitle Segmentation API",
    description="播客转写后处理：在硬约束下用动态规划求最优字幕分段（JSON 与 SRT），"
    "在词间静音处选择最优广告插播点，在人工锁定约束下校正说话人标注，"
    "按章节 cue 文本命中把章节对齐到词时间轴，"
    "检测词序列完全相同的重复口播片段，"
    "对命中敏感短语的字符生成逐字遮罩预览，"
    "在词区间不重叠与时长预算约束下组合必选说话人全覆盖的预告片片段，"
    "把连续同一说话人的词合并为语速块，在目标最长播放时长内选出"
    "总变速代价最小的语速方案，"
    "对允许乱序与交叠的词序列统计各说话人发声覆盖、"
    "至少两人同时发声的叠话区间与各说话人对的总重叠毫秒数，"
    "并按 (text, speaker) 的最长保序匹配把修订稿锚定到原稿词时间轴、"
    "为未匹配修订词在相邻锚点间隔内均分时间，"
    "以及把相邻同一说话人的词合并为发言轮次、"
    "在其他说话人且间隔受限的回答中为每个提问保序一对一配对、"
    "生成可回听的问答索引；并在峰值安全与相邻增益变化受限下，"
    "为逐帧响度测量选择命中目标响度且总代价最小的整数 dB 增益序列。",
    version="1.12.0",
)


def _error_response(code: str, message: str, extra: dict | None = None):
    detail = {"code": code, "message": message}
    if extra:
        detail.update(extra)
    return JSONResponse(status_code=422, content={"error": detail})


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return _error_response(exc.code, exc.message, exc.extra)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return _error_response(
        "invalid_request",
        "请求体不合法：words 必须为非空数组，每项含非负整数 start_ms/end_ms、"
        "非空字符串 text/speaker；/ad-breaks 另需 break_count∈[1,5]、"
        "min_silence_ms≥1、min_spacing_ms≥0；/speaker-reconcile 另需 "
        "switch_cost∈[0,10]，locks 每项含整数 word_index 与非空 speaker；"
        "/chapter-align 另需非空 chapters（每项含非空 title/cue、非负整数 "
        "approx_ms）与正整数 min_gap_ms；/duplicate-passages 另需 "
        "min_words∈[2,20]、非负整数 min_gap_ms，且 words 至多 20000 个；"
        "/redact-preview 另需 phrases 为 1～50 个非空字符串，"
        "且 words 至多 2000 个；/trailer-plan 另需 1～20 个 candidates"
        "（每项含整数 start_word/end_word 与非负整数 score）、非负整数 "
        "budget_ms 与 1～3 个非空 required_speakers；/playback-plan 另需"
        "非负整数 max_duration_ms；/crosstalk-report 的 words 至多 2000 个；"
        "/transcript-retime 另需非空 revisions（每项含非空 text/speaker），"
        "words 与 revisions 各不超过 300 个；/qa-pairs 另需非负整数 "
        "max_gap_ms，words 不超过 300 个；/gain-plan 另需 1～300 个 frames"
        "（每项含非负整数 start_ms/end_ms 且 start_ms < end_ms、"
        "-80～0 的整数 loudness_lufs/true_peak_dbfs）、"
        "-40～0 的整数 target_lufs、0～12 的整数 max_step_db",
        {"errors": exc.errors()},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/segments", response_model=SegmentResponse)
async def create_segments(request: SegmentRequest):
    words = request.words  # Pydantic 模型自带所需属性
    segments, total_score = optimal_segmentation(words)
    return SegmentResponse(
        segments=segments,
        total_score=total_score,
        srt=render_srt(segments),
    )


@app.post("/ad-breaks", response_model=AdBreakResponse)
async def create_ad_breaks(request: AdBreakRequest):
    breaks, total_silence_ms = optimal_ad_breaks(
        request.words,
        request.break_count,
        request.min_silence_ms,
        request.min_spacing_ms,
    )
    return AdBreakResponse(breaks=breaks, total_silence_ms=total_silence_ms)


@app.post("/speaker-reconcile", response_model=SpeakerReconcileResponse)
async def create_speaker_reconcile(request: SpeakerReconcileRequest):
    words, changed_word_indices, total_cost = reconcile_speakers(
        request.words,
        request.locks,
        request.switch_cost,
    )
    return SpeakerReconcileResponse(
        words=words,
        changed_word_indices=changed_word_indices,
        total_cost=total_cost,
    )


@app.post("/chapter-align", response_model=ChapterAlignResponse)
async def create_chapter_align(request: ChapterAlignRequest):
    chapters, total_deviation_ms = align_chapters(
        request.words,
        request.chapters,
        request.min_gap_ms,
    )
    return ChapterAlignResponse(
        chapters=chapters,
        total_deviation_ms=total_deviation_ms,
    )


@app.post("/duplicate-passages", response_model=DuplicatePassagesResponse)
async def create_duplicate_passages(request: DuplicatePassagesRequest):
    match = find_duplicate_passages(
        request.words,
        request.min_words,
        request.min_gap_ms,
    )
    return DuplicatePassagesResponse(match=match)


@app.post("/redact-preview", response_model=RedactPreviewResponse)
async def create_redact_preview(request: RedactPreviewRequest):
    words, hits = redact_preview(request.words, request.phrases)
    return RedactPreviewResponse(words=words, hits=hits)


@app.post("/trailer-plan", response_model=TrailerPlanResponse)
async def create_trailer_plan(request: TrailerPlanRequest):
    clips, total_score, total_duration_ms = plan_trailer(
        request.words,
        request.candidates,
        request.budget_ms,
        request.required_speakers,
    )
    return TrailerPlanResponse(
        clips=clips,
        total_score=total_score,
        total_duration_ms=total_duration_ms,
    )


@app.post("/playback-plan", response_model=PlaybackPlanResponse)
async def create_playback_plan(request: PlaybackPlanRequest):
    blocks, total_cost, total_duration_ms = plan_playback(
        request.words,
        request.max_duration_ms,
    )
    return PlaybackPlanResponse(
        blocks=blocks,
        total_cost=total_cost,
        total_duration_ms=total_duration_ms,
    )


@app.post("/crosstalk-report", response_model=CrosstalkReportResponse)
async def create_crosstalk_report(request: CrosstalkReportRequest):
    speakers, segments, pair_overlaps = build_crosstalk_report(request.words)
    return CrosstalkReportResponse(
        speakers=speakers,
        segments=segments,
        pair_overlaps=pair_overlaps,
    )


@app.post("/transcript-retime", response_model=TranscriptRetimeResponse)
async def create_transcript_retime(request: TranscriptRetimeRequest):
    words, matched_count = retime_transcript(request.words, request.revisions)
    return TranscriptRetimeResponse(words=words, matched_count=matched_count)


@app.post("/qa-pairs", response_model=QaPairsResponse)
async def create_qa_pairs(request: QaPairsRequest):
    pairs, unpaired = build_qa_pairs(request.words, request.max_gap_ms)
    return QaPairsResponse(
        pairs=pairs,
        unpaired_question_start_words=unpaired,
    )


@app.post("/gain-plan", response_model=GainPlanResponse)
async def create_gain_plan(request: GainPlanRequest):
    frames, total_cost = plan_gain(
        request.frames,
        request.target_lufs,
        request.max_step_db,
    )
    return GainPlanResponse(frames=frames, total_cost=total_cost)
