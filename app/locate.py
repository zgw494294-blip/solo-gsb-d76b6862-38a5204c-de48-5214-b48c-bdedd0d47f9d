"""排练记录定位：把排练记录与演奏带的**任意非空连续区间**做编辑距离匹配。

输入
====

- ``measures``   录入谱面（同 ``/api/expand``）：先复用现有完整校验与展开，
                  得到实际演奏带（每次访问按来源小节编号取符号）；
- ``record``     排练记录文本：以逗号（中英文均可，顿号同样接受）或换行
                  分隔的 **1~64** 个小节编号，**允许出现谱面之外的误记
                  编号**（不做归属校验，否则误记无从体现）；
- ``tolerance``  容错数：0~4 的整数。

匹配模型
========

把完整记录（模式 P，长度 m）与演奏带 T（长度 n，访问数 ≤ 512）的每个
**非空连续区间** ``T[s:b]`` 求半全局（semi-global）Levenshtein 编辑距离：

- 相同（match）计 0，错记替换（substitute）、多记删除（delete，记录里
  多写的编号）、漏记补入（insert，演奏了却没记的访问）各计 1；
- 区间外的访问一律不计费：模式可从任意列 s“免费开始”，结束于任意
  列 b，即只计区间 ``[s, b)`` 内的补入。

所有距离不超过容错数的区间都命中，按 ``(距离, 起点, 终点)`` 升序，最多
返回前 10 项并给出命中区间总数。不同遍次里同编号的访问各自占据不同的
全局序号，因此“同编号区间”按遍次分别计数。

动态规划状态复用
================

不为每个区间重算一张表：令 ``F[i][j]`` 为“从任意起点 s ≤ j 出发、把记录
前 i 项对齐到访问前缀 ``[:j]”时各起点的最小代价映射，用一张
``(m+1)×(n+1)×(容错+1)`` 的**来源位掩码累积表** R 一次算完：

``R[i][j][c]`` 的第 s 位为 1 ⇔ 存在一条从 ``(0,s)`` 到 ``(i,j)``、代价
≤ c 的对齐路径。第 0 行每个访问列都可免费开新区间（位 j），并允许一路
补入；后续单元按 匹配/替换（对角）、删除（上方）、补入（左方）转移。
命中枚举与逐项回溯都复用同一张表：精确代价层由
``E[i][j][c] = R[i][j][c] & ~R[i][j][c-1]`` 直接导出。

回溯从末端 ``(m,b)`` 逆向进行，遇到并列代价时按 **匹配 → 替换 → 删除 →
补入** 的固定优先级选边，保证对齐结果唯一确定。
"""

from __future__ import annotations

import re
from typing import Any

from .expander import MAX_VISITS, expand

MIN_RECORD_ITEMS = 1
MAX_RECORD_ITEMS = 64
MIN_TOLERANCE = 0
MAX_TOLERANCE = 4
MAX_MATCHES_RETURNED = 10
MAX_NUMBER_LEN = 16

# 逗号（，,、）或换行（含回车）分隔；编号内部空白保留，仅去首尾空白。
_SPLIT_RE = re.compile(r"[,，、\r\n]+")

OP_MATCH = "match"
OP_SUBSTITUTE = "substitute"
OP_DELETE = "delete"
OP_INSERT = "insert"


def _err(code: str, message: str, index: int | None = None,
         measure: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "index": index,
        "measure": measure,
    }


def _as_int(value: Any) -> int | None:
    # 与 expander 一致：bool 是 int 子类，这里拒绝。
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _validate_record(raw: Any) -> tuple[list[str] | None, list[dict[str, Any]]]:
    """解析并校验排练记录文本，返回 ``(items, errors)``。

    分隔后的空白段忽略（容忍末尾逗号 / 空行）；其余每项去首尾空白后
    必须非空且 ≤ 16 字符。**不检查编号是否在谱面中**——误记编号是
    定位输入的一部分。
    """
    if not isinstance(raw, str):
        return None, [_err(
            "LOCATE_RECORD_FORMAT",
            "排练记录必须是逗号或换行分隔的编号文本（字符串）。")]

    parts = [p.strip() for p in _SPLIT_RE.split(raw)]
    items = [p for p in parts if p]
    if not (MIN_RECORD_ITEMS <= len(items) <= MAX_RECORD_ITEMS):
        return None, [_err(
            "LOCATE_RECORD_COUNT",
            f"排练记录必须包含 {MIN_RECORD_ITEMS}~{MAX_RECORD_ITEMS} 个编号，"
            f"当前为 {len(items)} 个。")]

    errors: list[dict[str, Any]] = []
    for pos, text in enumerate(items):
        if len(text) > MAX_NUMBER_LEN:
            errors.append(_err(
                "LOCATE_RECORD_ITEM_FORMAT",
                f"记录第 {pos + 1} 项 {text!r} 超过 {MAX_NUMBER_LEN} 个字符。",
                pos, text))
    if errors:
        return None, errors
    return items, []


def _validate_tolerance(raw: Any) -> tuple[int | None, list[dict[str, Any]]]:
    value = _as_int(raw)
    if value is None or not (MIN_TOLERANCE <= value <= MAX_TOLERANCE):
        return None, [_err(
            "LOCATE_TOLERANCE_FORMAT",
            f"容错数必须是 {MIN_TOLERANCE}~{MAX_TOLERANCE} 之间的整数。")]
    return value, []


def _visit_ref(visit: dict[str, Any]) -> dict[str, Any]:
    return {
        "global_index": visit["global_index"],
        "source_index": visit["source_index"],
        "source_measure": visit["source_measure"],
        "path": visit["path"],
    }


def _build_table(record: list[str], text: list[str],
                 tolerance: int) -> tuple[list[list[int]], int]:
    """一次构建全部区间共用的来源位掩码累积表 R。

    表的每一行压成一个长度 ``(n+1)*(tolerance+1)`` 的扁平整数列表：
    单元 ``(i,j)`` 的第 c 层位于下标 ``j*W+c``。整数的第 s 位代表
    “存在从起点 s 到本单元、代价 ≤ c 的路径”。
    """
    m, n = len(record), len(text)
    width = tolerance + 1
    table = [[0] * ((n + 1) * width) for _ in range(m + 1)]

    # 第 0 行：任意列 j 都可免费开区间（位 j）；此前各列只能一路补入，
    # 每补入一次访问代价 +1，故 R[0][j][c] 即来源位区间 [j-c, j]。
    row0 = table[0]
    for c in range(width):
        row0[c] = 1  # 单元 (0,0)：来源 0，代价 0
    seed_bit = 1
    for j in range(1, n + 1):
        seed_bit <<= 1
        base = j * width
        row0[base] = seed_bit
        prev_base = (j - 1) * width
        for c in range(1, width):
            row0[base + c] = seed_bit | row0[prev_base + c - 1]

    for i in range(1, m + 1):
        row = table[i]
        above = table[i - 1]
        item = record[i - 1]
        # j = 0：只能一路删除记录项
        for c in range(1, width):
            row[c] = above[c - 1]
        for j in range(1, n + 1):
            base = j * width
            diag_base = (j - 1) * width
            same = item == text[j - 1]
            sub_cost = 0 if same else 1
            left_base = (j - 1) * width
            for c in range(width):
                value = 0
                # 匹配 / 替换：来自 (i-1, j-1)
                if c >= sub_cost:
                    value |= above[diag_base + c - sub_cost]
                # 多记删除：记录项不计入区间，来自 (i-1, j)
                if c >= 1:
                    value |= above[base + c - 1]
                    # 漏记补入：区间内多一次访问，来自 (i, j-1)
                    value |= row[left_base + c - 1]
                row[base + c] = value

    return table, width


def _exact_mask(table: list[list[int]], width: int, tolerance: int,
                i: int, j: int, cost: int) -> int:
    """精确代价层 E[i][j][cost]：恰好以 cost 到达本单元的来源位掩码。"""
    if i < 0 or j < 0 or cost < 0 or cost > tolerance:
        return 0
    idx = j * width + cost
    cumulative = table[i][idx]
    if cost:
        cumulative &= ~table[i][idx - 1]
    return cumulative


def _trace_alignment(table: list[list[int]], width: int, tolerance: int,
                     record: list[str], text: list[str],
                     visits: list[dict[str, Any]],
                     start: int, end: int, distance: int,
                     ) -> list[dict[str, Any]]:
    """沿共用 DP 表回溯 ``[start, end)`` 区间的逐项对齐。

    并列代价时的选边优先级：匹配 → 替换 → 删除 → 补入。
    """
    m = len(record)
    source_bit = 1 << start
    i, j, cost = m, end, distance
    steps: list[dict[str, Any]] = []

    while i > 0 or j > start:
        # 1/2) 对角：先匹配（编号相同且免费），再替换（编号不同计 1）
        if i >= 1 and j >= 1:
            if record[i - 1] == text[j - 1]:
                if _exact_mask(table, width, tolerance,
                               i - 1, j - 1, cost) & source_bit:
                    steps.append({
                        "op": OP_MATCH,
                        "record_pos": i,
                        "record_item": record[i - 1],
                        "visit": _visit_ref(visits[j - 1]),
                    })
                    i -= 1
                    j -= 1
                    continue
            elif cost >= 1 and (
                    _exact_mask(table, width, tolerance,
                                i - 1, j - 1, cost - 1) & source_bit):
                steps.append({
                    "op": OP_SUBSTITUTE,
                    "record_pos": i,
                    "record_item": record[i - 1],
                    "visit": _visit_ref(visits[j - 1]),
                })
                i -= 1
                j -= 1
                cost -= 1
                continue

        # 3) 删除：记录里多写的编号（区间无对应访问，计 1）
        if i >= 1 and cost >= 1 and (
                _exact_mask(table, width, tolerance,
                            i - 1, j, cost - 1) & source_bit):
            steps.append({
                "op": OP_DELETE,
                "record_pos": i,
                "record_item": record[i - 1],
                "visit": None,
            })
            i -= 1
            cost -= 1
            continue

        # 4) 补入：区间内演奏了却漏记的访问（计 1）
        assert j >= 1 and cost >= 1, (start, end, distance, i, j, cost)
        assert _exact_mask(table, width, tolerance,
                           i, j - 1, cost - 1) & source_bit
        steps.append({
            "op": OP_INSERT,
            "record_pos": None,
            "record_item": None,
            "visit": _visit_ref(visits[j - 1]),
        })
        j -= 1
        cost -= 1

    steps.reverse()
    return steps


def locate_records(raw: dict[str, Any],
                   ) -> tuple[dict[str, Any] | None,
                              dict[str, Any] | None]:
    """定位入口。成功返回 ``(result, None)``；校验失败返回
    ``(None, payload)``，payload 含 ``score_errors`` / ``record_errors``
    / ``tolerance_errors`` 三组错误列表（可为空数组）。
    """
    measures = raw["measures"]
    record_text = raw["record"]
    tolerance_raw = raw["tolerance"]

    # 1) 录入谱面：复用现有完整校验与展开（含 512 上限）
    if not isinstance(measures, list):
        score_errors = [_err(
            "INVALID_REQUEST", "measures 必须是小节对象组成的数组。")]
        score_result = None
    else:
        score_result, score_errors = expand(measures)

    # 2) 排练记录与容错数
    record, record_errors = _validate_record(record_text)
    tolerance, tolerance_errors = _validate_tolerance(tolerance_raw)

    if score_errors or record_errors or tolerance_errors:
        return None, {
            "score_errors": score_errors,
            "record_errors": record_errors,
            "tolerance_errors": tolerance_errors,
        }
    assert score_result is not None and record is not None
    assert tolerance is not None

    visits = score_result["visits"]
    text = [v["source_measure"] for v in visits]
    n = len(visits)

    table, width = _build_table(record, text, tolerance)

    # 3) 枚举全部命中区间：末行第 tolerance 层里出现的每个来源位 s 都
    #    代表一条距离 ≤ tolerance 的对齐；s < b 才是非空区间。精确距离
    #    取该来源位出现的最低累积层。
    last_row = table[len(record)]
    found: list[tuple[int, int, int]] = []
    for end in range(1, n + 1):
        base = end * width
        full_mask = last_row[base + tolerance]
        if not full_mask:
            continue
        mask = full_mask
        while mask:
            bit = mask & -mask
            start = bit.bit_length() - 1
            mask ^= bit
            if start >= end:
                continue  # 空区间（起点即终点）不计
            distance = 0
            for c in range(tolerance + 1):
                if last_row[base + c] & bit:
                    distance = c
                    break
            found.append((distance, start, end))

    # (距离, 起点, 终点) 升序；不同遍次的同编号访问序号不同，天然分别计数
    found.sort()
    match_count = len(found)

    matches: list[dict[str, Any]] = []
    for distance, start, end in found[:MAX_MATCHES_RETURNED]:
        start_visit = visits[start]
        end_visit = visits[end - 1]
        matches.append({
            "distance": distance,
            "start": start_visit["global_index"],
            "end": end_visit["global_index"],
            "interval_length": end - start,
            "start_path": start_visit["path"],
            "end_path": end_visit["path"],
            "start_visit": _visit_ref(start_visit),
            "end_visit": _visit_ref(end_visit),
            "alignment": _trace_alignment(
                table, width, tolerance, record, text, visits,
                start, end, distance),
        })

    return {
        "record": record,
        "record_count": len(record),
        "tolerance": tolerance,
        "visit_count": n,
        "max_visits": MAX_VISITS,
        "max_tolerance": MAX_TOLERANCE,
        "match_count": match_count,
        "shown_count": len(matches),
        "matches": matches,
    }, None
