"""SRT 字幕渲染。"""


def _format_timestamp(ms: int) -> str:
    """毫秒 -> SRT 时间戳 HH:MM:SS,mmm（超过 1 小时自然进位）。"""
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def render_srt(segments) -> str:
    """将分段结果渲染为 SRT 文本。文本原样输出，不插入额外空格。"""
    blocks = []
    for number, seg in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                [
                    str(number),
                    f"{_format_timestamp(seg['start_ms'])} --> "
                    f"{_format_timestamp(seg['end_ms'])}",
                    seg["text"],
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"
