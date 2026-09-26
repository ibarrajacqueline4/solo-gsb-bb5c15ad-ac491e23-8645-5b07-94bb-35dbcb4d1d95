"""修订稿重新对齐词时间核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

输入按时间排列且互不重叠的原稿 words 与修订词 revisions（仅 text/speaker，
两侧各不超过 300 词），以整词 ``(text, speaker)`` 为符号在两侧之间求一个
**保序匹配**（不要求连续，即最长公共子序列 LCS）：

* 目标一：匹配词对数量最大（LCS 长度）；
* 目标二：同长时取匹配索引对序列 ``((原稿 i, 修订 j), ...)`` **字典序最小**者。

命中词保留所匹配原稿词的原 ``start_ms`` / ``end_ms``；未命中的**连续修订词**
（一个 run）在相邻锚点之间均分间隔：

* 中段 run 夹在命中词 L（时间 ``L.end_ms``）与命中词 R（时间 ``R.start_ms``）
  之间，可分间隔为 ``R.start_ms - L.end_ms``；
* 前置 run 以**原稿首词 start_ms** 为左虚拟锚点、第一个命中词 start_ms 为右锚点；
* 后置 run 以最后一个命中词 end_ms 为左锚点、**原稿末词 end_ms** 为右虚拟锚点；
* 完全没有命中时，全部修订词在 ``[首词 start_ms, 末词 end_ms)`` 全长内均分。

c 个词均分长度 span：每词时长 ``base = span // c`` 毫秒、余数
``rem = span % c`` 从前往后分给前 rem 个词（各多 1 毫秒）。某 run 不足每词
1 毫秒（``span < c``）时抛出 DomainError(code="infeasible_retime")（HTTP 422），
并指出该 run 的修订词范围 ``[revision_start_index, revision_end_index]``。

算法（LCS 后向 DP + 贪心重建），O(n·m) 时间/空间：

* ``lcs[i][j]`` 为后缀 A[i:]、B[j:] 的 LCS 长度，标准后向递推；
* 重建时在当前子矩形 ``[i0,n)×[j0,m)`` 内按 i 升序、同 i 按 j 升序找第一个
  满足 ``A[i] == B[j]`` 且 ``1 + lcs[i+1][j+1] == lcs[i0][j0]`` 的格子。
  条件要求“选该对 + 其后缀最优”恰好补得当前子问题的最优长度——枚举顺序
  先 i 后 j 即取索引对字典序最小的可行首对；归纳可得整条索引对序列字典序最小。
"""

from .errors import DomainError
from .segmentation import validate_words


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def _lexicographically_smallest_lcs(keys_a, keys_b, lcs):
    """按索引对序列字典序最小的要求重建一个最长公共子序列。

    返回 [(原稿 i, 修订 j), ...]（两列均严格升序）。lcs[i][j] 为后缀
    A[i:]/B[j:] 的 LCS 长度表（lcs[n][*] 与 lcs[*][m] 均为 0）。
    """
    n = len(keys_a)
    m = len(keys_b)
    pairs = []
    i0 = j0 = 0
    target = lcs[0][0]
    while target > 0:
        found = None
        # 在当前子矩形 [i0,n)×[j0,m) 内按 i 升序、同 i 按 j 升序找第一个
        # “取该对后其后缀还能补满 target”的格子：可行条件
        # 1 + lcs[i+1][j+1] == target 同时保证 A[i] == B[j]（不相等时
        # 不会走对角 +1 支）。枚举顺序即取索引对字典序最小的可行首对。
        for i in range(i0, n):
            for j in range(j0, m):
                if keys_a[i] == keys_b[j] and 1 + lcs[i + 1][j + 1] == target:
                    found = (i, j)
                    break
            if found is not None:
                break
        i, j = found  # target > 0 时必有可行对
        pairs.append((i, j))
        i0, j0 = i + 1, j + 1  # 后续扫描矩形严格缩小，全程累计访问 O(n·m)
        target -= 1
    return pairs


def retime_transcript(words, revisions):
    """为修订词序列重新计算逐词时间，返回 (out_words, matched_count)。

    参数 words 中每个对象需具备属性 start_ms / end_ms / text / speaker
    （沿用同一套词序与时间校验：按时间排列、不重叠）；revisions 中每个对象
    需具备属性 text / speaker。

    out_words 按修订顺序返回，每项为 dict：
    index / start_ms / end_ms / text / speaker / source_word_index
    （source_word_index 为命中原稿词序号 0-based，未命中为 None）。
    matched_count 为命中词对数。

    某连续未命中 run 的可用间隔不足每词 1 毫秒时抛出
    DomainError(code="infeasible_retime")（HTTP 422），附加字段
    revision_start_index / revision_end_index（0-based、含端点）、
    revision_word_count、available_ms、required_ms。
    """
    validate_words(words)
    n = len(words)
    m = len(revisions)

    keys_a = [(w.text, w.speaker) for w in words]
    keys_b = [(r.text, r.speaker) for r in revisions]

    # ---- 后向 LCS DP：lcs[i][j] = 后缀 A[i:]/B[j:] 的最长匹配长度 ----
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row = lcs[i]
        next_row = lcs[i + 1]
        ai = keys_a[i]
        for j in range(m - 1, -1, -1):
            if ai == keys_b[j]:
                row[j] = next_row[j + 1] + 1
            else:
                row[j] = max(next_row[j], row[j + 1])

    pairs = _lexicographically_smallest_lcs(keys_a, keys_b, lcs)
    matched_count = len(pairs)

    full_start = words[0].start_ms     # 前置 run / 无匹配时的左虚拟锚点
    full_end = words[n - 1].end_ms     # 后置 run / 无匹配时的右虚拟锚点

    out = [None] * m

    def fill_run(r_start, r_end, left_ms, right_ms):
        """把修订区间 [r_start, r_end]（含端点）在 [left_ms, right_ms) 内均分。"""
        count = r_end - r_start + 1
        span = right_ms - left_ms
        if span < count:
            _fail(
                "infeasible_retime",
                f"修订词第 {r_start}～{r_end} 个（共 {count} 个）的相邻锚点间隔仅 "
                f"{span} 毫秒，不足每词至少 1 毫秒（需 {count} 毫秒）",
                revision_start_index=r_start,
                revision_end_index=r_end,
                revision_word_count=count,
                available_ms=span,
                required_ms=count,
            )
        base, rem = divmod(span, count)
        cursor = left_ms
        for k in range(count):
            duration = base + (1 if k < rem else 0)  # 余下毫秒从前往后分配
            r = revisions[r_start + k]
            out[r_start + k] = {
                "index": r_start + k,
                "start_ms": cursor,
                "end_ms": cursor + duration,
                "text": r.text,
                "speaker": r.speaker,
                "source_word_index": None,
            }
            cursor += duration

    if matched_count == 0:
        # 无任何匹配：全部修订词在原稿全长内均分。
        fill_run(0, m - 1, full_start, full_end)
    else:
        first_src, first_rev = pairs[0]
        if first_rev > 0:
            fill_run(0, first_rev - 1, full_start, words[first_src].start_ms)

        for (src_l, rev_l), (src_r, rev_r) in zip(pairs, pairs[1:]):
            if rev_r > rev_l + 1:
                fill_run(
                    rev_l + 1,
                    rev_r - 1,
                    words[src_l].end_ms,
                    words[src_r].start_ms,
                )

        last_src, last_rev = pairs[-1]
        if last_rev < m - 1:
            fill_run(last_rev + 1, m - 1, words[last_src].end_ms, full_end)

        for i, j in pairs:
            w = words[i]
            out[j] = {
                "index": j,
                "start_ms": w.start_ms,
                "end_ms": w.end_ms,
                "text": revisions[j].text,
                "speaker": revisions[j].speaker,
                "source_word_index": i,
            }

    return out, matched_count
