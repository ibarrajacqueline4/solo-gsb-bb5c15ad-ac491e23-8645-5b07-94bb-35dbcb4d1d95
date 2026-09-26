"""敏感短语遮罩预览核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

把每个**同一说话人连续区间**内所有词的 text 原样拼接（不插空格），在其中
查找全部敏感短语的精确命中（按 Unicode code point 比较）：命中可跨词，但
不得跨越说话人边界；同一文本中的重叠出现各自算一条候选命中（如短语 "aa"
在 "aaa" 中有 2 条候选）。重叠命中按“起点靠前优先、同起点短语较长优先”
的优先级贪心选出互不重叠的命中集合，仅把选中字符**逐字**替换为 "█"
（每个字符一个 █），词序、时间与说话人保持不变。

算法（按字符起点保留优先候选 + 顺序贪心选取）：

* 候选：对每个说话人连续区间、每条短语，用 str.find 枚举全部出现起点
  （每次从 pos + 1 继续，允许重叠出现）；区间长度为 L 时开长度 L 的
  best_by_start 数组，每个字符起点只保留裁决优先级最高的一条候选
  （长度降序、短语输入序号升序），其余重叠候选即时丢弃、不入内存；
* 选择：按起点 pos = 0..L-1 顺序扫描，某起点存在候选且起点不早于已选
  命中右端 last_end 时即选中该候选——同起点只留最优候选，而任何被跳过
  的起点都与某个优先级更高（起点更早）的已选命中重叠，与“先排序再
  单趟扫描”结果完全一致；
* 映射：命中区间 [pos, pos+len) 用二分定位首末词（run_offsets 为区间内
  各词首字符的偏移），得到首末词序号与两端词内字符偏移：首词内
  [start_char, 词末)、末词内 [0, end_char)，跨词命中覆盖中间词的全部字符；
* 复杂度：O(P·T + T) 时间、O(T) 额外工作内存，P 为短语数（≤ 50）、
  T 为全量拼接文本总字符数（另需短语自身的 O(短语总长度)）；额外内存
  不随重叠命中总数 H 增长——高重复文本（如长串 "a" 配短语 "a"/"aa"/…）
  下 H 可达短语数 × 文本长度，旧实现把全部候选存表排序会随之放大内存。
"""

from bisect import bisect_right

from .segmentation import validate_words

REDACT_CHAR = "█"  # 逐字替换使用的遮罩字符


def redact_preview(words, phrases):
    """对命中敏感短语的字符做遮罩预览，返回 (redacted_words, hits)。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker
    （沿用同一套词序与时间校验）；phrases 为 1～50 个非空字符串。

    redacted_words 与输入等长、词序一致，每项为 dict：
    start_ms / end_ms / text / speaker，仅 text 中命中字符被逐字替换为 "█"。
    hits 按命中位置升序，每项为 dict：index / phrase / start_word / end_word /
    start_char / end_char；start_char 为命中首字符在首词内的 Unicode 字符偏移
    （0-based，含），end_char 为命中末字符在末词内的偏移区间右端（不含）。
    无命中时 hits 为空、redacted_words 文本与输入一致。
    """
    validate_words(words)
    n = len(words)
    char_counts = [len(w.text) for w in words]

    # word_redactions[i]：词 i 内需替换的字符区间列表（[start, end)，互不重叠且有序）
    word_redactions = [[] for _ in range(n)]
    hits = []

    run_start = 0
    for i in range(1, n + 1):
        if i < n and words[i].speaker == words[i - 1].speaker:
            continue
        run_end = i
        run_text = "".join(words[k].text for k in range(run_start, run_end))
        run_offsets = [0]
        for k in range(run_start, run_end - 1):
            run_offsets.append(run_offsets[-1] + char_counts[k])

        # ---- 候选：每个字符起点只保留裁决优先级最高的一条（长度降序、
        # 短语输入序号升序）；被比掉的候选即时丢弃，不随重叠命中数放大内存 ----
        run_len = len(run_text)
        best_by_start = [-1] * run_len  # 起点 -> 该起点最优候选的短语序号
        for phrase_index, phrase in enumerate(phrases):
            length = len(phrase)
            pos = run_text.find(phrase)
            while pos >= 0:
                prev = best_by_start[pos]
                if prev < 0 or length > len(phrases[prev]):
                    best_by_start[pos] = phrase_index
                pos = run_text.find(phrase, pos + 1)

        # ---- 按起点顺序贪心选出互不重叠的命中 ----
        last_end = 0
        for pos in range(run_len):
            phrase_index = best_by_start[pos]
            if phrase_index < 0 or pos < last_end:
                continue  # 该起点无候选，或与优先级更高的已选命中重叠
            end = pos + len(phrases[phrase_index])
            last_end = end

            first_ptr = bisect_right(run_offsets, pos) - 1
            last_ptr = bisect_right(run_offsets, end - 1) - 1
            first_word = run_start + first_ptr
            last_word = run_start + last_ptr
            start_char = pos - run_offsets[first_ptr]
            end_char = end - run_offsets[last_ptr]

            if first_word == last_word:
                word_redactions[first_word].append((start_char, end_char))
            else:
                word_redactions[first_word].append(
                    (start_char, char_counts[first_word])
                )
                for k in range(first_word + 1, last_word):
                    word_redactions[k].append((0, char_counts[k]))
                word_redactions[last_word].append((0, end_char))

            hits.append(
                {
                    "index": len(hits),
                    "phrase": phrases[phrase_index],
                    "start_word": first_word,
                    "end_word": last_word,
                    "start_char": start_char,
                    "end_char": end_char,
                }
            )
        run_start = run_end

    redacted_words = []
    for i, w in enumerate(words):
        text = w.text
        if word_redactions[i]:
            chars = list(text)
            for start, end in word_redactions[i]:
                chars[start:end] = REDACT_CHAR * (end - start)
            text = "".join(chars)
        redacted_words.append(
            {
                "start_ms": w.start_ms,
                "end_ms": w.end_ms,
                "text": text,
                "speaker": w.speaker,
            }
        )
    return redacted_words, hits
