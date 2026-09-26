"""重复口播片段检测核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

以每个词的 (text, speaker) 组成符号序列，查找两处词序列完全相同的连续
口播片段：两处词索引区间不重叠，且两处首词的 start_ms 至少相隔
min_gap_ms。单词文本不拆分——匹配以整词为最小单位。只返回词数最多的
一对；同长度时按两处起始词索引的二元组 (i, j)（i < j）字典序裁决。
最佳词数不足 min_words 时返回 None。

算法（后缀数组索引 + LCP 分组 + 答案二分，确定性、无随机/哈希），
最坏 O(n log² n) 时间、O(n) 额外空间（n 为词数，≤ 20000）；所有数组
均按词数分配，不按时间轴毫秒跨度分配：

* 符号压缩：把 (text, speaker) 排序去重映射为整数 id，O(n log n)；
* 后缀数组：倍增法按 (rank[i], rank[i+k]) 逐轮排序，O(log n) 轮、
  每轮 O(n log n)，合计 O(n log² n)；LCP 用 Kasai O(n) 求出。
  lcp(i, j) ≥ L 当且仅当两后缀落在后缀数组中同一个“相邻 LCP 均 ≥ L”
  的分组内；
* 起点对 (i, j)（i < j）的可用长度 usable = min(lcp(i,j), j-i)：区间
  不重叠要求片段长 L ≤ j-i；另需首词时间间隔
  start_ms[j] - start_ms[i] ≥ min_gap_ms。词时间非降，故可用双指针
  O(n) 预求 g[i] = 首个满足 start_ms ≥ start_ms[i] + min_gap_ms 的词
  下标，把长度 L 下的合法条件化为纯下标约束 j ≥ f(i) = max(i+L, g[i])；
  f 随 i 非降，故一组起点集合 S 可行当且仅当 f(min S) ≤ max S，
  单趟分组扫描 O(n) 完成一次可行性判定；
* 可用长度随 L 单调（L 可行则更小也可行），二分最大可行长度，
  合计 O(n log n)；
* 最优长度 L* 确定后按组内升序列表重建：i 升序枚举，第一个在组内
  存在 j ≥ f(i) 的 i 即最优首词，组内最小这样的 j 即最优次词，
  得字典序最小起点对；最后校验最优长度 ≥ min_words。
"""

from bisect import bisect_left

from .segmentation import validate_words


def _build_suffix_array(ids):
    """倍增法构造后缀数组，返回 sa（sa[p] 为名次 p 的后缀起点下标）。

    第 k 轮按 (rank[i], rank[i+k]) 二元组排序，得到长度 2k 前缀的名次；
    名次全部互不相同（前缀已覆盖整串）时结束。O(log n) 轮、每轮一次
    O(n log n) 排序，合计 O(n log² n) 时间、O(n) 空间。
    """
    n = len(ids)
    sa = list(range(n))
    rank = list(ids)
    k = 1
    while True:
        sa.sort(key=lambda i: (rank[i], rank[i + k] if i + k < n else -1))
        new_rank = [0] * n
        for p in range(1, n):
            a, b = sa[p - 1], sa[p]
            key_a = (rank[a], rank[a + k] if a + k < n else -1)
            key_b = (rank[b], rank[b + k] if b + k < n else -1)
            new_rank[b] = new_rank[a] + (key_a < key_b)
        rank = new_rank
        if rank[sa[-1]] == n - 1:
            return sa
        k <<= 1


def _build_lcp(ids, sa):
    """Kasai 算法求 LCP：lcp[p] 为后缀 sa[p] 与 sa[p+1] 的最长公共前缀长度。

    O(n) 时间、O(n) 空间。
    """
    n = len(ids)
    rank = [0] * n
    for p in range(n):
        rank[sa[p]] = p
    lcp = [0] * (n - 1)
    h = 0
    for i in range(n):
        r = rank[i]
        if r == n - 1:
            h = 0
            continue
        j = sa[r + 1]
        while i + h < n and j + h < n and ids[i + h] == ids[j + h]:
            h += 1
        lcp[r] = h
        if h:
            h -= 1
    return lcp


def find_duplicate_passages(words, min_words, min_gap_ms):
    """查找词数最多的重复口播片段对，返回 dict 或 None。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker
    （沿用同一套词序与时间校验）。

    返回 dict：word_count / text / first / second；first/second 各含
    start_word / end_word（0-based、含端点）与 start_ms / end_ms，
    first 为起始词索引较小的一处；text 为共同文本（词 text 原样拼接，
    不插空格）。不存在词数 ≥ min_words 的合法片段对时返回 None。
    """
    validate_words(words)
    n = len(words)
    starts = [w.start_ms for w in words]
    # 快捷判空：词数不足以容纳两段不重叠的 min_words 片段，
    # 或全序列首末词的时间间隔都达不到 min_gap_ms。
    if n < 2 * min_words or starts[-1] - starts[0] < min_gap_ms:
        return None

    # 整词符号压缩为整数 id，供后缀数组按整数比较
    tokens = [(w.text, w.speaker) for w in words]
    token_id = {tok: k for k, tok in enumerate(sorted(set(tokens)))}
    ids = [token_id[tok] for tok in tokens]

    # g[i]：首个满足 starts[t] >= starts[i] + min_gap_ms 的下标 t（无则为 n）。
    # starts 非降使阈值随 i 非降，双指针单趟 O(n)。
    g = [0] * n
    t = 0
    for i in range(n):
        limit = starts[i] + min_gap_ms
        while t < n and starts[t] < limit:
            t += 1
        g[i] = t

    sa = _build_suffix_array(ids)
    lcp = _build_lcp(ids, sa)

    # 长度 L 可行：存在 i < j 使 lcp(i,j) >= L、j - i >= L 且首词间隔达标。
    # 同组（相邻 lcp 均 >= L）起点集合 S 中，j 合法当且仅当
    # j >= f(i) = max(i+L, g[i])；f 非降，故 S 可行当且仅当 f(min S) <= max S。
    def feasible(length):
        lo = hi = sa[0]
        for p in range(1, n):
            if lcp[p - 1] >= length:
                idx = sa[p]
                if idx < lo:
                    lo = idx
                elif idx > hi:
                    hi = idx
            else:
                if max(lo + length, g[lo]) <= hi:
                    return True
                lo = hi = sa[p]
        return max(lo + length, g[lo]) <= hi

    # usable = min(lcp, j-i) <= min(j, n-j) <= n//2，且可行性随长度单调，二分最大值
    lo_len, hi_len = min_words, n // 2
    best_len = 0
    while lo_len <= hi_len:
        mid = (lo_len + hi_len) // 2
        if feasible(mid):
            best_len = mid
            lo_len = mid + 1
        else:
            hi_len = mid - 1
    if best_len < min_words:
        return None

    # 重建字典序最小起点对：按相邻 lcp >= best_len 分组，组内起点升序；
    # i 升序枚举，第一个在组内存在 j >= f(i) 的 i 即最优首词，
    # 组内第一个 >= f(i) 的起点即最优次词（feasible(best_len) 保证存在）。
    groups = []
    current = [sa[0]]
    for p in range(1, n):
        if lcp[p - 1] >= best_len:
            current.append(sa[p])
        else:
            groups.append(current)
            current = [sa[p]]
    groups.append(current)
    group_of = [0] * n
    for gid, members in enumerate(groups):
        members.sort()
        for idx in members:
            group_of[idx] = gid

    best_pair = None
    for i in range(n):
        members = groups[group_of[i]]
        if len(members) < 2:
            continue
        f = i + best_len
        if g[i] > f:
            f = g[i]
        pos = bisect_left(members, f)
        if pos < len(members):
            best_pair = (i, members[pos])
            break

    i, j = best_pair
    end_i = i + best_len - 1
    end_j = j + best_len - 1
    return {
        "word_count": best_len,
        "text": "".join(w.text for w in words[i : i + best_len]),
        "first": {
            "start_word": i,
            "end_word": end_i,
            "start_ms": starts[i],
            "end_ms": words[end_i].end_ms,
        },
        "second": {
            "start_word": j,
            "end_word": end_j,
            "start_ms": starts[j],
            "end_ms": words[end_j].end_ms,
        },
    }
