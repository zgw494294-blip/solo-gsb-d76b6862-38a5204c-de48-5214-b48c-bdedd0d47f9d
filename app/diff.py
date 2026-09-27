"""改谱影响对照：两版展开结果的 LCS 对齐、差异分类与时间偏移。

对照流程
========

1. 两版小节分别经 :func:`app.expander.expand` 校验并展开（任一失败时由
   路由层返回该版错误，不产生对照结果）；
2. 对两条演奏带按**来源小节编号**求最长公共子序列（LCS）：重复访问逐次
   参与配对；最优解不唯一时，取“配对序号对序列”字典序最小的一组
   （逐对比较，每对先比基准下标、再比当前下标）；
3. 未配对访问标为新增（当前版）或删去（基准版）；配对访问比较拍数、
   BPM 与遍次路径（逐层的 遍次/总遍数/是否尾部房子）；
4. 两版分别按每拍 60/BPM 秒累计起拍时间，配对访问给出时间偏移，
   并标出按对齐顺序的首次差异。
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

# 秒数保留 6 位小数，避免浮点尾差干扰展示与断言
_SECONDS_DIGITS = 6


def _lcs_pairs(base_seq: list[str], curr_seq: list[str]) -> list[tuple[int, int]]:
    """返回字典序最小的最长配对下标对序列 ``[(基准下标, 当前下标), …]``。

    ``dp[i][j]`` 为 ``base_seq[i:]`` 与 ``curr_seq[j:]`` 的 LCS 长度。
    贪心回取：每一步在可行范围内取最小的基准下标 ``i'``，再取最小的
    当前下标 ``j'``（``curr_seq`` 中该编号在 ``j`` 之后的首个出现），
    使得后缀仍能补足剩余长度——``dp`` 关于下标单调，故首个出现若不可行，
    更靠后的出现也不可行。
    """
    n, m = len(base_seq), len(curr_seq)
    if n == 0 or m == 0:
        return []

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        ai = base_seq[i]
        row, below = dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            if ai == curr_seq[j]:
                row[j] = below[j + 1] + 1
            else:
                row[j] = below[j] if below[j] >= row[j + 1] else row[j + 1]

    # 当前版中每个编号的出现下标（升序），供二分定位某位置之后的首个出现
    positions: dict[str, list[int]] = {}
    for j, c in enumerate(curr_seq):
        positions.setdefault(c, []).append(j)

    pairs: list[tuple[int, int]] = []
    i = j = 0
    remaining = dp[0][0]
    while remaining > 0:
        for ii in range(i, n):
            plist = positions.get(base_seq[ii])
            if not plist:
                continue
            k = bisect_left(plist, j)
            if k == len(plist):
                continue
            jj = plist[k]
            if dp[ii + 1][jj + 1] == remaining - 1:
                pairs.append((ii, jj))
                i, j = ii + 1, jj + 1
                remaining -= 1
                break
        else:
            break  # 理论不可达：dp 不变量保证每步必有可行配对
    return pairs


def _path_signature(path: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """遍次路径归一化：逐层 (遍次, 总遍数, 是否尾部房子)。

    反复 id 是两版各自按起点顺序编的号、跨版比较无意义，不参与对照。
    """
    return [(p["pass"], p["times"], bool(p.get("tail"))) for p in path]


def _start_times(visits: list[dict[str, Any]]) -> tuple[list[float], float]:
    """按每拍 60/BPM 秒累计，返回 ``(每次访问的起拍时间, 总时长)``。"""
    starts: list[float] = []
    t = 0.0
    for v in visits:
        starts.append(t)
        t += v["beats"] * 60.0 / v["tempo"]
    return starts, t


def _visit_ref(visit: dict[str, Any], start: float) -> dict[str, Any]:
    return {
        "global_index": visit["global_index"],
        "source_index": visit["source_index"],
        "source_measure": visit["source_measure"],
        "beats": visit["beats"],
        "tempo": visit["tempo"],
        "volta": visit["volta"],
        "path": visit["path"],
        "start_seconds": round(start, _SECONDS_DIGITS),
    }


def compare(base_result: dict[str, Any],
            curr_result: dict[str, Any]) -> dict[str, Any]:
    """对照两版展开结果，返回对齐事件序列、统计与首次差异。

    事件按对齐顺序排列：相邻两对配对之间，先排出基准版被删去的访问，
    再排出当前版新增的访问。每个事件带 ``align_index``（对齐位次）与
    ``differs``（是否构成差异）。
    """
    base_visits = base_result["visits"]
    curr_visits = curr_result["visits"]
    pairs = _lcs_pairs(
        [v["source_measure"] for v in base_visits],
        [v["source_measure"] for v in curr_visits],
    )

    base_starts, base_total = _start_times(base_visits)
    curr_starts, curr_total = _start_times(curr_visits)

    events: list[dict[str, Any]] = []
    stats = {"paired": 0, "unchanged": 0, "changed": 0, "added": 0, "removed": 0}
    first_difference: dict[str, Any] | None = None

    def emit(kind: str,
             base_ref: dict[str, Any] | None,
             curr_ref: dict[str, Any] | None,
             changes: list[str],
             time_offset: float | None) -> None:
        nonlocal first_difference
        differs = kind != "pair" or bool(changes)
        event = {
            "align_index": len(events),
            "kind": kind,
            "base": base_ref,
            "curr": curr_ref,
            "changes": changes,
            "time_offset": time_offset,
            "differs": differs,
        }
        events.append(event)
        if differs and first_difference is None:
            first_difference = {
                "align_index": event["align_index"],
                "kind": kind,
                "base": base_ref,
                "curr": curr_ref,
                "changes": changes,
                "time_offset": time_offset,
            }

    bi = ci = 0
    for b, c in pairs:
        for x in range(bi, b):
            emit("removed", _visit_ref(base_visits[x], base_starts[x]),
                 None, [], None)
            stats["removed"] += 1
        for y in range(ci, c):
            emit("added", None, _visit_ref(curr_visits[y], curr_starts[y]),
                 [], None)
            stats["added"] += 1

        bv, cv = base_visits[b], curr_visits[c]
        changes: list[str] = []
        if bv["beats"] != cv["beats"]:
            changes.append("beats")
        if bv["tempo"] != cv["tempo"]:
            changes.append("tempo")
        if _path_signature(bv["path"]) != _path_signature(cv["path"]):
            changes.append("path")
        offset = round(curr_starts[c] - base_starts[b], _SECONDS_DIGITS)
        emit("pair",
             _visit_ref(bv, base_starts[b]),
             _visit_ref(cv, curr_starts[c]),
             changes, offset)
        stats["paired"] += 1
        stats["changed" if changes else "unchanged"] += 1
        bi, ci = b + 1, c + 1

    for x in range(bi, len(base_visits)):
        emit("removed", _visit_ref(base_visits[x], base_starts[x]),
             None, [], None)
        stats["removed"] += 1
    for y in range(ci, len(curr_visits)):
        emit("added", None, _visit_ref(curr_visits[y], curr_starts[y]),
             [], None)
        stats["added"] += 1

    return {
        "baseline": {
            "visit_count": len(base_visits),
            "total_beats": base_result["total_beats"],
            "total_seconds": round(base_total, _SECONDS_DIGITS),
        },
        "current": {
            "visit_count": len(curr_visits),
            "total_beats": curr_result["total_beats"],
            "total_seconds": round(curr_total, _SECONDS_DIGITS),
        },
        "stats": stats,
        "events": events,
        "first_difference": first_difference,
    }
