"""访谈问答索引核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

把按时间排列且互不重叠的词序列（至多 300 词）先合并为发言轮次：**相邻且
同一 speaker** 的词合并成一轮（即 speaker 连续区间），轮文本为词 text 原样
拼接（不插空格）。轮文本去掉**尾部空白**（Unicode 空白，``str.rstrip``）后
以 ``？`` 或 ``?`` 结尾者视为**提问**，其余轮次均可作为**回答**候选。

一条提问 q 与一条回答 a 构成候选对当且仅当：

* **其他说话人**：``a.speaker != q.speaker``；
* **开始于问题结束后**：``a.start_ms >= q.end_ms``（输入词互不重叠，端点
  相接允许，故该条件已蕴含回答在时间上不早于问题、二者不重叠）；
* **间隔不超过 max_gap_ms**：``0 <= a.start_ms - q.end_ms <= max_gap_ms``
  （max_gap_ms 为非负整数）。

在全部候选上求一个**保序一对一匹配**（每条提问至多配一条回答、每条回答至多
配一条提问，配对双方的轮次顺序都严格递增，即最长保序匹配 / LCS 式）：

* 目标一：配对数最大；
* 目标二：同配对数时，Σ 回答文本字符数（Unicode code point，按返回的原样
  拼接文本计）最大；
* 目标三：仍同分时取 ``(问题首词序号, 回答首词序号)`` 配对序列**字典序最小**
  者——逐对比较，先比问题首词序号、相同再比回答首词序号。

算法（后向 DP + 贪心重建），O(p·r) 时间/空间（p、r 为提问与回答轮次数，
p + r 为总轮次数 ≤ 词数 ≤ 300）：

* ``dp[i][j]`` 为提问后缀 [i,p) 与回答后缀 [j,r) 上的最优值
  ``(配对数, 回答字符总数)``（元组比较先比配对数）；转移为“跳过提问”
  “跳过回答”与“取候选对 (i,j) + dp[i+1][j+1]”三者取最大，边界全为 (0,0)；
* 重建时在当前子矩形 ``[i0,p)×[j0,r)`` 内按问题序号升序、同问题按回答序号
  升序，找第一个满足“取该对 + 其后缀最优”恰等于当前最优值的候选对——枚举
  顺序先问题后回答即取 (问题首词序号, 回答首词序号) 字典序最小的可行首对；
  取对后子矩形严格缩小，归纳可得整条配对序列字典序最小。

无任何候选对时配对为空，所有提问首词序号进入未配对列表；没有提问轮次时
两者皆空。
"""

from .segmentation import validate_words

MAX_QA_WORDS = 300  # 词数上限（与 Pydantic max_length 保持一致）
QUESTION_MARKS = frozenset("？?")  # 去尾部空白后以此结尾即为提问


def _merge_turns(words):
    """把相邻同一说话人的词合并为发言轮次。

    返回轮次列表，每项为 dict：start_word / end_word（0-based、含端点）、
    start_ms / end_ms / speaker / text / char_count。
    """
    n = len(words)
    turns = []
    run_start = 0
    for i in range(1, n + 1):
        if i == n or words[i].speaker != words[run_start].speaker:
            chunk = words[run_start:i]
            text = "".join(w.text for w in chunk)
            turns.append(
                {
                    "start_word": run_start,
                    "end_word": i - 1,
                    "start_ms": words[run_start].start_ms,
                    "end_ms": words[i - 1].end_ms,
                    "speaker": words[run_start].speaker,
                    "text": text,
                    "char_count": len(text),
                }
            )
            run_start = i
    return turns


def _is_question(turn):
    """轮文本去尾部空白后以全角/半角问号结尾即视为提问。"""
    stripped = turn["text"].rstrip()
    return bool(stripped) and stripped[-1] in QUESTION_MARKS


def build_qa_pairs(words, max_gap_ms):
    """生成可回听的问答索引，返回 (pairs, unpaired_question_start_words)。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker
    （沿用同一套词序与时间校验：按时间排列、不重叠）；max_gap_ms 为非负整数。

    pairs 按问题时间（首词序号）升序，每项为 dict：index / question / answer，
    question、answer 各为含 start_word / end_word / start_ms / end_ms /
    speaker / text 的轮次 dict。unpaired_question_start_words 为未配上回答的
    提问首词序号（0-based、升序）。
    """
    validate_words(words)

    turns = _merge_turns(words)
    questions = [t for t in turns if _is_question(t)]
    answers = [t for t in turns if not _is_question(t)]
    p = len(questions)
    r = len(answers)

    # ---- 候选边：其他说话人、回答始于问题结束后、间隔 <= max_gap_ms ----
    # edge[i][j] 为真时提问 i 可配回答 j；回答权重为其原样文本字符数。
    edge = [[False] * r for _ in range(p)]
    for i, q in enumerate(questions):
        q_end = q["end_ms"]
        q_speaker = q["speaker"]
        row = edge[i]
        for j, a in enumerate(answers):
            gap = a["start_ms"] - q_end
            if a["speaker"] != q_speaker and 0 <= gap <= max_gap_ms:
                row[j] = True

    # ---- 后向 DP：dp[i][j] = 后缀 [i,p)×[j,r) 的 (最大配对数, 最大字符数) ----
    dp = [[(0, 0)] * (r + 1) for _ in range(p + 1)]
    for i in range(p - 1, -1, -1):
        cur_row = dp[i]
        next_row = dp[i + 1]
        edge_row = edge[i]
        for j in range(r - 1, -1, -1):
            best = next_row[j]          # 跳过提问 i
            skip_a = cur_row[j + 1]     # 跳过回答 j
            if skip_a > best:
                best = skip_a
            if edge_row[j]:
                tail_count, tail_chars = next_row[j + 1]
                take = (tail_count + 1, tail_chars + answers[j]["char_count"])
                if take > best:
                    best = take
            cur_row[j] = best

    # ---- 贪心重建 (问题首词序号, 回答首词序号) 字典序最小的最优配对 ----
    selected = []  # (提问下标 i, 回答下标 j)
    i0 = j0 = 0
    target = dp[0][0]
    while target[0] > 0:
        found = None
        for i in range(i0, p):
            edge_row = edge[i]
            for j in range(j0, r):
                if not edge_row[j]:
                    continue
                tail_count, tail_chars = dp[i + 1][j + 1]
                take = (tail_count + 1, tail_chars + answers[j]["char_count"])
                if take == target:
                    found = (i, j)
                    break
            if found is not None:
                break
        i, j = found  # target[0] > 0 时必存在可行对
        selected.append((i, j))
        i0, j0 = i + 1, j + 1  # 后续扫描矩形严格缩小
        target = dp[i0][j0]

    matched_questions = {i for i, _ in selected}
    pairs = [
        {
            "index": k,
            "question": _turn_out(questions[i]),
            "answer": _turn_out(answers[j]),
        }
        for k, (i, j) in enumerate(selected)
    ]
    unpaired = [
        questions[i]["start_word"]
        for i in range(p)
        if i not in matched_questions
    ]
    return pairs, unpaired


def _turn_out(turn):
    """对外输出的轮次字段：词区间、时间区间、说话人与原样拼接文本。"""
    return {
        "start_word": turn["start_word"],
        "end_word": turn["end_word"],
        "start_ms": turn["start_ms"],
        "end_ms": turn["end_ms"],
        "speaker": turn["speaker"],
        "text": turn["text"],
    }
