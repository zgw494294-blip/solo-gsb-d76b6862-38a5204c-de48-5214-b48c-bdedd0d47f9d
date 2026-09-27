"""MusicXML 结构导出（``score-partwise``，单 part）。

把当前录入行（与 ``/api/expand`` 同构的小节数组）导出为**只含结构**的
MusicXML：每个小节写出编号、分母为 4 的拍号、显式 BPM（仅标记行）与
全小节休止符；反复、连续替代结尾 / 终点后的房子、嵌套反复与同线双终点
全部编码为 barline 记号。

导出前对录入行**复用完整校验与展开**（:func:`expander.expand`）：非法
谱面按原始行返回现有错误，不产生文件。文件随后用现有的
:func:`musicxml_import.import_musicxml` 重新导入做**往返自检**，重新导入
出的录入行与展开结果（拍数、显式 BPM、反复边界、房子遍次、双终点归属、
访问的来源编号 / 遍次路径 / 生效 BPM）必须与原谱一致，否则不产生文件。

编码约定（均可被现有导入规则识别）
==================================

- 拍号：每小节都显式写 ``<time><beats>拍数</beats><beat-type>4</beat-type>
  </time>``（``divisions=1``，全小节休止符 ``<rest/>`` 时长等于拍数）。
- BPM：仅在**原始行显式标记** BPM 的小节起始处写 ``<sound tempo=".."/>``；
  留空行不写，由导入沿用最近标记——显式标记位置与各次访问生效 BPM 均与
  原谱一致。
- forward：写在起点小节的**左 barline**（首小节即边界 -1）。
- backward：每个反复的**主**终点写 ``times``（2~4），次级终点不写；
  普通行写在该小节**右 barline**。
- 替代结尾：同一所属反复、同一遍次的连续小节合并成一个房子括号：
  ``<ending type="start">`` 在首小节**左 barline**、``<ending type="stop">``
  在末小节**右 barline**（右线 stop 与 backward 同 barline 时 stop 在前）。
  始终用左 barline 起点，因此不触发导入端“编号 1 房子右 barline 起点”的
  同线特例，覆盖范围确定；终点后的尾部房子同样编码（最末房子可无
  backward，与打谱软件常规写法一致）。
- **同线双终点**（一行 ``right_closes=[内层起点, 外层起点]``）：两个
  backward 分置同一道物理小节线两侧——内层（主或次级）在该小节**右
  barline**，外层主终点在下一小节**左 barline**，保留内外层归属；重新
  导入时凭“同线两侧各一个 backward + 内层房子闭合记号”的现有规则还原
  ``right_closes``。下一小节同时是**新反复起点**时不冲突：该小节开头写
  两道左 barline——第一道只放外层主 backward（先闭合旧反复），第二道放
  新 forward（后开启新反复）；该小节的房子 start 仍与 forward 同组。
  这一“先闭后开”顺序是必要的：反过来会把外层 backward 错配给新起点。

不可编码情形
============

现有展开器接受的结构中仅一种极端写法无法落到单 part MusicXML：同线双
终点行恰好是**最后一个小节**——外层主 backward 需要写到下一小节左
barline，但其后已无小节。这类谱面返回 ``MUSICXML_EXPORT_BARLINE`` 等
错误，不产生下载；凡能导出的文件必然通过往返自检。
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import quoteattr

from .expander import (
    _assign_volta_owners,
    _match_brackets,
    expand,
    normalize_rows,
)
from .musicxml_import import import_musicxml

PART_ID = "P1"


def _err(code: str, message: str, index: int | None = None,
         measure: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "index": index,
        "measure": measure,
    }


def _esc(text: Any) -> str:
    """元素文本转义。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# barline 布置
# ---------------------------------------------------------------------------

def _plan_barlines(rows: list[dict[str, Any]]
                   ) -> tuple[dict[int, list[list[str]]],
                              dict[int, list[str]],
                              list[dict[str, Any]]]:
    """根据配对结果安排各小节左 / 右 barline 上的记号。

    返回 ``(left_groups, right_tokens, errors)``：

    - ``left_groups``：键为小节行号，值为该小节开头要写出的**多道左
      barline**（每道是一个 token 列表，按文档顺序逐道写出）。同一小节
      开头至多两道：同线双终点的外层主 backward 独占第一道（先闭合旧
      反复），房子 start / 新 forward 同处第二道（后开启新反复）；
    - ``right_tokens``：键为小节行号，值为该小节右 barline 的 token
      列表（一道 barline，按文档顺序）。
    """
    n = len(rows)
    pairs, errors = _match_brackets(rows)
    if errors:
        return {}, {}, errors

    owner = _assign_volta_owners(rows, pairs)

    # 每道右 barline（按行）需要放置的 backward：
    # (pair, is_main)；同线双终点时内层在右、外层移到下一小节左 barline。
    right_backs: dict[int, list[tuple[dict[str, Any], bool]]] = {}
    left_backs: dict[int, list[tuple[dict[str, Any], bool]]] = {}

    # closes_at：终点行 → 该边界闭合的各 pair（内层在前、外层在后），
    # 与展开器 closes_at 的排序口径一致。
    closes_at: dict[int, list[dict[str, Any]]] = {}
    for p in pairs:
        for c in p["closes"]:
            closes_at.setdefault(c, []).append(p)
    for cs in closes_at.values():
        cs.sort(key=lambda p: -p["depth"])

    for i, cs in closes_at.items():
        declared = rows[i]["right_closes"]
        if declared is not None and len(declared) == 2:
            if i + 1 >= n:
                # 外层主 backward 必须落在下一小节左 barline，但已无下一小节。
                errors.append(_err(
                    "MUSICXML_EXPORT_BARLINE",
                    "同线双终点位于最后一个小节：外层主终点需要写到下一小节"
                    "左 barline，但其后已无小节，无法导出为 MusicXML"
                    "（表格与演奏带不受影响）。",
                    i, rows[i]["number"],
                ))
                continue
            # 同线双终点：右 barline 放内层（主或次级），外层主终点移到
            # 下一小节左 barline（同一道物理小节线两侧）。
            inner, outer = cs
            right_backs.setdefault(i, []).append(
                (inner, inner["close"] == i))
            left_backs.setdefault(i + 1, []).append(
                (outer, outer["close"] == i))
        else:
            for p in cs:
                right_backs.setdefault(i, []).append(
                    (p, p["close"] == i))

    # 替代结尾：把连续的同 (所属反复, 遍次) 小节合并为一个房子括号。
    # start 在首小节左 barline，stop 在末小节右 barline。
    segments: list[tuple[int, int, int]] = []  # (volta, lo, hi)
    seg_start = 0
    for i in range(1, n + 1):
        same = i < n and owner[i] == owner[seg_start] \
            and rows[i]["volta"] == rows[seg_start]["volta"]
        if not same:
            if owner[seg_start] is not None \
                    and rows[seg_start]["volta"] is not None:
                segments.append(
                    (rows[seg_start]["volta"], seg_start, i - 1))
            seg_start = i

    left_groups: dict[int, list[list[str]]] = {}
    right_tokens: dict[int, list[str]] = {}

    def ending_xml(number: int, etype: str) -> str:
        return f'<ending number="{number}" type="{etype}"/>'

    def backward_xml(pair: dict[str, Any], is_main: bool) -> str:
        # times 只写在主终点（与该反复起点行的次数一致）；次级终点不写。
        times = f' times="{pair["times"]}"' if is_main else ""
        return f'<repeat direction="backward"{times}/>'

    # 左 barline：同一物理线上“先闭合旧反复、后开启新反复”。同线双终点
    # 移过来的外层主 backward 独占**第一道**左 barline；房子 start 与
    # forward 同处**第二道**（一道 barline 至多一个 repeat，故二者拆成
    # 两道；该小节若不携带外层主 backward，则仍只写一道）。这个顺序不可
    # 调换——先写 forward 会让外层 backward 错配给刚开启的新起点。
    for i, backs in left_backs.items():
        left_groups.setdefault(i, []).append(
            [backward_xml(pair, is_main) for pair, is_main in backs])

    open_tokens: dict[int, list[str]] = {}
    for number, lo, _hi in segments:
        open_tokens.setdefault(lo, []).append(ending_xml(number, "start"))
    for p in pairs:
        open_tokens.setdefault(p["open"], []).append(
            '<repeat direction="forward"/>')
    for i, tokens in open_tokens.items():
        # 第一道已被外层主 backward 占用时，start / forward 追加为第二道；
        # 否则该小节只写这一道。
        left_groups.setdefault(i, []).append(tokens)

    # 右 barline：房子 stop 在前、backward 在后（同一道 barline）。
    for number, _lo, hi in segments:
        right_tokens.setdefault(hi, []).append(ending_xml(number, "stop"))
    for i, backs in right_backs.items():
        for pair, is_main in backs:
            right_tokens.setdefault(i, []).append(
                backward_xml(pair, is_main))

    return left_groups, right_tokens, errors


def _barline_xml(location: str, tokens: list[str]) -> list[str]:
    lines = [f'    <barline location="{location}">']
    lines.extend(f"      {t}" for t in tokens)
    lines.append("    </barline>")
    return lines


# ---------------------------------------------------------------------------
# 文件生成
# ---------------------------------------------------------------------------

def _build_musicxml(rows: list[dict[str, Any]],
                   left_groups: dict[int, list[list[str]]],
                   right_tokens: dict[int, list[str]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<score-partwise version="4.0">',
        f"  <part-list><score-part id={quoteattr(PART_ID)}>"
        f"<part-name>Part</part-name></score-part></part-list>",
        f'  <part id={quoteattr(PART_ID)}>',
    ]
    for i, row in enumerate(rows):
        lines.append(f'  <measure number={quoteattr(row["number"])}>')
        # 每小节显式写出分母为 4 的拍号（divisions=1：四分音符为一时值单位）
        lines.append(
            "    <attributes><divisions>1</divisions><time>"
            f'<beats>{row["beats"]}</beats><beat-type>4</beat-type>'
            "</time></attributes>")
        # 显式 BPM：只在原始行标记了速度的小节起始处写出；留空行沿用。
        if row["tempo"] is not None:
            lines.append(f'    <sound tempo="{row["tempo"]}"/>')
        # 开头可有多道左 barline（外层主 backward 先闭合、新 forward
        # 后开启），按组逐道写出，每道至多一个 repeat 记号。
        if i in left_groups:
            for group in left_groups[i]:
                lines.extend(_barline_xml("left", group))
        # 全小节休止符：时长等于本小节拍数（单位为四分音符）。
        lines.append(
            '    <note><rest/><duration>'
            f'{row["beats"]}</duration><type>whole</type></note>')
        if i in right_tokens:
            lines.extend(_barline_xml("right", right_tokens[i]))
        lines.append("  </measure>")
    lines.extend(["  </part>", "</score-partwise>", ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

# 往返自检时逐字段比较的录入行字段（顺序无关，值必须一致）。
_ROW_FIELDS = (
    "number", "beats", "tempo", "left_repeat", "right_repeat",
    "right_closes", "times", "volta",
)


def export_musicxml(raw: Any
                    ) -> tuple[dict[str, Any] | None,
                               list[dict[str, Any]]]:
    """把录入行导出为单 part ``score-partwise`` 文本。

    成功返回 ``({"musicxml": ...}, [])``；谱面非法或结构无法编码时返回
    ``(None, errors)``（错误按原始行定位，不产生下载内容）。
    """
    # 1) 复用完整校验与展开（含 512 上限）。
    result, errors = expand(raw)
    if errors:
        return None, errors
    assert result is not None

    # 归一化后的录入行（编号 / 拍数 / BPM / 反复 / 房子都已规整）。
    rows, norm_errors = normalize_rows(raw)
    assert rows is not None and not norm_errors

    # 2) 安排 barline 记号（配对错误理论上已在 expand 阶段返回）。
    left_groups, right_tokens, plan_errors = _plan_barlines(rows)
    if plan_errors:
        return None, plan_errors

    # 3) 生成 MusicXML。
    xml_text = _build_musicxml(rows, left_groups, right_tokens)

    # 4) 往返自检：用现有入口重新导入，行与展开结果必须完全一致。
    reimp, reimp_errors = import_musicxml(xml_text)
    if reimp_errors:
        return None, [_err(
            "MUSICXML_EXPORT_BARLINE",
            "该谱面的反复 / 房子结构无法编码为可重新导入的 MusicXML"
            "（往返自检未通过）；表格与演奏带不受影响。",
        )]
    assert reimp is not None

    reimp_rows, reimp_norm_errors = normalize_rows(reimp["measures"])
    if reimp_norm_errors or reimp_rows is None:  # 防御：导出文件应始终可导入
        return None, [_err(
            "MUSICXML_EXPORT_ROUNDTRIP",
            "导出文件重新导入时的录入行校验失败（往返自检未通过）。",
        )]

    for i, (a, b) in enumerate(zip(rows, reimp_rows)):
        for field in _ROW_FIELDS:
            if a[field] != b[field]:
                return None, [_err(
                    "MUSICXML_EXPORT_ROUNDTRIP",
                    f"第 {i + 1} 行（{a['number']}）的 {field} 在重新导入后"
                    "与原谱不一致（往返自检未通过）。",
                    i, a["number"],
                )]

    if reimp["expansion"] != result:
        return None, [_err(
            "MUSICXML_EXPORT_ROUNDTRIP",
            "导出文件重新导入后的展开结果（来源编号、遍次路径或生效 BPM）"
            "与原谱不一致（往返自检未通过）。",
        )]

    return {"musicxml": xml_text}, []
