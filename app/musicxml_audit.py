"""MusicXML 小节时值审计。

在不改动录入表格的前提下，检查**现有结构导入支持的单 part 谱面**中
每个小节的各声部时值是否完整：按各节生效拍号（分母为 4，首节必有、
后续沿用，与结构导入同一套规则）与**正整数 divisions**，把节内位置
表示成**有理数拍位**（从小节起点 0 起计的四分音符拍数），逐节按文档
顺序读取 ``<note>`` / ``<backup>`` / ``<forward>``：

- 普通音符与休止符按 ``duration ÷ 当前 divisions`` 推进游标，占用区间
  ``[起点, 终点]`` 记入所属声部；
- ``<chord/>`` 音符继承**前一同声部普通音符**的起点、不推进游标；
  同起点的和弦音在汇总时合成一个占用区间（终点取最大）；
- ``<grace/>`` 音符不占时，直接跳过（不要求 duration，也不参与和弦
  继承）；
- ``<voice>`` 缺省为 ``1``；``<backup>`` / ``<forward>`` 只移动游标，
  不计入任何声部的占用区间（``forward`` 跳过的区域因此会在对应声部
  显示为缺拍）。

每个声部在小节内应恰好铺满 ``[0, 拍数]``：

- 未被占用区间覆盖的区段按**缺拍**列出；
- 同起点合并后仍相交的区段按**非和弦重叠**列出；
- 终点超过小节容量的占用区间按**越过小节边界**列出。

以下情况按**源小节**报错（不给出“通过”结论）：缺少生效的 divisions
或 divisions 不是正整数、duration 缺失或不是正整数、``backup`` 把游标
移到负拍位、孤立 ``<chord/>``（本小节内该声部此前没有普通音符）。
拍号与整体形状问题沿用结构导入的 ``MUSICXML_TIME`` 等错误码。

审计只关心时值：忽略音高、速度、反复与房子记号——缺少速度标记或反复
未配对的文件仍可审计（但同样的文件未必能通过结构导入）。
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


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _pos_int(text: str | None) -> int | None:
    """正整数字符串 → int；缺失 / 非十进制正整数返回 ``None``。"""
    if text is None:
        return None
    t = text.strip()
    if not t or not t.isascii() or not t.isdigit():
        return None
    value = int(t)
    return value if value > 0 else None


def _fmt(pos: Fraction) -> str:
    """有理数拍位 → 紧凑字符串（整数不带分母，如 ``3``、``3/2``）。"""
    if pos.denominator == 1:
        return str(pos.numerator)
    return f"{pos.numerator}/{pos.denominator}"


def _voice_key(voice: str) -> tuple[int, Any]:
    """声部排序：纯数字按数值，其余按字典序排在后。"""
    if voice.isascii() and voice.isdigit():
        return (0, int(voice))
    return (1, voice)


def _duration_of(el: ET.Element) -> int | None:
    """读取 ``<duration>`` 的正整数值；缺失或非法返回 ``None``。"""
    dur_el = _find_local(el, "duration")
    return _pos_int(dur_el.text if dur_el is not None else None)


# ---------------------------------------------------------------------------
# 形状校验（与结构导入一致的单 part 范围）
# ---------------------------------------------------------------------------

def _load_measures(raw_text: Any
                   ) -> tuple[list[ET.Element] | None,
                              list[str] | None,
                              list[dict[str, Any]]]:
    """解析并校验整体形状；返回 ``(measures, labels, errors)``。"""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None, None, [_err("MUSICXML_EMPTY", "审计内容为空。")]

    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError as exc:
        return None, None, [_err(
            "MUSICXML_PARSE",
            f"不是合法的 XML / MusicXML 文件：{exc}",
        )]

    _strip_ns(root)
    if root.tag != "score-partwise":
        if root.tag == "score-timewise":
            return None, None, [_err(
                "MUSICXML_FORMAT",
                "不支持 score-timewise 格式，请先在打谱软件中另存为"
                " score-partwise（分部）形式。",
            )]
        return None, None, [_err(
            "MUSICXML_FORMAT",
            f"根元素必须是 score-partwise，当前为 {root.tag!r}。",
        )]

    part_list = _find_local(root, "part-list")
    if part_list is not None:
        declared = _findall_local(part_list, "score-part")
        if len(declared) != 1:
            return None, None, [_err(
                "MUSICXML_PARTS",
                f"只接收单个 part 的谱面，part-list 中声明了 {len(declared)} 个。",
            )]

    parts = _findall_local(root, "part")
    if len(parts) != 1:
        return None, None, [_err(
            "MUSICXML_PARTS",
            f"只接收单个 part 的谱面，当前包含 {len(parts)} 个 part。",
        )]

    measures = _findall_local(parts[0], "measure")
    n = len(measures)
    if not (MIN_MEASURES <= n <= MAX_MEASURES):
        return None, None, [_err(
            "MUSICXML_MEASURE_COUNT",
            f"小节数量必须在 {MIN_MEASURES}~{MAX_MEASURES} 之间，"
            f"当前为 {n}。",
        )]

    labels = [_measure_label(m.get("number"), i)
              for i, m in enumerate(measures)]
    return measures, labels, []


# ---------------------------------------------------------------------------
# 单小节：游标推进与声部占用区间
# ---------------------------------------------------------------------------

def _walk_measure(measure: ET.Element, divisions_in: int | None,
                  i: int, label: str,
                  ) -> tuple[dict[str, list[tuple[Fraction, Fraction]]],
                             int | None,
                             list[dict[str, Any]]]:
    """按文档顺序推进游标，收集各声部占用区间。

    返回 ``(voices, 节末生效 divisions, errors)``；``voices`` 为
    声部 → ``[(起点, 终点)]``（有理数拍位，未做同起点合并）。
    同类错误在同一小节内只报一次。
    """
    errors: list[dict[str, Any]] = []
    reported: set[tuple[str, str]] = set()
    voices: dict[str, list[tuple[Fraction, Fraction]]] = {}
    last_normal_start: dict[str, Fraction] = {}
    cursor = Fraction(0)
    divisions = divisions_in

    def report(code: str, message: str) -> None:
        if (code, message) in reported:
            return
        reported.add((code, message))
        errors.append(_err(code, message, i, label))

    def need_divisions(kind: str) -> bool:
        if divisions is not None:
            return True
        report(
            "MUSICXML_AUDIT_DIVISIONS",
            f"{kind} 出现时还没有生效的 divisions；请在首个含音符的小节"
            "的 <attributes> 中给出正整数 divisions。",
        )
        return False

    for child in measure:
        tag = child.tag if isinstance(child.tag, str) else ""

        if tag == "attributes":
            div_el = _find_local(child, "divisions")
            if div_el is not None:
                value = _pos_int(div_el.text)
                if value is None:
                    report(
                        "MUSICXML_AUDIT_DIVISIONS",
                        f"divisions 必须是正整数，当前为 "
                        f"{(div_el.text or '').strip()!r}。",
                    )
                else:
                    divisions = value

        elif tag == "note":
            if _find_local(child, "grace") is not None:
                continue  # 装饰音不占时：不要求 duration，不影响和弦继承
            if not need_divisions("音符"):
                continue
            dur = _duration_of(child)
            if dur is None:
                report(
                    "MUSICXML_AUDIT_DURATION",
                    "音符 / 休止符的 duration 必须是正整数（缺失或非法）。",
                )
                continue
            voice_el = _find_local(child, "voice")
            voice = (voice_el.text or "").strip() \
                if voice_el is not None and voice_el.text else ""
            if not voice:
                voice = "1"  # voice 缺省为 1
            length = Fraction(dur, divisions)
            if _find_local(child, "chord") is not None:
                # 和弦音：继承前一同声部普通音符的起点，不推进游标
                start = last_normal_start.get(voice)
                if start is None:
                    report(
                        "MUSICXML_AUDIT_CHORD",
                        f"声部 {voice} 的 <chord/> 音符在本小节内之前没有"
                        "同声部的普通音符（孤立和弦音）。",
                    )
                    continue
            else:
                start = cursor
                cursor += length
                last_normal_start[voice] = start
            voices.setdefault(voice, []).append((start, start + length))

        elif tag == "backup":
            if not need_divisions("backup"):
                continue
            dur = _duration_of(child)
            if dur is None:
                report(
                    "MUSICXML_AUDIT_DURATION",
                    "backup 的 duration 必须是正整数（缺失或非法）。",
                )
                continue
            cursor -= Fraction(dur, divisions)
            if cursor < 0:
                report(
                    "MUSICXML_AUDIT_CURSOR",
                    f"backup 使游标移到负拍位（{_fmt(cursor)}）；"
                    "回退量不能超过本小节已进行的时值。",
                )
                cursor = Fraction(0)  # 钳回小节起点，继续收集后续错误

        elif tag == "forward":
            if not need_divisions("forward"):
                continue
            dur = _duration_of(child)
            if dur is None:
                report(
                    "MUSICXML_AUDIT_DURATION",
                    "forward 的 duration 必须是正整数（缺失或非法）。",
                )
                continue
            cursor += Fraction(dur, divisions)

    return voices, divisions, errors


# ---------------------------------------------------------------------------
# 单声部：同起点合并与三类问题
# ---------------------------------------------------------------------------

def _voice_findings(intervals: list[tuple[Fraction, Fraction]],
                    capacity: Fraction,
                    ) -> tuple[list[tuple[Fraction, Fraction]],
                               list[tuple[Fraction, Fraction]],
                               list[tuple[Fraction, Fraction]],
                               list[tuple[Fraction, Fraction]]]:
    """同起点合并后计算缺拍 / 非和弦重叠 / 越过小节边界。

    返回 ``(merged, missing, overlaps, overflows)``，均为按起点排序的
    拍位区间列表。
    """
    # 同起点（和弦）合成一个占用区间：终点取最大
    by_start: dict[Fraction, Fraction] = {}
    for s, e in intervals:
        prev = by_start.get(s)
        by_start[s] = e if prev is None else max(prev, e)
    merged = sorted(by_start.items())

    overlaps: list[tuple[Fraction, Fraction]] = []
    max_end: Fraction | None = None
    for s, e in merged:
        if max_end is not None and s < max_end:
            overlaps.append((s, min(e, max_end)))
        max_end = e if max_end is None else max(max_end, e)

    overflows = [(s, e) for s, e in merged if e > capacity]

    missing: list[tuple[Fraction, Fraction]] = []
    pos = Fraction(0)
    for s, e in merged:
        lo = min(max(s, Fraction(0)), capacity)
        hi = min(max(e, Fraction(0)), capacity)
        if lo > pos:
            missing.append((pos, lo))
        if hi > pos:
            pos = hi
    if pos < capacity:
        missing.append((pos, capacity))
    return merged, missing, overlaps, overflows


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def audit_musicxml(raw_text: Any
                   ) -> tuple[dict[str, Any] | None,
                              list[dict[str, Any]] | None]:
    """审计单 part MusicXML 的各声部小节时值。

    成功返回 ``(result, None)``；形状 / 拍号 / 时值要素错误返回
    ``(None, errors)``，错误按源小节定位（此时不给出“通过”结论）。
    """
    measures, labels, shape_errors = _load_measures(raw_text)
    if shape_errors:
        return None, shape_errors
    assert measures is not None and labels is not None
    n = len(measures)

    # --- 各节生效拍号（分母为 4，首节必有、后续沿用，同结构导入） --------
    errors: list[dict[str, Any]] = []
    capacities: list[int] = []
    current_beats: int | None = None
    for i, measure in enumerate(measures):
        beats, time_errors = _parse_time(measure, labels[i], i)
        errors.extend(time_errors)
        if beats is not None:
            current_beats = beats
        if i == 0 and beats is None and not time_errors:
            errors.append(_err(
                "MUSICXML_TIME",
                "第 1 小节必须显式给出分母为 4 的拍号。",
                0, labels[0],
            ))
        capacities.append(current_beats if current_beats is not None else 4)

    # --- 逐节推进游标，收集各声部占用区间（divisions 跨节沿用） ----------
    divisions: int | None = None
    walked: list[tuple[dict[str, list[tuple[Fraction, Fraction]]],
                       int | None]] = []
    for i, measure in enumerate(measures):
        voices, divisions, walk_errors = _walk_measure(
            measure, divisions, i, labels[i])
        errors.extend(walk_errors)
        walked.append((voices, divisions))

    if errors:
        errors.sort(key=lambda e: (e["index"] is None, e["index"] or 0,
                                   e["code"]))
        return None, errors

    # --- 汇总：同起点合并，按声部列出缺拍 / 非和弦重叠 / 越过边界 --------
    result_measures: list[dict[str, Any]] = []
    issue_count = 0
    for i in range(n):
        voices, div = walked[i]
        if not voices:
            # 整节没有任何音符：按声部 1 全节缺拍列出
            voices = {"1": []}
        capacity = Fraction(capacities[i])
        voice_entries: list[dict[str, Any]] = []
        for voice in sorted(voices, key=_voice_key):
            merged, missing, overlaps, overflows = _voice_findings(
                voices[voice], capacity)
            issue_count += len(missing) + len(overlaps) + len(overflows)

            def spans(ranges: list[tuple[Fraction, Fraction]]
                      ) -> list[dict[str, str]]:
                return [{"start": _fmt(s), "end": _fmt(e)}
                        for s, e in ranges]

            voice_entries.append({
                "voice": voice,
                "intervals": spans(merged),
                "missing": spans(missing),
                "overlaps": spans(overlaps),
                "overflows": spans(overflows),
            })
        result_measures.append({
            "index": i,
            "number": labels[i],
            "beats": capacities[i],
            "divisions": div,
            "voices": voice_entries,
        })

    return {
        "measure_count": n,
        "issue_count": issue_count,
        "passed": issue_count == 0,
        "measures": result_measures,
    }, None
