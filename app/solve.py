"""反复次数反求：给定目标演奏编号序列，反推待求位置的次数组合。

流程
====

1. 先按现有规则校验**录入谱面**（次数照录入值，含展开与 512 上限）与
   **目标演奏编号序列**（1~512 项，每项必须是谱面中的小节编号）；
2. 待求位置限 1~8 个，且必须带左侧反复起点；
3. 枚举待求位置的次数组合（各取 2~4；小节顺序、反复边界、替代结尾、
   拍数与 BPM 不变，其余反复的次数照录入值），每个组合都经现有校验与
   展开——校验失败或超过 512 次访问的组合不计入；
4. 展开编号序列与目标完全一致的组合即为方案。方案按待求位置的录入行
   顺序组成次数元组，字典序排列，最多返回前 10 个，并给出总解数；
   谱面与目标均合法但没有匹配组合时，返回 ``solution_count = 0`` 由
   前端明确提示无解。
"""

from __future__ import annotations

from itertools import product
from typing import Any

from .expander import MAX_VISITS, expand, normalize_rows

MIN_UNKNOWNS = 1
MAX_UNKNOWNS = 8
TIMES_CHOICES = (2, 3, 4)
MAX_SOLUTIONS_RETURNED = 10
MAX_NUMBER_LEN = 16


def _err(code: str, message: str, index: int | None = None,
         measure: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "index": index,
        "measure": measure,
    }


def _validate_unknowns(raw: Any, rows: list[dict[str, Any]] | None,
                       row_count: int | None,
                       ) -> tuple[list[int] | None, list[dict[str, Any]]]:
    """校验待求位置选择，返回 ``(indices, errors)``（行号按录入顺序升序）。

    ``rows`` 为 :func:`normalize_rows` 成功解析出的小节列表（可能为
    None，此时只做得知形状的范围/格式校验，左反复检查跳过）。
    """
    if not isinstance(raw, list):
        return None, [_err(
            "UNKNOWN_FORMAT", "unknowns 必须是待求位置行号（0 基）组成的数组。")]
    if not (MIN_UNKNOWNS <= len(raw) <= MAX_UNKNOWNS):
        return None, [_err(
            "UNKNOWN_COUNT",
            f"待求位置必须勾选 {MIN_UNKNOWNS}~{MAX_UNKNOWNS} 个，"
            f"当前为 {len(raw)} 个。",
        )]

    errors: list[dict[str, Any]] = []
    indices: list[int] = []
    seen: set[int] = set()
    for pos, item in enumerate(raw):
        if isinstance(item, bool) or not isinstance(item, int):
            errors.append(_err(
                "UNKNOWN_FORMAT", "待求位置必须是整数行号（0 基）。", pos))
            continue
        if item in seen:
            errors.append(_err(
                "UNKNOWN_DUPLICATE", f"待求位置第 {item + 1} 行重复选择。", pos))
            continue
        seen.add(item)
        if row_count is not None and not (0 <= item < row_count):
            errors.append(_err(
                "UNKNOWN_RANGE", f"待求位置第 {item + 1} 行超出小节范围。", pos))
            continue
        if (rows is not None and 0 <= item < len(rows)
                and not rows[item]["left_repeat"]):
            errors.append(_err(
                "UNKNOWN_NOT_LEFT_REPEAT",
                f"第 {item + 1} 行（编号 {rows[item]['number']!r}）"
                "没有左侧反复起点，不能作为待求位置。",
                item, rows[item]["number"],
            ))
            continue
        indices.append(item)

    if errors:
        return None, errors
    return sorted(indices), []


def _validate_target(raw: Any, numbers: set[str] | None,
                     ) -> tuple[list[str] | None, list[dict[str, Any]]]:
    """校验目标演奏编号序列，返回 ``(target, errors)``。

    ``numbers`` 为谱面编号集合（谱面不可用时为 None，跳过归属检查）。
    """
    if not isinstance(raw, list):
        return None, [_err(
            "TARGET_FORMAT", "target 必须是小节编号组成的数组。")]
    if not raw:
        return None, [_err("TARGET_EMPTY", "目标演奏编号序列不能为空。")]
    if len(raw) > MAX_VISITS:
        return None, [_err(
            "TARGET_TOO_MANY",
            f"目标演奏编号序列最多 {MAX_VISITS} 项，当前为 {len(raw)} 项。",
        )]

    errors: list[dict[str, Any]] = []
    target: list[str] = []
    for pos, item in enumerate(raw):
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            errors.append(_err(
                "TARGET_ITEM_FORMAT", "目标项必须是小节编号（字符串）。", pos))
            continue
        text = str(item).strip()
        if not text:
            errors.append(_err("TARGET_ITEM_FORMAT", "目标项不能为空。", pos))
            continue
        if len(text) > MAX_NUMBER_LEN:
            errors.append(_err(
                "TARGET_ITEM_FORMAT",
                f"目标项 {text!r} 超过 {MAX_NUMBER_LEN} 个字符。",
                pos, text,
            ))
            continue
        target.append(text)
    if errors:
        return None, errors

    if numbers is not None:
        for pos, text in enumerate(target):
            if text not in numbers:
                errors.append(_err(
                    "TARGET_UNKNOWN_MEASURE",
                    f"目标第 {pos + 1} 项 {text!r} 不是录入谱面中的小节编号。",
                    pos, text,
                ))
        if errors:
            return None, errors
    return target, []


def solve(raw: dict[str, Any],
          ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """反求入口。成功返回 ``(result, None)``；失败返回 ``(None, payload)``，
    payload 含 ``score_errors`` / ``unknown_errors`` / ``target_errors``
    三组错误列表（可为空数组）。
    """
    measures = raw["measures"]
    raw_unknowns = raw["unknowns"]
    raw_target = raw["target"]

    # 1) 录入谱面：先按现有规则校验（次数照录入值，含展开与 512 上限）
    rows: list[dict[str, Any]] | None = None
    row_count = len(measures) if isinstance(measures, list) else None
    if isinstance(measures, list):
        rows, score_errors = normalize_rows(measures)
        if not score_errors:
            _, score_errors = expand(measures)
    else:
        score_errors = [_err(
            "INVALID_REQUEST", "measures 必须是小节对象组成的数组。")]

    # 2) 待求位置与目标序列
    unknown_indices, unknown_errors = _validate_unknowns(
        raw_unknowns, rows, row_count)
    numbers = {r["number"] for r in rows} if rows else None
    target, target_errors = _validate_target(raw_target, numbers)

    if score_errors or unknown_errors or target_errors:
        return None, {
            "score_errors": score_errors,
            "unknown_errors": unknown_errors,
            "target_errors": target_errors,
        }
    assert rows is not None and unknown_indices and target is not None

    # 3) 枚举次数组合：每个组合都须通过现有校验与展开。
    # expect=target 让展开在与目标不符时提前中止（结论与完整展开一致）。
    trial = [dict(r) for r in rows]
    solutions: list[dict[str, Any]] = []
    solution_count = 0
    for combo in product(TIMES_CHOICES, repeat=len(unknown_indices)):
        for idx, t in zip(unknown_indices, combo):
            trial[idx]["times"] = t
        _, errors = expand(trial, expect=target)
        if errors:
            continue  # 校验失败、展开与目标不符或超过 512 次访问：不计入
        solution_count += 1
        if len(solutions) < MAX_SOLUTIONS_RETURNED:
            solutions.append({"times": list(combo)})

    return {
        # 待求位置按录入行顺序
        "unknowns": [{"index": i, "measure": rows[i]["number"]}
                     for i in unknown_indices],
        "target_count": len(target),
        "combination_count": len(TIMES_CHOICES) ** len(unknown_indices),
        "solution_count": solution_count,
        "solutions": solutions,
    }, None
