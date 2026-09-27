"""遍次排练提示单：把 1~32 条提示按遍次条件命中到演奏带访问的指定拍。

输入
====

- ``measures``  录入谱面（同 ``/api/expand``）：先复用现有完整校验与展开；
- ``cues``      1~32 条提示组成的数组，每条提示：

  - ``measure``  来源小节编号（必须是谱面中的编号）；
  - ``beat``     从 1 开始的节内拍序（不得超过该小节拍数）；
  - ``text``     1~80 字提示语；
  - ``passes``   可选的遍次限定数组，每项 ``{"open", "pass"}``——``open``
                 是反复起点所在行（0 基行号），``pass`` 是该层要限定的遍次；
                 未限定的层级匹配任意遍。省略或空数组表示不限定。

校验
====

复用 :func:`app.expander.expand` 的完整校验与展开（含 512 次访问上限）
之后逐条校验：拍序不得超过该小节拍数；所选反复（按起点行）须至少出现
在该小节的一条访问路径中；遍次须在 1~该层次数之间；来源小节与全部遍次
限定组成的条件没有命中任何访问时，按提示行报错。

匹配与计时
==========

按每次演奏访问的**完整遍次路径**匹配：反复主终点之后尾部房子的
``tail`` 路径同样参与；外层重入后内层遍次已由展开器重新计数，因此
“外层第 2 遍里的内层第 1 遍”与“外层第 1 遍里的内层第 1 遍”是不同的
命中。每条命中提示落在对应访问的指定拍：该拍秒数 = 此前各访问
拍数 × 60/BPM 的累计 + (拍序 − 1) × 60/本访问生效 BPM。提示单按
（时间, 录入顺序）排列输出。
"""

from __future__ import annotations

from typing import Any

from .expander import MAX_VISITS, expand

MIN_CUES = 1
MAX_CUES = 32
MAX_TEXT_LEN = 80
MAX_NUMBER_LEN = 16

# 秒数保留 6 位小数（与改谱对照一致），避免浮点尾差干扰展示与断言
_SECONDS_DIGITS = 6


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


def _visit_ref(visit: dict[str, Any]) -> dict[str, Any]:
    return {
        "global_index": visit["global_index"],
        "source_index": visit["source_index"],
        "source_measure": visit["source_measure"],
        "path": visit["path"],
    }


def _validate_cue_shape(raw: Any,
                        ) -> tuple[list[dict[str, Any]] | None,
                                   list[dict[str, Any]]]:
    """校验提示数组的形状，返回 ``(cues, errors)``。

    每条提示规整为 ``{measure, beat, text, passes, shape_ok}``；无法解析
    的字段置 ``None`` 并记 ``shape_ok = False``（该行的语义检查跳过，
    其余行照常）。``passes`` 省略或为 ``null`` 视为空数组（不限定）。
    """
    if not isinstance(raw, list):
        return None, [_err("CUES_FORMAT", "cues 必须是提示对象组成的数组。")]
    if not (MIN_CUES <= len(raw) <= MAX_CUES):
        return None, [_err(
            "CUES_COUNT",
            f"提示条数必须在 {MIN_CUES}~{MAX_CUES} 之间，"
            f"当前为 {len(raw)} 条。")]

    cues: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(_err("CUE_FORMAT", "每条提示必须是对象。", i))
            cues.append({"measure": None, "beat": None, "text": None,
                         "passes": [], "shape_ok": False})
            continue
        shape_ok = True

        measure_raw = item.get("measure")
        measure = measure_raw.strip() if isinstance(measure_raw, str) else None
        if not measure:
            errors.append(_err(
                "CUE_MEASURE_FORMAT",
                "来源小节编号必须是非空字符串（谱面中的小节编号）。", i))
            shape_ok = False
            measure = None
        elif len(measure) > MAX_NUMBER_LEN:
            errors.append(_err(
                "CUE_MEASURE_FORMAT",
                f"来源小节编号最多 {MAX_NUMBER_LEN} 个字符。", i, measure))
            shape_ok = False
            measure = None

        beat = _as_int(item.get("beat"))
        if beat is None or beat < 1:
            errors.append(_err(
                "CUE_BEAT_FORMAT",
                "拍序必须是从 1 开始的整数（节内第几拍）。", i, measure))
            shape_ok = False
            beat = None

        text_raw = item.get("text")
        text = text_raw.strip() if isinstance(text_raw, str) else None
        if not text:
            errors.append(_err(
                "CUE_TEXT",
                f"提示语必须是 1~{MAX_TEXT_LEN} 字的非空文本。", i, measure))
            shape_ok = False
            text = None
        elif len(text) > MAX_TEXT_LEN:
            errors.append(_err(
                "CUE_TEXT",
                f"提示语最多 {MAX_TEXT_LEN} 字，当前 {len(text)} 字。",
                i, measure))
            shape_ok = False
            text = None

        passes_raw = item.get("passes")
        if passes_raw is None:
            passes_raw = []
        passes: list[dict[str, int]] = []
        if not isinstance(passes_raw, list):
            errors.append(_err(
                "CUE_PASS_FORMAT",
                "passes 必须是遍次限定数组（省略表示不限定任何层）。",
                i, measure))
            shape_ok = False
        else:
            seen_opens: set[int] = set()
            for p in passes_raw:
                if not isinstance(p, dict):
                    errors.append(_err(
                        "CUE_PASS_FORMAT",
                        "每条遍次限定必须是包含 open 与 pass 的对象。",
                        i, measure))
                    shape_ok = False
                    continue
                open_idx = _as_int(p.get("open"))
                pass_no = _as_int(p.get("pass"))
                if open_idx is None or open_idx < 0 \
                        or pass_no is None or pass_no < 1:
                    errors.append(_err(
                        "CUE_PASS_FORMAT",
                        "遍次限定的 open（反复起点行，0 基）必须是不小于 0 "
                        "的整数，pass（遍次）必须是不小于 1 的整数。",
                        i, measure))
                    shape_ok = False
                    continue
                if open_idx in seen_opens:
                    errors.append(_err(
                        "CUE_PASS_DUPLICATE",
                        f"第 {open_idx + 1} 行起点的反复被重复限定。",
                        i, measure))
                    shape_ok = False
                    continue
                seen_opens.add(open_idx)
                passes.append({"open": open_idx, "pass": pass_no})

        cues.append({
            "measure": measure,
            "beat": beat,
            "text": text,
            "passes": passes,
            "shape_ok": shape_ok,
        })
    return cues, errors


def _match_cues(cues: list[dict[str, Any]],
                score_result: dict[str, Any],
                ) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    """语义校验与匹配，返回 ``(entries, total_seconds, errors)``。

    对每条形状合法的提示：来源小节须存在、拍序不超过该小节拍数、每个
    遍次限定的反复须出现在该小节的访问路径中且遍次不越界；随后按每次
    访问的完整遍次路径（含尾部房子 tail 路径）做组合匹配，无命中按
    提示行报错。命中的提示落到对应访问的指定拍并累计秒数。
    """
    visits = score_result["visits"]
    repeat_by_open = {r["open_index"]: r for r in score_result["repeats"]}

    # 每个来源小节的全部访问（演奏顺序）；合法谱面中每个小节至少被
    # 演奏一次，故编号是否存在于谱面等价于是否有访问。
    visits_by_measure: dict[str, list[dict[str, Any]]] = {}
    for v in visits:
        visits_by_measure.setdefault(v["source_measure"], []).append(v)

    # 每次访问的起拍秒数：此前各访问 拍数 × 60/生效BPM 累计
    start_seconds: list[float] = []
    acc = 0.0
    for v in visits:
        start_seconds.append(acc)
        acc += v["beats"] * 60.0 / v["tempo"]
    total_seconds = acc

    errors: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for i, cue in enumerate(cues):
        if not cue["shape_ok"]:
            continue  # 形状错误已上报，本行不再做语义检查
        measure = cue["measure"]
        m_visits = visits_by_measure.get(measure)
        if not m_visits:
            errors.append(_err(
                "CUE_MEASURE_UNKNOWN",
                f"谱面中没有编号为 {measure!r} 的小节。",
                i, measure))
            continue
        beats = m_visits[0]["beats"]
        beat = cue["beat"]
        if beat > beats:
            errors.append(_err(
                "CUE_BEAT_RANGE",
                f"拍序 {beat} 超过小节 {measure} 的拍数（{beats} 拍）。",
                i, measure))
            continue

        # 该小节全部访问路径中出现过的反复 id（含尾部房子路径）
        ids_in_measure: set[int] = set()
        for v in m_visits:
            for p in v["path"]:
                ids_in_measure.add(p["repeat_id"])

        constraints: list[tuple[int, int]] = []  # [(repeat_id, pass)]
        cue_ok = True
        for p in cue["passes"]:
            rep = repeat_by_open.get(p["open"])
            if rep is None:
                errors.append(_err(
                    "CUE_PASS_OPEN",
                    f"第 {p['open'] + 1} 行不是反复起点"
                    f"（反复起点是带左反复 |: 的小节行）。",
                    i, measure))
                cue_ok = False
                continue
            if not (1 <= p["pass"] <= rep["times"]):
                errors.append(_err(
                    "CUE_PASS_RANGE",
                    f"第 {p['open'] + 1} 行起点的反复共 {rep['times']} 遍，"
                    f"限定遍次 {p['pass']} 超出 1~{rep['times']}。",
                    i, measure))
                cue_ok = False
                continue
            if rep["id"] not in ids_in_measure:
                errors.append(_err(
                    "CUE_PASS_REPEAT",
                    f"第 {p['open'] + 1} 行起点的反复未出现在小节 "
                    f"{measure} 的任何访问路径中。",
                    i, measure))
                cue_ok = False
                continue
            constraints.append((rep["id"], p["pass"]))
        if not cue_ok:
            continue

        # 组合匹配：访问的完整遍次路径须同时满足全部限定；
        # 未限定的层级匹配任意遍。
        hits = [
            v for v in m_visits
            if all(any(pe["repeat_id"] == rid and pe["pass"] == pno
                       for pe in v["path"])
                   for rid, pno in constraints)
        ]
        if not hits:
            errors.append(_err(
                "CUE_NO_MATCH",
                "来源小节与遍次限定的组合没有命中任何访问；"
                "请检查遍次限定是否互相矛盾。",
                i, measure))
            continue
        for v in hits:
            sec = (start_seconds[v["global_index"] - 1]
                   + (beat - 1) * 60.0 / v["tempo"])
            entries.append({
                "cue_index": i,
                "text": cue["text"],
                "beat": beat,
                "seconds": round(sec, _SECONDS_DIGITS),
                "passes": [{"open": p["open"], "pass": p["pass"]}
                           for p in cue["passes"]],
                "visit": _visit_ref(v),
            })

    # 依时间与录入顺序排列；同一提示的多次命中再按访问序号
    entries.sort(key=lambda e: (e["seconds"], e["cue_index"],
                                e["visit"]["global_index"]))
    return entries, total_seconds, errors


def build_cue_sheet(raw: dict[str, Any],
                    ) -> tuple[dict[str, Any] | None,
                               dict[str, Any] | None]:
    """提示单入口。成功返回 ``(result, None)``；校验失败返回
    ``(None, payload)``，payload 含 ``score_errors`` / ``cue_errors``
    两组错误列表（可为空数组），提示错误按提示行（0 基）定位。
    """
    measures = raw["measures"]
    cues_raw = raw["cues"]

    # 1) 录入谱面：复用现有完整校验与展开（含 512 上限）
    if not isinstance(measures, list):
        score_errors = [_err(
            "INVALID_REQUEST", "measures 必须是小节对象组成的数组。")]
        score_result = None
    else:
        score_result, score_errors = expand(measures)

    # 2) 提示形状校验（不依赖谱面）
    cues, cue_errors = _validate_cue_shape(cues_raw)

    # 3) 谱面合法时继续语义校验与匹配（形状有误的提示行跳过）
    entries: list[dict[str, Any]] = []
    total_seconds = 0.0
    if score_result is not None and cues is not None:
        entries, total_seconds, sem_errors = _match_cues(cues, score_result)
        cue_errors.extend(sem_errors)

    if score_errors or cue_errors:
        cue_errors.sort(key=lambda e: (
            e["index"] is None,
            e["index"] if e["index"] is not None else -1))
        return None, {
            "score_errors": score_errors,
            "cue_errors": cue_errors,
        }
    assert score_result is not None and cues is not None

    return {
        "cue_count": len(cues),
        "hit_count": len(entries),
        "visit_count": score_result["visit_count"],
        "total_seconds": round(total_seconds, _SECONDS_DIGITS),
        "max_cues": MAX_CUES,
        "max_text_len": MAX_TEXT_LEN,
        "max_visits": MAX_VISITS,
        "entries": entries,
    }, None
