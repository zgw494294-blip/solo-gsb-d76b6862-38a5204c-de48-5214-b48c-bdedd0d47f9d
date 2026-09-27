"""演奏片段摘谱：把演奏带上选中的若干访问区间剪成一份可回读的新谱。

输入
====

- ``measures``  录入谱面（同 ``/api/expand``）：先复用现有完整校验与展开；
- ``segments``  1~4 段互不重叠、按演奏先后排列的闭区间，每段
  ``{"start": s, "end": e}``——``s`` / ``e`` 是演奏带上的 1 基全局访问
  序号（``s <= e``），段间不得重叠且必须升序给出；各段合计 2~64 次访问。
  同一来源小节在不同遍次的访问是不同序号，可重复选入。

摘谱行
======

选中的每次访问依序变成一行独立录入行：

- 编号重排为 1 起的连续整数（``"1"``、``"2"``……）；
- 拍数保留该次访问的拍数；
- **清除反复与房子记号**（无左右反复、无次数、无 volta、无 right_closes）；
- 速度：**每段首行写入该次访问的生效 BPM**（段间剪接处的速度自带，不会
  误沿用前一段末尾的速度）；段内其余行仅在生效 BPM 相对上一选中访问
  发生变化时写入，否则留空沿用。

导出与回读校验
==============

摘谱行先经 :func:`app.expander.expand` 完整校验与展开，再用现有的
:func:`app.musicxml_export.export_musicxml` 生成单 part MusicXML（导出
内部已含往返自检）。随后再用现有导入入口回读生成的文件，**逐行核对**
回读演奏带的拍数与生效 BPM 是否一一对应所选访问；不一致即返回
``MUSICXML_EXCERPT_ROUNDTRIP``，**不产生下载内容**。

原谱与演奏带不被修改：本模块只读取展开结果，不回写任何输入。
"""

from __future__ import annotations

from typing import Any

from .expander import MAX_VISITS, expand
from .musicxml_export import export_musicxml
from .musicxml_import import import_musicxml

MIN_SEGMENTS = 1
MAX_SEGMENTS = 4
MIN_EXCERPT_VISITS = 2
MAX_EXCERPT_VISITS = 64


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


def _validate_segments(raw: Any, visit_count: int,
                       ) -> tuple[list[tuple[int, int]] | None,
                                  list[dict[str, Any]]]:
    """校验选段数组，返回 ``(segments, errors)``。

    ``segments`` 为 ``[(start, end), …]``（1 基全局访问序号，闭区间，
    保持输入顺序）。校验：1~4 段、对象为 ``{start, end}``、序号在
    1~``visit_count`` 内且 ``start <= end``、段间互不重叠且按演奏先后
    升序、合计 2~64 次访问。
    """
    if not isinstance(raw, list):
        return None, [_err(
            "EXCERPT_SEGMENTS_FORMAT",
            "segments 必须是选段对象组成的数组（每段含 start 与 end）。")]
    if not (MIN_SEGMENTS <= len(raw) <= MAX_SEGMENTS):
        return None, [_err(
            "EXCERPT_SEGMENT_COUNT",
            f"选段数量必须在 {MIN_SEGMENTS}~{MAX_SEGMENTS} 段之间，"
            f"当前为 {len(raw)} 段。")]

    segments: list[tuple[int, int]] = []
    errors: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(_err(
                "EXCERPT_SEGMENT_FORMAT",
                "每段必须是包含 start 与 end 的对象（1 基访问序号）。", i))
            continue
        start = _as_int(item.get("start"))
        end = _as_int(item.get("end"))
        if start is None or end is None or start > end:
            errors.append(_err(
                "EXCERPT_SEGMENT_FORMAT",
                "每段的 start 与 end 必须是整数，且 start 不大于 end"
                "（1 基访问序号闭区间）。", i))
            continue
        if start < 1 or end > visit_count:
            errors.append(_err(
                "EXCERPT_SEGMENT_RANGE",
                f"第 {i + 1} 段 [{start}, {end}] 超出访问序号范围"
                f"（1~{visit_count}）。", i))
            continue
        segments.append((start, end))
    if errors:
        return None, errors

    # 互不重叠且按演奏先后排列（相邻区间 [1,2],[3,4] 允许）
    for i in range(1, len(segments)):
        if segments[i][0] <= segments[i - 1][1]:
            errors.append(_err(
                "EXCERPT_SEGMENT_ORDER",
                f"第 {i + 1} 段 [{segments[i][0]}, {segments[i][1]}] 与"
                f"第 {i} 段 [{segments[i - 1][0]}, {segments[i - 1][1]}]"
                "重叠或未按演奏先后排列。",
                i))
    if errors:
        return None, errors

    total = sum(e - s + 1 for s, e in segments)
    if not (MIN_EXCERPT_VISITS <= total <= MAX_EXCERPT_VISITS):
        return None, [_err(
            "EXCERPT_VISIT_COUNT",
            f"各段合计须为 {MIN_EXCERPT_VISITS}~{MAX_EXCERPT_VISITS} 次访问，"
            f"当前为 {total} 次。")]

    return segments, []


def _build_rows(visits: list[dict[str, Any]],
                segments: list[tuple[int, int]],
                ) -> tuple[list[dict[str, Any]],
                           list[dict[str, Any]],
                           list[dict[str, Any]]]:
    """把选中访问依序转成摘谱录入行，返回 ``(rows, mapping, seg_info)``。

    - 编号重排为 1 起连续整数；拍数保留；反复 / 房子记号全部清除；
    - 每段首行写入该次访问的生效 BPM；段内仅在生效 BPM 相对上一选中
      访问变化时写入，其余留空沿用。
    """
    rows: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    seg_info: list[dict[str, Any]] = []
    prev_tempo: int | None = None
    for k, (s, e) in enumerate(segments):
        seg_info.append({
            "segment": k + 1,
            "start": s,
            "end": e,
            "visit_count": e - s + 1,
            "first_row": len(rows) + 1,  # 该段首行的新编号（1 起）
        })
        for gi in range(s, e + 1):
            v = visits[gi - 1]
            tempo = v["tempo"]
            # 段首必写（剪接处不沿用前段速度）；段内仅在速度变化时写入
            mark = gi == s or tempo != prev_tempo
            prev_tempo = tempo
            rows.append({
                "number": str(len(rows) + 1),
                "beats": v["beats"],
                "tempo": tempo if mark else None,
                "left_repeat": False,
                "right_repeat": False,
                "right_closes": None,
                "times": None,
                "volta": None,
            })
            mapping.append({
                "row": len(rows),          # 新编号（1 起连续整数）
                "segment": k + 1,          # 所属选段（1 起）
                "visit": {                 # 原访问引用
                    "global_index": v["global_index"],
                    "source_index": v["source_index"],
                    "source_measure": v["source_measure"],
                    "beats": v["beats"],
                    "tempo": tempo,        # 该次访问的生效 BPM
                    "path": v["path"],
                },
            })
    return rows, mapping, seg_info


def build_excerpt(raw: dict[str, Any],
                  ) -> tuple[dict[str, Any] | None,
                             dict[str, Any] | None]:
    """摘谱入口。成功返回 ``(result, None)``；校验失败返回
    ``(None, payload)``，payload 含 ``score_errors`` / ``segment_errors``
    / ``excerpt_errors`` 三组错误列表（可为空数组），选段错误按段
    （0 基）定位。
    """
    measures = raw["measures"]
    segments_raw = raw["segments"]

    # 1) 录入谱面：复用现有完整校验与展开（含 512 上限）
    if not isinstance(measures, list):
        score_errors = [_err(
            "INVALID_REQUEST", "measures 必须是小节对象组成的数组。")]
        score_result = None
    else:
        score_result, score_errors = expand(measures)

    # 2) 选段校验（依赖展开后的访问总数；谱面非法时只做形状检查）
    segments: list[tuple[int, int]] | None = None
    segment_errors: list[dict[str, Any]] = []
    if score_result is not None:
        segments, segment_errors = _validate_segments(
            segments_raw, score_result["visit_count"])
    elif isinstance(segments_raw, list) \
            and MIN_SEGMENTS <= len(segments_raw) <= MAX_SEGMENTS:
        segment_errors = []  # 谱面错误已上报，选段待谱面修复后再校验
    else:
        _ignored, segment_errors = _validate_segments(segments_raw, MAX_VISITS)

    if score_errors or segment_errors:
        return None, {
            "score_errors": score_errors,
            "segment_errors": segment_errors,
            "excerpt_errors": [],
        }
    assert score_result is not None and segments is not None

    # 3) 选中访问 → 摘谱录入行（编号重排、保留拍数、清除反复与房子、
    #    段首写生效 BPM、段内仅在速度变化时写入）
    visits = score_result["visits"]
    rows, mapping, seg_info = _build_rows(visits, segments)

    # 4) 摘谱行自身先复用完整校验与展开（防御：构造行理应始终合法）
    excerpt_result, excerpt_row_errors = expand(rows)
    if excerpt_row_errors or excerpt_result is None:
        return None, {
            "score_errors": [],
            "segment_errors": [],
            "excerpt_errors": excerpt_row_errors or [_err(
                "EXCERPT_ROWS", "摘谱行未能通过现有校验与展开。")],
        }

    # 5) 用现有结构导出生成单 part MusicXML（导出内含往返自检）
    export_result, export_errors = export_musicxml(rows)
    if export_errors or export_result is None:
        return None, {
            "score_errors": [],
            "segment_errors": [],
            "excerpt_errors": export_errors or [_err(
                "MUSICXML_EXCERPT_ROUNDTRIP",
                "摘谱行无法导出为 MusicXML，未产生下载内容。")],
        }
    xml_text = export_result["musicxml"]

    # 6) 回读校验：用现有导入入口重新导入生成的文件，回读演奏带的
    #    拍数与生效 BPM 必须逐行对应所选访问，否则不下载。
    reimp, reimp_errors = import_musicxml(xml_text)
    if reimp_errors or reimp is None:
        return None, {
            "score_errors": [],
            "segment_errors": [],
            "excerpt_errors": [_err(
                "MUSICXML_EXCERPT_ROUNDTRIP",
                "摘谱文件回读失败（往返自检未通过），未产生下载内容。")],
        }
    selected = [visits[gi - 1]
                for s, e in segments for gi in range(s, e + 1)]
    reimp_visits = reimp["expansion"]["visits"]
    mismatch = (
        len(reimp_visits) != len(selected)
        or any(rv["beats"] != sv["beats"] or rv["tempo"] != sv["tempo"]
               for rv, sv in zip(reimp_visits, selected))
    )
    if mismatch:
        return None, {
            "score_errors": [],
            "segment_errors": [],
            "excerpt_errors": [_err(
                "MUSICXML_EXCERPT_ROUNDTRIP",
                "摘谱文件回读后的拍数或生效 BPM 与所选访问不逐行对应"
                "（往返自检未通过），未产生下载内容。")],
        }

    total_beats = sum(v["beats"] for v in selected)
    return {
        "segment_count": len(segments),
        "visit_count": len(selected),
        "total_beats": total_beats,
        "max_segments": MAX_SEGMENTS,
        "max_excerpt_visits": MAX_EXCERPT_VISITS,
        "max_visits": MAX_VISITS,
        "segments": seg_info,
        "rows": rows,          # 摘谱录入行（新编号 1 起连续整数）
        "mapping": mapping,    # 新行 → 原访问序号 / 来源编号 / 遍次路径
        "musicxml": xml_text,
    }, None
