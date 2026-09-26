"""说话人标注校正核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

在严格满足人工锁定、且不改词序/文本/时间的前提下，为每个词从候选说话人
（原标注与锁定值的并集，至多 8 个）中重新指派说话人，最小化

    改动原 speaker 的词数 + switch_cost × 相邻换人次数

同成本时取整条说话人序列字典序最小者。locks 为空时即对原标注做平滑。

算法（后向 DP + 贪心重建），O(n·m) 时间、O(n·m) 空间，m 为候选说话人数（<= 8）：

* bwd[i][k]：词 i 指派为第 k 个候选时，后缀 [i, n) 的最小总代价（含词 i 的
  改动代价与其后全部换人代价）；锁定词只允许取锁定值；
* 转移利用“不换人代价为 0、换人统一为 switch_cost”的结构：
  bwd[i][k] = 改动代价(i,k) + min(bwd[i+1][k], switch_cost + 下一行除 k 外的最小值)，
  每行预求最小值与次小值即可 O(m) 完成整行转移；
* 重建：最优总成本为 bwd[0] 的最小值。从左向右逐词贪心，每步在仍能达到
  最优总成本的候选中取字典序最小的说话人，归纳可得整条序列字典序最小。
"""

from .errors import DomainError
from .segmentation import validate_words

MAX_SPEAKERS = 8  # 候选说话人上限

INF = float("inf")


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def reconcile_speakers(words, locks, switch_cost):
    """重指派说话人，返回 (revised_words, changed_word_indices, total_cost)。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker；
    locks 中每项需具备属性：word_index / speaker（可为空序列）。

    revised_words 与输入等长，词序、文本、时间均不变，仅 speaker 可能被改写。
    锁定索引越界、重复锁定或候选说话人超限时抛出 DomainError（HTTP 422）。
    """
    validate_words(words)
    n = len(words)

    # ---- 校验锁定：索引范围、重复 ----
    locked = [None] * n
    for lock in locks:
        idx = lock.word_index
        if not 0 <= idx < n:
            _fail(
                "lock_index_out_of_range",
                f"锁定词序号 {idx} 超出范围 [0, {n})",
                word_index=idx,
            )
        if locked[idx] is not None:
            _fail(
                "duplicate_lock",
                f"词序号 {idx} 被重复锁定",
                word_index=idx,
            )
        locked[idx] = lock.speaker

    # ---- 候选说话人：原标注与锁定值的并集（按字典序排列），至多 8 个 ----
    candidates = sorted({w.speaker for w in words} | {lock.speaker for lock in locks})
    if len(candidates) > MAX_SPEAKERS:
        _fail(
            "too_many_speakers",
            f"候选说话人共 {len(candidates)} 个，超过上限 {MAX_SPEAKERS}",
            speaker_count=len(candidates),
        )
    m = len(candidates)

    orig = [w.speaker for w in words]

    # ---- 后向 DP：bwd[i][k] = 词 i 指派 candidates[k] 时后缀 [i, n) 的最小代价 ----
    bwd = [[INF] * m for _ in range(n)]
    last = n - 1
    for k in range(m):
        if locked[last] is None or candidates[k] == locked[last]:
            bwd[last][k] = 0 if candidates[k] == orig[last] else 1
    for i in range(last - 1, -1, -1):
        nxt = bwd[i + 1]
        # 下一行的最小值与次小值：换到“别人”时只需知道除自己外的最小值
        min1 = min2 = INF
        arg1 = -1
        for k in range(m):
            v = nxt[k]
            if v < min1:
                min2, min1, arg1 = min1, v, k
            elif v < min2:
                min2 = v
        row = bwd[i]
        for k in range(m):
            spk = candidates[k]
            if locked[i] is not None and spk != locked[i]:
                continue
            best_next = nxt[k]  # 不换人
            others = min1 if arg1 != k else min2
            alt = switch_cost + others  # 换到别人的最优
            if alt < best_next:
                best_next = alt
            row[k] = (0 if spk == orig[i] else 1) + best_next

    optimum = min(bwd[0])  # 锁定值必在候选中，恒有可行解

    # ---- 贪心重建字典序最小的最优说话人序列 ----
    speakers = []
    budget = optimum
    prev = None
    for i in range(n):
        for k in range(m):
            spk = candidates[k]
            if locked[i] is not None and spk != locked[i]:
                continue
            transition = switch_cost if prev is not None and spk != prev else 0
            if transition + bwd[i][k] != budget:
                continue
            speakers.append(spk)
            budget -= transition + (0 if spk == orig[i] else 1)
            prev = spk
            break

    revised = [
        {
            "start_ms": w.start_ms,
            "end_ms": w.end_ms,
            "text": w.text,
            "speaker": speakers[i],
        }
        for i, w in enumerate(words)
    ]
    changed = [i for i in range(n) if speakers[i] != orig[i]]
    return revised, changed, int(optimum)
