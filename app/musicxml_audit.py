"""MusicXML 小节时值审计。

在**不改动录入表格**的前提下，对单个 ``score-partwise`` 文件逐小节核对
各声部的时值占用：按各节生效拍号（分母为 4）与正整数 ``divisions``，
用有理数（``fractions.Fraction``）表示拍位，按声部列出缺拍、非和弦重
叠与越过小节边界的拍位。审计与结构导入相互独立：在导入面板选择本地
文件后可单独发起，结果只展示在导入面板内。

支持范围（与结构导入相同的文件范围）
====================================

- 根元素必须是 ``score-partwise``，全曲恰好 **1 个** part；
- 小节数 2~64，``number`` 属性作为小节编号（仅用于定位）；
- 拍号：只接收分母为 4 的单一 ``<time>``（``beats`` 即本小节拍数）；
  首节必有，后续小节缺省沿用最近一个；
- 速度、反复、替代结尾、调号、谱号等记号与时值无关，一律**忽略**——
  即使它们会导致结构导入失败，也不影响审计结论。

游标模型
========

每个小节内按文档顺序读取 ``note`` / ``backup`` / ``forward``：

- ``<attributes><divisions>`` 更新生效 divisions（跨小节沿用），必须是
  正整数；遇到需要解释 duration 的元素时还没有生效 divisions 即报错；
- 普通音符与休止符：起点为当前游标，按 ``duration / divisions`` 拍推
  进游标，并占用 ``[起点, 起点+时值)`` 区间；
- ``<chord/>`` 音符：继承**前一同声部普通音符**的起点，不推进游标；
  同一起点的和弦合并成**一个**占用区间（终点取组内最大）；
- ``<grace/>`` 音符不占时：不校验 duration、不推进游标、不参与区间；
- ``<backup>`` 把游标回拨 ``duration / divisions`` 拍，``<forward>``
  前移同样拍数；二者作用于全小节唯一的游标（不分声部）；
- ``<voice>`` 缺省为 ``1``。

审计结论
========

- **硬错误**（按源小节报错，不显示通过）：缺少 / 非正整数 divisions、
  非法 duration（非正整数）、``backup`` 把游标移到负拍位、孤立 chord
  （前面没有同声部普通音符）。每个小节只报告第一个硬错误；
- **时值问题**（审计通过结构检查后按声部列出）：
  - ``gap`` 缺拍：``[0, 本小节拍数)`` 内未被任何区间覆盖的拍位；
  - ``overlap`` 非和弦重叠：同声部不同和弦组的区间相互交叠；
  - ``overflow`` 越过小节边界：区间终点超出本小节拍号拍数。

拍位一律是**距小节开头的拍数偏移**（0 起，1 拍 = 1 个四分音符），以
``p/q`` 有理数文本输出（分母为 1 时只写分子）。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import Any

from .expander import MAX_MEASURES, MIN_MEASURES
from .musicxml_import import (
    _err,
    _find_local,
    _findall_local,
    _measure_label,
    _parse_time,
    _strip_ns,
)


def _fmt_pos(pos: Fraction) -> str:
    """拍位输出为 ``p/q`` 文本（分母为 1 时只写分子）。"""
    if pos.denominator == 1:
        return str(pos.numerator)
    return f"{pos.numerator}/{pos.denominator}"


def _voice_sort_key(voice: str) -> tuple[int, Any]:
    """声部排序：数字声部按数值在前，其余按文本在后。"""
    return (0, int(voice)) if voice.isdigit() else (1, voice)


def _issue(kind: str, index: int, measure: str, voice: str,
           start: Fraction, end: Fraction, message: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "index": index,
        "measure": measure,
        "voice": voice,
        "start": _fmt_pos(start),
        "end": _fmt_pos(end),
        "message": message,
    }


def _parse_positive_int(text: str) -> int | None:
    """正整数字符串返回 int，否则 None。"""
    if not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


def audit_musicxml(raw_text: Any
                   ) -> tuple[dict[str, Any] | None,
                              list[dict[str, Any]] | None]:
    """审计单 part MusicXML 的小节时值。

    成功返回 ``(result, None)``，``result`` 含 ``measure_count``、
    ``issue_count`` 与 ``issues``（缺拍 / 非和弦重叠 / 越过小节边界，
    按小节、声部、拍位升序）；文件范围不符或存在硬错误时返回
    ``(None, errors)``，错误按源小节定位，不显示通过。
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None, [_err("MUSICXML_EMPTY", "审计内容为空。")]

    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError as exc:
        return None, [_err(
            "MUSICXML_PARSE",
            f"不是合法的 XML / MusicXML 文件：{exc}",
        )]

    _strip_ns(root)
    if root.tag != "score-partwise":
        if root.tag == "score-timewise":
            return None, [_err(
                "MUSICXML_FORMAT",
                "不支持 score-timewise 格式，请先在打谱软件中另存为"
                " score-partwise（分部）形式。",
            )]
        return None, [_err(
            "MUSICXML_FORMAT",
            f"根元素必须是 score-partwise，当前为 {root.tag!r}。",
        )]

    part_list = _find_local(root, "part-list")
    if part_list is not None:
        declared = _findall_local(part_list, "score-part")
        if len(declared) != 1:
            return None, [_err(
                "MUSICXML_PARTS",
                f"只接收单个 part 的谱面，part-list 中声明了 {len(declared)} 个。",
            )]

    parts = _findall_local(root, "part")
    if len(parts) != 1:
        return None, [_err(
            "MUSICXML_PARTS",
            f"只接收单个 part 的谱面，当前包含 {len(parts)} 个 part。",
        )]

    measures = _findall_local(parts[0], "measure")
    n = len(measures)
    if not (MIN_MEASURES <= n <= MAX_MEASURES):
        return None, [_err(
            "MUSICXML_MEASURE_COUNT",
            f"小节数量必须在 {MIN_MEASURES}~{MAX_MEASURES} 之间，"
            f"当前为 {n}。",
        )]

    labels = [_measure_label(m.get("number"), i)
              for i, m in enumerate(measures)]

    errors: list[dict[str, Any]] = []
    scored_issues: list[tuple[tuple, dict[str, Any]]] = []
    divisions: int | None = None  # 生效 divisions（跨小节沿用）
    beats: int | None = None      # 生效拍号分子（分母恒为 4）

    for i, measure in enumerate(measures):
        label = labels[i]

        # 拍号：与结构导入同一套规则（分母 4、首节必有、后续沿用）
        new_beats, time_errors = _parse_time(measure, label, i)
        errors.extend(time_errors)
        if new_beats is not None:
            beats = new_beats
        if i == 0 and new_beats is None and not time_errors:
            errors.append(_err(
                "MUSICXML_TIME",
                "第 1 小节必须显式给出分母为 4 的拍号。",
                0, label,
            ))

        cursor = Fraction(0)
        # voice -> 当前和弦组 [start, end]（同起点和弦合并为一个区间）
        last_group: dict[str, list[Fraction]] = {}
        # voice -> 已完成的占用区间列表
        intervals: dict[str, list[list[Fraction]]] = {}
        measure_error: dict[str, Any] | None = None

        def fail(code: str, message: str) -> None:
            nonlocal measure_error
            if measure_error is None:
                measure_error = _err(code, message, i, label)

        for child in measure:
            tag = child.tag if isinstance(child.tag, str) else None
            if tag == "attributes":
                # divisions 更新即使在本小节已有硬错误后也照常生效，
                # 保证后续小节的沿用判断不受本小节错误影响。
                div_el = _find_local(child, "divisions")
                if div_el is not None:
                    text = (div_el.text or "").strip()
                    value = _parse_positive_int(text)
                    if value is None:
                        fail("MUSICXML_AUDIT_DIVISIONS",
                             f"divisions 必须是正整数，当前为 {text!r}。")
                    else:
                        divisions = value
                continue
            if tag not in ("note", "backup", "forward"):
                continue
            if measure_error is not None or beats is None:
                # 本小节已有硬错误 / 无生效拍号：时值无法解释，跳过时值处理
                continue
            if tag == "note" and _find_local(child, "grace") is not None:
                continue  # grace 不占时

            dur_el = _find_local(child, "duration")
            dur_text = (dur_el.text or "").strip() if dur_el is not None else ""
            dur_value = _parse_positive_int(dur_text)
            if dur_value is None:
                kind = {"note": "音符", "backup": "backup",
                        "forward": "forward"}[tag]
                fail("MUSICXML_AUDIT_DURATION",
                     f"{kind}的 duration 必须是正整数（以 divisions 为单"
                     f"位），当前为 {dur_text!r}。")
                continue
            if divisions is None:
                fail("MUSICXML_AUDIT_DIVISIONS",
                     "缺少 divisions（attributes/divisions），"
                     "无法解释 duration。")
                continue
            dur = Fraction(dur_value, divisions)

            if tag == "backup":
                cursor -= dur
                if cursor < 0:
                    fail("MUSICXML_AUDIT_CURSOR",
                         f"backup 使游标移到负拍位（{_fmt_pos(cursor)} 拍）。")
                continue
            if tag == "forward":
                cursor += dur
                continue

            # 普通音符 / 休止符 / chord（非 grace）
            voice_el = _find_local(child, "voice")
            voice = (voice_el.text or "").strip() if voice_el is not None else ""
            if not voice:
                voice = "1"  # voice 缺省为 1
            if _find_local(child, "chord") is not None:
                group = last_group.get(voice)
                if group is None:
                    fail("MUSICXML_AUDIT_CHORD",
                         f"声部 {voice} 的 chord 音符之前没有同声部普通音"
                         "符（孤立 chord）。")
                    continue
                # 继承前一同声部普通音符的起点，不推进游标；同起点和弦
                # 合并为一个区间，终点取组内最大。
                group[1] = max(group[1], group[0] + dur)
            else:
                group = [cursor, cursor + dur]
                last_group[voice] = group
                intervals.setdefault(voice, []).append(group)
                cursor += dur

        if measure_error is not None:
            errors.append(measure_error)
            continue
        if beats is None:
            continue  # 拍号错误已记录，无法核对本小节时值

        measure_len = Fraction(beats)
        if not intervals:
            # 全小节没有任何非 grace 音符：默认声部整节缺拍
            intervals = {"1": []}
        for voice in sorted(intervals, key=_voice_sort_key):
            ivs = sorted(intervals[voice], key=lambda g: (g[0], g[1]))

            def add(kind: str, start: Fraction, end: Fraction,
                    message: str) -> None:
                scored_issues.append(
                    ((i, _voice_sort_key(voice), start, end, kind),
                     _issue(kind, i, label, voice, start, end, message)))

            # 非和弦重叠（同起点和弦已合并，剩余交叠即非和弦重叠）
            max_end: Fraction | None = None
            for start, end in ivs:
                if max_end is not None and start < max_end:
                    add("overlap", start, min(end, max_end),
                        f"非和弦重叠：拍位 {_fmt_pos(start)}~"
                        f"{_fmt_pos(min(end, max_end))} 被多个非同和弦音"
                        "符同时占用。")
                max_end = end if max_end is None else max(max_end, end)
            # 缺拍：[0, 本小节拍数) 内未被覆盖的拍位
            pos = Fraction(0)
            for start, end in ivs:
                if start > pos:
                    add("gap", pos, start,
                        f"缺拍：拍位 {_fmt_pos(pos)}~{_fmt_pos(start)} "
                        "没有任何音符或休止符。")
                pos = max(pos, end)
            if pos < measure_len:
                add("gap", pos, measure_len,
                    f"缺拍：拍位 {_fmt_pos(pos)}~{_fmt_pos(measure_len)} "
                    "没有任何音符或休止符。")
            # 越过小节边界
            for start, end in ivs:
                if end > measure_len:
                    add("overflow", start, end,
                        f"越过小节边界：拍位 {_fmt_pos(start)}~"
                        f"{_fmt_pos(end)} 超出小节长度"
                        f"（{_fmt_pos(measure_len)} 拍）。")

    if errors:
        errors.sort(key=lambda e: (e["index"] is None, e["index"] or 0,
                                   e["code"]))
        return None, errors

    scored_issues.sort(key=lambda t: t[0])
    issues = [issue for _, issue in scored_issues]
    return {
        "measure_count": n,
        "issue_count": len(issues),
        "issues": issues,
    }, None
