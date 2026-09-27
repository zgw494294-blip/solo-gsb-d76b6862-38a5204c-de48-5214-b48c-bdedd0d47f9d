"""MusicXML 结构导入。

把单个 ``score-partwise`` 文件的**结构信息**转换成录入行（同
``/api/expand`` 的小节数组），随后直接走现有的完整校验与展开。

支持范围（其余结构一律拒绝，按源小节报错，不改当前表格）
================================================================

- 根元素必须是 ``score-partwise``，且全曲恰好 **1 个** part；
- 小节数 2~64，``number`` 属性作为小节编号（沿用现有编号规则）；
- 拍号：只接收分母为 4 的 ``<time>``（``beats`` 即拍数）；首节必有，
  后续小节沿用最近一个；``senza-misura`` / 混合拍号不支持；
- 速度：读取小节**起始处**（首个 ``<note>`` 之前）的整数 BPM——
  ``<sound tempo="..."/>`` 或 ``<direction>`` 里单位为四分音符的
  ``<metronome>``（``beat-unit=quarter`` + ``per-minute``）；首节必有，
  后续留空沿用；
- 反复：``<repeat direction="forward"/>`` 与 ``direction="backward"``
  必须按 LIFO 栈配对嵌套；backward 的 ``times``（缺省 2，限 2~4）写到
  forward 所在起点行；同一终点后第 2+ 遍房子上的 backward 识别为该反复的
  次级终点，不单独配对；
- 同线特例按**反复所属层级与房子的实际闭合边界**判定：编号 1 房子的右
  barline start 只有在该房子**也在同一物理边界闭合**（真正的同线闭合）
  且同线有 backward 时，才是规范“起点兼终点”写法；若同线的 backward
  属于内层反复、外层房子在后面的小节才闭合，则内层终点留在线前小节，
  外层房子从线后小节覆盖，互不挪动；
- 替代结尾：``number`` 为单个 1~4 整数的 ``<ending type="start">`` 到
  对应 ``stop`` / ``discontinue``（含同一小节边界上“新房子开始隐式闭合
  旧房子”的写法）覆盖的小节整体映射为该遍次房子，包括主终点之后的尾部
  房子；右 barline 上的 start 从**下一小节**起覆盖，左 barline 上的
  start 从所在小节起覆盖（真正同线闭合的规范写法除外，见
  “房子起点的覆盖语义”）；同一道小节线两侧的记号按**物理边界**配对——
  右侧 barline 上的 start 配合下一小节左 barline 上的 stop / backward
  与写在本小节右 barline 上等效；
- 忽略一切音符内容、``<backup>`` / ``<forward>``、调号、谱号等。

边界模型
========

MusicXML 的反复 / 房子记号挂在小节的 ``<barline location="right|left">``
上。转换时把每个记号归到一个**小节间边界** ``k``：

- 第 ``i`` 小节右 barline → 边界 ``i``；左 barline → 边界 ``i-1``；
- forward 在边界 ``k`` → 起点行 ``k``（``k`` 可为 0）；
- backward 在边界 ``k``：
  - 默认终点行 ``k``；
  - 只有与编号 1 房子的右 barline ``start`` **且该房子在同一边界闭合**
    （真正的同线闭合，见“房子起点的覆盖语义”）共线时，主终点才落在
    该房子覆盖的线前小节（此边界与默认终点行重合，无需挪动）；
  - 与**更外层**编号 1 房子 start 共线、但该房子在后面的边界才闭合时，
    该 backward 属于内层反复，终点保留在线前小节，不随外层房子移动；
- 第 2+ 遍房子 ``stop`` / ``discontinue`` 与 backward 在同一侧 barline
  （或另一侧闭合、且另一侧没有自己的 backward）时，该 backward 是次级
  终点：只置该行右标记，不弹反复栈；同线两侧各有一个 backward 时各认
  本侧闭合记号，外层 backward 不被内层房子误吞。**同一道物理小节线两侧
  各有一个 backward（一侧内层第 2+ 房子的次级终点、另一侧更外层反复的
  主终点）时，两个终点落同一录入行却不会被合并**：该行右标记置真，并用
  ``right_closes``（[内层起点行, 外层起点行]）保留两个终点的反复归属，
  展开时在该边界先完成内层房子、再闭合外层反复。预配对（房子覆盖判定）
  与正式配对（反复落行）共用 :func:`_classify_secondary_backwards` 的
  同一套跨侧 / 同线双 backward / 层级判定，结论不会随记号写在线的哪一
  侧而分叉。

一道物理小节线就是小节 ``k`` 的右 barline 与小节 ``k+1`` 的左 barline
共同指向的边界 ``k``；配对只认边界、不认记号写在线的哪一侧。

房子起点的覆盖语义
==================

``<ending type="start">`` 表示房子括号从这道小节线起向**后**张开，因此：

- **右 barline 上的 start**（边界 ``b``）从**下一小节** ``b+1`` 开始覆盖，
  所在小节 ``b`` 仍属于线另一侧的公共段——不能把它误标成该房子；
- **左 barline 上的 start**（边界 ``b``，挂在第 ``b+1`` 小节开头）
  从所在小节 ``b+1`` 开始覆盖（与“右 barline start 在边界后第一小节”
  是同一物理位置的两种写法）。

唯一例外是 MusicXML 规范里“编号 1 房子 start 与 backward 同线”的写法：
backward 就在该边界上（任意一侧）**且房子自身也在这道线闭合**（闭合
边界 == start 边界），这道线同时是第一房子的起点与主终点，所在小节 ``b``
即房子覆盖的唯一小节。判定必须同时看反复所属的层级与房子的实际闭合
边界，因此内层反复的终点与**更外层**第一房子的右 barline start 共线时，
只要外层房子在后面的小节才闭合，就不是同线特例：内层终点留在线前小节，
外层第一房子仍从线后小节 ``b+1`` 起覆盖、到它真正的 stop 边界结束，
外层终点落在房子末节。两种主流写法都能正确落行：

- 公共小节右 barline 只有房子 start、backward 在房子末节右 barline：
  房子从下一小节开始，公共小节两遍都演；
- start 与 backward 同线且房子同线闭合：所在小节就是 {1 房子，主终点
  落在它上面——无论 backward / stop 写在该线右侧（本小节右 barline）
  还是左侧（下一小节左 barline），都按同一物理边界识别；第二房子从
  线后小节开始。

这样转换出的行与手工录入完全同构，直接复用 :func:`expander.expand`。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .expander import (
    MAX_MEASURES,
    MAX_TEMPO,
    MIN_MEASURES,
    MIN_TEMPO,
    expand,
)


def _err(code: str, message: str, index: int | None = None,
         measure: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "index": index,
        "measure": measure,
    }


# ---------------------------------------------------------------------------
# XML 基础
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    """去掉命名空间前缀（{ns}local → local）。"""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = _local(el.tag)


def _findall_local(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if isinstance(c.tag, str) and c.tag == name]


def _find_local(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if isinstance(c.tag, str) and c.tag == name:
            return c
    return None


def _measure_label(number: str | None, i: int) -> str:
    """错误信息中使用的小节标识（与 expander 行错误的定位口径一致）。"""
    if number is not None and number.strip():
        return number.strip()
    return f"#{i + 1}"


# ---------------------------------------------------------------------------
# 单小节：拍号
# ---------------------------------------------------------------------------

def _parse_time(measure: ET.Element, label: str, i: int,
                ) -> tuple[int | None, list[dict[str, Any]]]:
    """读取小节内 ``<attributes>/<time>``，返回 ``(beats, errors)``。

    本小节无拍号时返回 ``(None, [])``（沿用最近一个）。
    """
    times: list[ET.Element] = []
    for attrs in _findall_local(measure, "attributes"):
        times.extend(_findall_local(attrs, "time"))
    if not times:
        return None, []
    if len(times) > 1:
        return None, [_err(
            "MUSICXML_TIME",
            "同一小节内出现多个拍号（中途变拍号），暂不支持。",
            i, label,
        )]

    time_el = times[0]
    if _find_local(time_el, "senza-misura") is not None:
        return None, [_err(
            "MUSICXML_TIME",
            "不支持无拍号（senza-misura）小节；只接收分母为 4 的拍号。",
            i, label,
        )]

    beats_list = _findall_local(time_el, "beats")
    beat_types = _findall_local(time_el, "beat-type")
    if not beats_list or not beat_types:
        return None, [_err(
            "MUSICXML_TIME",
            "拍号必须同时包含 beats 与 beat-type，且只接收分母为 4 的拍号。",
            i, label,
        )]
    if len(beats_list) != 1 or len(beat_types) != 1:
        return None, [_err(
            "MUSICXML_TIME",
            "不支持混合拍号（多个 beats / beat-type）；只接收单一的分母为 4 的拍号。",
            i, label,
        )]

    beats_text = (beats_list[0].text or "").strip()
    type_text = (beat_types[0].text or "").strip()
    if not beats_text or not beats_text.isdigit():
        return None, [_err(
            "MUSICXML_TIME",
            f"拍号分子必须是正整数，当前为 {beats_text!r}。",
            i, label,
        )]
    beats = int(beats_text)
    if type_text != "4":
        return None, [_err(
            "MUSICXML_TIME",
            f"只接收分母为 4 的拍号，当前分母为 {type_text}。",
            i, label,
        )]
    if not (1 <= beats <= 128):
        return None, [_err(
            "MUSICXML_TIME",
            f"拍数（拍号分子）必须在 1~128 之间，当前为 {beats}。",
            i, label,
        )]
    return beats, []


# ---------------------------------------------------------------------------
# 单小节：起始处 BPM
# ---------------------------------------------------------------------------

def _bpm_from_metronome(metro: ET.Element
                        ) -> tuple[str | None, str | None]:
    """从 ``<metronome>`` 取 ``(per-minute 文本, 问题原因)``。

    只接收四分音符为一拍：``<beat-unit>quarter</beat-unit>``（不带
    ``beat-unit-dot``）加 ``<per-minute>``。
    """
    unit = _find_local(metro, "beat-unit")
    dotted = _find_local(metro, "beat-unit-dot")
    per_min = _find_local(metro, "per-minute")
    if per_min is None:
        return None, "metronome 缺少 per-minute。"
    if dotted is not None or unit is None or (unit.text or "").strip() != "quarter":
        return None, "只接收四分音符为一拍（beat-unit=quarter）的速度标记。"
    return (per_min.text or "").strip(), None


def _parse_int_bpm(text: str) -> int | None:
    """合法整数 BPM（如 "90"、"120.0"）返回 int，否则 None。"""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != int(value):
        return None
    return int(value)


def _parse_tempo(measure: ET.Element, label: str, i: int,
                 ) -> tuple[int | None, list[dict[str, Any]]]:
    """读取小节起始处（首个 ``<note>`` 之前）的 BPM。

    来源两种：直接子元素 ``<sound tempo>``；或 ``<direction>`` 内的
    ``<sound tempo>`` / 四分音符单位 ``<metronome><per-minute>``。
    """
    candidates: list[tuple[str, str]] = []  # (文本, 来源)
    errors: list[dict[str, Any]] = []
    for child in measure:
        tag = child.tag if isinstance(child.tag, str) else None
        if tag == "note":
            break  # 起始处结束：其后的标记一律忽略
        if tag == "sound":
            tempo = child.get("tempo")
            if tempo is not None and tempo.strip():
                candidates.append((tempo.strip(), "sound tempo"))
        elif tag == "direction":
            # direction/sound
            sound = _find_local(child, "sound")
            if sound is not None:
                tempo = sound.get("tempo")
                if tempo is not None and tempo.strip():
                    candidates.append((tempo.strip(), "sound tempo"))
            # direction/direction-type/metronome
            for dtype in _findall_local(child, "direction-type"):
                for metro in _findall_local(dtype, "metronome"):
                    text, reason = _bpm_from_metronome(metro)
                    if reason:
                        errors.append(_err(
                            "MUSICXML_TEMPO",
                            f"小节起始处的速度标记不受支持：{reason}",
                            i, label,
                        ))
                    elif text:
                        candidates.append((text, "metronome per-minute"))

    bpm: int | None = None
    if candidates:
        texts = {c[0] for c in candidates}
        if len(texts) > 1:
            errors.append(_err(
                "MUSICXML_TEMPO",
                "小节起始处出现多个数值不一致的速度标记，无法确定 BPM。",
                i, label,
            ))
        else:
            bpm = _parse_int_bpm(next(iter(texts)))
            if bpm is None:
                errors.append(_err(
                    "MUSICXML_TEMPO",
                    f"速度标记必须是整数 BPM，当前为 {next(iter(texts))!r}。",
                    i, label,
                ))
            elif not (MIN_TEMPO <= bpm <= MAX_TEMPO):
                errors.append(_err(
                    "MUSICXML_TEMPO",
                    f"速度标记必须是 {MIN_TEMPO}~{MAX_TEMPO} 之间的整数（BPM），"
                    f"当前为 {bpm}。",
                    i, label,
                ))
                bpm = None
    return bpm, errors


# ---------------------------------------------------------------------------
# 记号事件：barline → 小节间边界
# ---------------------------------------------------------------------------

# 事件类型元组字段：
# ending: ("ending", side, boundary, src, etype, number_text, order)
# repeat: ("repeat", side, boundary, src, direction, times_text, order)


def _parse_ending_number(text: str | None) -> tuple[int | None, str | None]:
    """``<ending number>`` 只接收单个 1~4 整数。返回 (number, 问题)。"""
    if text is None or not text.strip():
        return None, "ending 缺少 number 属性。"
    value = text.strip()
    if not value.isdigit() or not (1 <= int(value) <= 4):
        return None, f"替代结尾编号只支持单个 1~4 的整数，当前为 {value!r}。"
    return int(value), None


def _collect_events(measures: list[ET.Element], labels: list[str],
                    ) -> tuple[list[tuple], list[dict[str, Any]]]:
    """扫描各小节 barline，生成按小节阅读顺序排列的记号事件与错误。"""
    events: list[tuple] = []
    errors: list[dict[str, Any]] = []
    order = 0  # 全文档内的记号出现序号，保证同 (边界, side) 内保持原序

    for i, measure in enumerate(measures):
        label = labels[i]
        for barline in _findall_local(measure, "barline"):
            loc = (barline.get("location") or "right").strip()
            if loc not in ("right", "left"):
                errors.append(_err(
                    "MUSICXML_BARLINE",
                    f"不支持的 barline 位置 {loc!r}（仅 right / left）。",
                    i, label,
                ))
                continue
            boundary = i if loc == "right" else i - 1
            # 同一 barline 可含多个 ending（MuseScore 常把旧房子 stop 与
            # 新房子 start 写在同一个左 barline），以及至多一个 repeat。
            for ending in _findall_local(barline, "ending"):
                etype = (ending.get("type") or "").strip()
                if etype not in ("start", "stop", "discontinue"):
                    errors.append(_err(
                        "MUSICXML_BARLINE",
                        f"不支持的 ending 类型 {etype!r}"
                        "（仅 start / stop / discontinue）。",
                        i, label,
                    ))
                else:
                    number, reason = _parse_ending_number(ending.get("number"))
                    if reason:
                        errors.append(_err("MUSICXML_ENDING", reason, i, label))
                    else:
                        events.append(("ending", loc, boundary, i,
                                       etype, number, order))
                        order += 1
            repeats_here = _findall_local(barline, "repeat")
            if len(repeats_here) > 1:
                errors.append(_err(
                    "MUSICXML_BARLINE",
                    "同一 barline 上出现多个 repeat 记号，暂不支持。",
                    i, label,
                ))
            for repeat in repeats_here:
                direction = (repeat.get("direction") or "").strip()
                if direction not in ("forward", "backward"):
                    errors.append(_err(
                        "MUSICXML_BARLINE",
                        f"不支持的 repeat direction {direction!r}"
                        "（仅 forward / backward）。",
                        i, label,
                    ))
                else:
                    events.append(("repeat", loc, boundary, i,
                                   direction, repeat.get("times"), order))
                    order += 1

    # 同一小节边界（位于小节 b 与 b+1 之间）按阅读顺序：先小节 b 右
    # barline（终点类记号：stop / backward），再小节 b+1 左 barline
    # （起点类记号：start / forward）；同一 barline 内保持文档顺序。
    events.sort(key=lambda e: (e[2], 0 if e[1] == "right" else 1, e[6]))
    return events, errors


# ---------------------------------------------------------------------------
# 共用：次级 backward 的层级判定（预配对与正式配对必须结论一致）
# ---------------------------------------------------------------------------

def _classify_secondary_backwards(events: list[tuple]) -> set[int]:
    """返回被识别为**次级终点**的 backward 事件 order 集合。

    一道物理边界（小节 ``b`` 右 barline 与小节 ``b+1`` 左 barline 之间）
    两侧各至多有一个 backward；第 2+ 遍房子的闭合记号（stop /
    discontinue）可写在任意一侧。对某一侧的 backward：

    - 本侧就有第 2+ 房子闭合记号 → 该 backward 是这个房子的终点（次级）；
    - 本侧有 {1 等非第 2+ 闭合记号 → 本 backward 是主终点；
    - 本侧没有闭合记号、另一侧有第 2+ 房子闭合记号且另一侧**没有自己的
      backward** 时，按物理边界跨侧等效，该 backward 仍为次级
      （stop 写在某节右侧、次级 backward 写在下一节左侧正是此情形）；
    - 另一侧**有自己的 backward**（嵌套反复：另一侧是内层第 2+ 房子与
      内层终点）时，本侧 backward 是更外层反复的主终点，不被对侧内层
      房子的 stop 误吞。

    配对前的房子记号尚未校验，这里直接以原始事件为准，与 Pass 2 的
    LIFO 栈校验完全独立——无效记号（stop 无 start 等）仍由 Pass 1 /
    Pass 2 按源小节报错，分类本身不改变错误结论。
    """
    stop_numbers: dict[tuple[int, str], set[int]] = {}
    backward_sides: dict[int, set[str]] = {}
    for ev in events:
        kind = ev[0]
        if kind == "ending" and ev[4] in ("stop", "discontinue"):
            stop_numbers.setdefault((ev[2], ev[1]), set()).add(ev[5])
        elif kind == "repeat" and ev[4] == "backward":
            backward_sides.setdefault(ev[2], set()).add(ev[1])

    secondary: set[int] = set()
    for ev in events:
        if ev[0] != "repeat" or ev[4] != "backward":
            continue
        boundary, side, order = ev[2], ev[1], ev[6]
        own = stop_numbers.get((boundary, side), set())
        if any(num >= 2 for num in own):
            secondary.add(order)  # 本侧就有第 2+ 房子闭合
            continue
        if own:
            continue  # 本侧有 {1 等闭合记号：本 backward 是主终点
        other = "left" if side == "right" else "right"
        opp = stop_numbers.get((boundary, other), set())
        if not any(num >= 2 for num in opp):
            continue  # 整条物理线上没有第 2+ 房子闭合
        # 第 2+ 闭合只在另一侧：另一侧另有自己的 backward 时属于嵌套
        # （那是内层房子与内层终点），本侧 backward 仍是外层主终点。
        if other not in backward_sides.get(boundary, set()):
            secondary.add(order)
    return secondary


# ---------------------------------------------------------------------------
# Pass 1：替代结尾配对（start → stop/discontinue/下一房子 start）
# ---------------------------------------------------------------------------

def _match_endings(events: list[tuple], n: int, labels: list[str],
                   secondary_back: set[int],
                   ) -> tuple[list[tuple[int, int, int, int]],
                              list[dict[str, Any]]]:
    """配对房子并算出覆盖小节。

    记号挂在小节边界的某一侧，覆盖范围由 side 决定：

    - start：房子括号从这道线起向后张开。左 barline 上的 start（边界
      ``b``，位于小节 ``b+1`` 开头）与右 barline 上的 start（边界 ``b``，
      位于小节 ``b`` 末尾）物理上是同一道线，覆盖的第一个小节都是线后的
      ``b+1``；右 barline 所在小节 ``b`` 属于线另一侧的公共段，**不**
      计入房子。
    - 唯一例外：右 barline 上编号 1 房子的 start 与 backward 在同一
      物理小节线（任意一侧：本小节右 barline 或下一小节左 barline），
      **且该房子自身就在这道线闭合**（闭合边界 == start 边界，即真正的
      同线闭合），此线即房子的起点兼终点，覆盖的第一个（也是最后一个）
      小节就是所在小节 ``b``。若同线的 backward 属于内层反复、外层房子
      在后面的边界才闭合，则不是特例：外层房子仍从 ``b+1`` 起覆盖，
      内层 backward 的终点保留在线前小节 ``b``；
    - stop / discontinue：闭合记号所在的物理线位于小节 ``b`` 与
      ``b+1`` 之间（无论写成小节 ``b`` 的右 barline 还是小节 ``b+1``
      的左 barline），覆盖的最后一个小节都是 ``b``；
    - 同一小节边界另一侧的下一个房子 start 可**隐式闭合**旧房子
      （规范允许省去旧房子的显式 stop）：旧房子最后一个小节就是
      闭合边界处的小节；仅当该闭合边界与旧 start 边界相同（旧 start
      在右 barline）且同线有 backward 时，才按同线规范写法把线前小节
      计入旧房子。

    返回：

    - ``covers``：``(number, lo, hi, src_index)``，房子覆盖小节
      ``lo..hi``（含端点）；
    - ``errors``。
    """
    active: tuple[int, int, int, str, int] | None = None
    # (number, lo, hi, src_index)
    covers: list[tuple[int, int, int, int]] = []
    errors: list[dict[str, Any]] = []

    # 同线规范写法的判定不能只看房子记号：嵌套反复里“内层终点 + 外层
    # {1 start”共线与真正的“{1 start 与本层 backward 同线”在房子事件
    # 序列上完全同构，区别只在 backward 实际闭合的是哪一层 forward。
    # 先按与 Pass 2 相同的顺序（同边界右 barline 先于左 barline）与
    # **同一套层级规则**（_classify_secondary_backwards：本侧 / 跨侧
    # 第 2+ 房子闭合、同线双 backward 各认本侧）做一次 LIFO 预配对，
    # 得到：
    #
    # - ``back_owner``：每个主 backward 事件 → 它闭合的 forward 起点行
    #   （无配对记 -1，交给 Pass 2 报错）；
    # - ``forward_open_before``：各边界右 barline 处理**前**栈顶 forward
    #   的起点行，即该边界右 barline start 的房子在“线前小节”视角下
    #   所属的最内层反复。``secondary_back`` 由入口统一预计算，与 Pass 2
    #   共用，二者对跨侧闭合 / 同线双 backward 的判定不会分叉。
    rep_on_side: dict[tuple[int, str], list[tuple]] = {}
    back_on_bound: dict[int, list[tuple]] = {}
    for ev in events:
        if ev[0] == "repeat":
            rep_on_side.setdefault((ev[2], ev[1]), []).append(ev)
            if ev[4] == "backward":
                back_on_bound.setdefault(ev[2], []).append(ev)

    back_owner: dict[int, int] = {}
    forward_open_before: dict[int, int | None] = {}
    fstack: list[int] = []
    for b in sorted({ev[2] for ev in events if ev[0] == "repeat"}):
        forward_open_before[b] = fstack[-1] if fstack else None
        for side in ("right", "left"):
            for ev in rep_on_side.get((b, side), []):
                if ev[4] == "forward":
                    fstack.append(b + 1)
                elif ev[6] in secondary_back:
                    pass  # 次级终点（含跨侧闭合）：不弹栈
                elif fstack:
                    back_owner[ev[6]] = fstack.pop()
                else:
                    back_owner[ev[6]] = -1

    def start_lo(start_boundary: int, side: str, number: int,
                 close_boundary: int, close_side: str | None) -> int:
        # 默认：房子从线后第一小节开始。右 barline 的编号 1 start 只有在
        # 房子自身也在这道线闭合（close == start，真正的同线闭合）、且
        # 该物理线上存在一个**属于本层反复**（闭合的就是 start 处理前
        # 栈顶 forward）的主 backward 时，才是规范的“单小节 {1 房子兼
        # 主终点”写法，lo 取线前所在小节。内层终点与外层房子 start
        # 共线时内层 backward 闭合的是更内层 forward，不满足条件，lo 仍
        # 取线后小节，内层终点留在原位。显式闭合只查闭合记号所在一侧；
        # 隐式闭合等价于闭合在另一侧（左 barline）。
        if side == "right" and number == 1 \
                and close_boundary == start_boundary:
            owner = forward_open_before.get(start_boundary)
            if owner is not None:
                need_sides = ("right", "left") if close_side is None \
                    else (close_side,)
                for ev in back_on_bound.get(start_boundary, []):
                    if ev[1] in need_sides \
                            and back_owner.get(ev[6]) == owner:
                        return start_boundary
        return start_boundary + 1

    def stop_hi(close_boundary: int) -> int:
        # 闭合记号所在的物理线位于小节 b 与 b+1 之间（无论写成小节 b
        # 的右 barline 还是小节 b+1 的左 barline），覆盖的最后小节是 b。
        return close_boundary

    def finish(num: int, lo: int, hi: int, src: int) -> None:
        if hi < lo or not (0 <= lo < n and 0 <= hi < n):
            errors.append(_err(
                "MUSICXML_ENDING",
                f"第 {num} 遍替代结尾房子没有覆盖任何小节"
                "（start 与结束记号位于同一小节边界）。",
                src, labels[src],
            ))
        else:
            covers.append((num, lo, hi, src))

    for ev in events:
        if ev[0] != "ending":
            continue
        _, side, boundary, src, etype, number, _order = ev

        if etype == "start":
            if active is not None:
                a_num, a_start, a_src, a_side, _a_order = active
                if boundary == a_start and a_side != side:
                    # 同一小节边界两侧：新房子在另一侧开始，旧房子隐式
                    # 闭合（close_side=None），闭合边界即本边界。是否把
                    # 线前小节计入旧房子，按本层 backward 归属由 start_lo
                    # 依 LIFO 预配对判定。
                    lo = start_lo(a_start, a_side, a_num, boundary, None)
                    finish(a_num, lo, boundary, a_src)
                else:
                    errors.append(_err(
                        "MUSICXML_ENDING",
                        f"第 {a_num} 遍替代结尾的 start 缺少 stop 或 "
                        "discontinue 就开始下一个房子，存在未配对的房子记号。",
                        a_src, labels[a_src],
                    ))
                active = None
            active = (number, boundary, src, side, _order)
        else:
            # stop / discontinue
            if active is None:
                errors.append(_err(
                    "MUSICXML_ENDING",
                    f"第 {number} 遍替代结尾的 {etype} 没有对应的 start，"
                    "存在未配对的房子记号。",
                    src, labels[src],
                ))
                continue
            a_num, a_start, a_src, a_side, _a_order = active
            hi = stop_hi(boundary)
            lo = start_lo(a_start, a_side, a_num, boundary, side)
            if hi < lo:
                errors.append(_err(
                    "MUSICXML_ENDING",
                    f"第 {number} 遍替代结尾房子没有覆盖任何小节"
                    "（start 与结束记号位于同一小节边界）。",
                    src, labels[src],
                ))
                active = None
                continue
            if a_num != number:
                errors.append(_err(
                    "MUSICXML_ENDING",
                    f"第 {a_num} 遍替代结尾用第 {number} 遍的 {etype} 闭合，"
                    "房子编号不匹配。",
                    src, labels[src],
                ))
                active = None
                continue
            finish(a_num, lo, hi, a_src)
            active = None

    if active is not None:
        num, _, a_src, _, _ = active
        errors.append(_err(
            "MUSICXML_ENDING",
            f"第 {num} 遍替代结尾的 start 缺少 stop 或 discontinue，"
            "存在未配对的房子记号。",
            a_src, labels[a_src],
        ))
    return covers, errors


# ---------------------------------------------------------------------------
# Pass 2：反复配对（forward/backward，LIFO 栈）
# ---------------------------------------------------------------------------

def _parse_times(text: str | None, src: int, label: str,
                 ) -> tuple[int | None, dict[str, Any] | None]:
    """backward 的 times 属性：缺省 2，限 2~4。"""
    if text is None or not str(text).strip():
        return 2, None
    value = str(text).strip()
    if not value.isdigit() or not (2 <= int(value) <= 4):
        return None, _err(
            "MUSICXML_REPEAT_TIMES",
            f"反复次数必须是 2~4 的整数（缺省为 2），当前为 {value!r}。",
            src, label,
        )
    return int(value), None


def _match_repeats(events: list[tuple],
                   secondary_back: set[int], n: int, labels: list[str],
                   ) -> tuple[dict[int, int], dict[int, list[int]],
                              list[dict[str, Any]]]:
    """配对 forward/backward 并落到行。

    记号按小节阅读顺序处理：第 ``i`` 小节先处理其左 barline（边界
    ``i-1``，forward 起点在本行），再处理右 barline（边界 ``i``，
    backward 终点在本行）。官方 MusicXML 常见的“右 barline backward
    与下一小节左 barline forward 位于同一物理小节线”的写法因此天然
    配对：同一小节线处先闭合旧起点、再开新起点。

    返回：

    - ``left_map``：起点行 → times；
    - ``right_closes_map``：终点行 → 该行右边界闭合的各反复**起点行**
      （按处理先后，即内层→外层；同线双终点时为两个）。单行单终点时
      也是长度 1 的列表，由入口决定是否写到行的 ``right_closes``；
    - ``errors``。

    次级终点的层级判定（本侧 / 跨侧第 2+ 房子闭合、同线双 backward
    各认本侧）与 Pass 1 的预配对共用
    :func:`_classify_secondary_backwards`，两个 pass 的结论必然一致；
    这里只需对次级 backward 校验承载反复存在、times 一致：

    - backward 被识别为次级终点时不弹栈，但与主终点一样登记到终点行，
      并记下它闭合的反复起点（承载 forward 所在行）；
    - 同一物理边界两侧各有一个 backward（一侧内层第 2+ 房子次级终点、
      另一侧更外层主终点）时，两个终点落在**同一录入行**，
      ``right_closes_map`` 保留两个归属（内层在前），不再被单行布尔
      右标记合并成一个；
    - backward 的主终点一律落在它所在物理边界前的小节，不再按同线房子
      挪动：真正的同线闭合下该小节正是编号 1 房子覆盖的唯一小节（房子
      覆盖由 :func:`_match_endings` 按“闭合边界 == start 边界”判定）；
      若同线是**更外层**房子的右 barline start 而该房子在后面的边界才
      闭合，则该 backward 属于内层反复，终点保留在线前小节，外层房子
      从线后小节覆盖，避免把内层终点误挪到外层房子末节。
    """
    errors: list[dict[str, Any]] = []
    left_map: dict[int, int] = {}
    right_closes_map: dict[int, list[int]] = {}

    def add_close(row: int, owner_row: int) -> None:
        """登记终点行 ``row`` 上一个归属反复起点 ``owner_row`` 的终点。

        按调用顺序（右 barline 先于同边界左 barline）追加，因此同线双
        终点天然是内层在前、外层在后。
        """
        owners = right_closes_map.setdefault(row, [])
        if owner_row not in owners:
            owners.append(owner_row)

    def index_events(kind: str) -> dict[tuple[int, str], list[tuple]]:
        grouped: dict[tuple[int, str], list[tuple]] = {}
        for ev in events:
            if ev[0] == kind:
                grouped.setdefault((ev[2], ev[1]), []).append(ev)
        return grouped

    repeats = index_events("repeat")

    # 反复栈：元素 (start_row, src_index)；matched 记录已配对的
    # (start_row, times)，供次级终点确认存在承载反复。
    stack: list[tuple[int, int]] = []
    matched: list[tuple[int, int]] = []

    def fail(message: str, src: int) -> None:
        errors.append(_err("MUSICXML_REPEAT_UNMATCHED", message,
                           src, labels[src]))

    def open_forward(ev: tuple) -> None:
        _, _side, boundary, src, _d, _tt, _o = ev
        # forward 位于小节开始边界之后：起点行 = boundary + 1（首小节
        # 左 barline 的边界为 -1 → 第 0 行）；起点后至少要有一个小节。
        if not (-1 <= boundary < n - 1):
            fail("forward repeat 必须位于一个小节的开头，且该小节后至少"
                 "还有一个小节。", src)
            return
        start_row = boundary + 1
        if any(r == start_row for r, _ in stack):
            fail("同一小节开头出现重复的 forward repeat。", src)
            return
        stack.append((start_row, src))

    def close_backward(ev: tuple) -> None:
        _, side, boundary, src, _d, times_text, _o = ev
        times, times_error = _parse_times(times_text, src, labels[src])
        if times_error is not None:
            errors.append(times_error)
            times = 2

        # 次级终点：与 Pass 1 共用同一套层级分类（本侧 / 跨侧的第 2+
        # 房子闭合，同线双 backward 各认本侧）。承载反复可能仍在栈上，
        # 也可能其 {1 主终点已弹栈（次级终点在第 2+ 房子末尾，本就位于
        # 主终点之后）——从“栈 + 已配对”里取起点行最大（最内层）的一个，
        # 这样同线双 backward 时右 barline 的内层次级终点不会误认外层
        # forward，归属与随后左 barline 的外层主终点区分开。
        if _o in secondary_back:
            owner_row = None
            for open_row, _src in reversed(stack):
                if open_row <= boundary:
                    owner_row = open_row
                    break
            for open_row, _t in reversed(matched):
                if open_row <= boundary and (
                        owner_row is None or open_row > owner_row):
                    owner_row = open_row
            if owner_row is None:
                fail("替代结尾房子终点处的 backward repeat 没有对应的 forward "
                     "repeat（未配对的反复记号）。", src)
                return
            owner_times = left_map.get(owner_row)
            # 次级终点通常不写 times；显式写出时必须与承载反复一致
            if (times_text is not None and str(times_text).strip()
                    and owner_times is not None and owner_times != times):
                errors.append(_err(
                    "MUSICXML_REPEAT_TIMES",
                    f"替代结尾房子终点处的 backward times（{times}）与该反复"
                    f"起点的次数（{owner_times}）不一致。",
                    src, labels[src],
                ))
            # 次级终点同样登记归属；同边界左 barline 随后的外层主终点
            # 会追加到同一行，形成 [内层起点, 外层起点]。
            add_close(boundary, owner_row)
            return

        # 主终点落在 backward 所在物理边界前的小节 boundary。真正的同线
        # 闭合下它就是 {1 房子覆盖的唯一小节；与更外层房子 start 共线
        # （外层房子之后才闭合）时它仍是内层反复自己的终点，不挪动。
        right_row = boundary
        if not (0 <= right_row < n):
            fail("backward repeat 位于全曲第一个小节之前，没有可反复的内容。",
                 src)
            return
        if not stack:
            fail("backward repeat 没有对应的 forward repeat（未配对的反复记号）。",
                 src)
            return
        open_row, _open_src = stack.pop()
        if right_row < open_row:
            fail("backward repeat 位于对应 forward repeat 之前，未正确嵌套配对。",
                 src)
            return
        if open_row in left_map and left_map[open_row] != times:
            errors.append(_err(
                "MUSICXML_REPEAT_TIMES",
                "同一反复起点的多个终点给出的 times 不一致。",
                src, labels[src],
            ))
        else:
            left_map[open_row] = times
        add_close(right_row, open_row)
        matched.append((open_row, times))

    # 逐小节、按文档顺序：先左 barline（边界 i-1）上的记号，再右 barline
    # （边界 i）上的记号。forward / backward 按各自语义处理，出现在
    # “错误”一侧的记号同样过配对栈校验，不会被静默忽略。
    for i in range(n):
        for ev in repeats.get((i - 1, "left"), []):
            if ev[4] == "forward":
                open_forward(ev)
            else:
                close_backward(ev)
        for ev in repeats.get((i, "right"), []):
            if ev[4] == "forward":
                open_forward(ev)
            else:
                close_backward(ev)

    for open_row, open_src in stack:
        fail("forward repeat 没有对应的 backward repeat（未配对的反复记号）。",
             open_src)
    return left_map, right_closes_map, errors


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def import_musicxml(raw_text: Any
                    ) -> tuple[dict[str, Any] | None,
                               list[dict[str, Any]] | None]:
    """把 MusicXML 文本转换成录入行并跑现有校验与展开。

    成功返回 ``(result, None)``，``result`` 含 ``measures``（录入行数组）
    与 ``expansion``（``/api/expand`` 的完整展开结果）；结构 / 内容不支持
    或校验展开失败时返回 ``(None, errors)``，错误按源小节定位。
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None, [_err("MUSICXML_EMPTY", "导入内容为空。")]

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

    numbers = [m.get("number") for m in measures]
    labels = [_measure_label(numbers[i], i) for i in range(n)]

    # --- 逐小节：拍号与起始处 BPM --------------------------------------
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    current_beats: int | None = None
    for i, measure in enumerate(measures):
        label = labels[i]
        beats, time_errors = _parse_time(measure, label, i)
        errors.extend(time_errors)
        if beats is not None:
            current_beats = beats
        if i == 0 and beats is None and not time_errors:
            errors.append(_err(
                "MUSICXML_TIME",
                "第 1 小节必须显式给出分母为 4 的拍号。",
                0, label,
            ))

        tempo, tempo_errors = _parse_tempo(measure, label, i)
        errors.extend(tempo_errors)

        rows.append({
            "number": label,
            "beats": current_beats if current_beats is not None else 4,
            "tempo": tempo,
            "left_repeat": False,
            "right_repeat": False,
            "right_closes": None,
            "times": None,
            "volta": None,
        })

    # --- 记号事件配对 ---------------------------------------------------
    events, event_errors = _collect_events(measures, labels)
    errors.extend(event_errors)
    # 次级 backward 的层级分类由两个 pass 共用，确保房子覆盖（预配对）
    # 与反复落行（正式配对）对跨侧闭合 / 同线双 backward 结论一致。
    secondary_back = _classify_secondary_backwards(events)
    covers, ending_errors = _match_endings(events, n, labels,
                                           secondary_back)
    errors.extend(ending_errors)
    left_map, right_closes_map, repeat_errors = _match_repeats(
        events, secondary_back, n, labels)
    errors.extend(repeat_errors)

    # 房子覆盖的小节 → volta
    for number, lo, hi, _src in covers:
        for pos in range(lo, hi + 1):
            if rows[pos]["volta"] is not None and rows[pos]["volta"] != number:
                # 理论上 _match_endings 已排除交叉；保留一道防线
                errors.append(_err(
                    "MUSICXML_ENDING",
                    "替代结尾房子覆盖范围重叠，暂不支持。",
                    pos, labels[pos],
                ))
            rows[pos]["volta"] = number

    # 反复起点 / 终点落行。同一物理边界两侧各有一个 backward（内层第 2+
    # 房子次级终点 + 更外层主终点）时，二者落在同一行：right_repeat 置
    # 真，并用 right_closes 保留两个终点各自的反复起点（内层在前），
    # 交给展开引擎在该边界先完成内层房子、再闭合外层反复。单终点行保持
    # 原有形态（right_closes 留空），手工录入 / 旧谱面不受影响。
    for pos, times in left_map.items():
        rows[pos]["left_repeat"] = True
        rows[pos]["times"] = times
    for pos, owners in right_closes_map.items():
        rows[pos]["right_repeat"] = True
        if len(owners) > 1:
            rows[pos]["right_closes"] = owners

    if errors:
        errors.sort(key=lambda e: (e["index"] is None, e["index"] or 0,
                                   e["code"]))
        return None, errors

    # --- 转换完成：必须通过现有校验与展开 -------------------------------
    result, expand_errors = expand(rows)
    if expand_errors:
        return None, expand_errors
    assert result is not None
    return {"measures": rows, "expansion": result}, None
