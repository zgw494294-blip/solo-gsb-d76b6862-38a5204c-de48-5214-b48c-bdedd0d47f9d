"""MusicXML 结构导入测试。"""

import pytest

from app.musicxml_import import import_musicxml

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Music</part-name></score-part>
  </part-list>
  <part id="P1">
{measures}
  </part>
</score-partwise>
"""

DEFAULT_ATTRS = (
    '<attributes><divisions>1</divisions>'
    '<time><beats>4</beats><beat-type>4</beat-type></time></attributes>'
)
NOTE = (
    '<note><pitch><step>C</step><octave>4</octave></pitch>'
    '<duration>4</duration><type>whole</type></note>'
)


def measure(num, inner="", attrs=DEFAULT_ATTRS):
    return f'<measure number="{num}">{attrs}{inner}</measure>'


def build(measures):
    return HEADER.format(measures="".join(measures))


def import_ok(xml):
    result, errors = import_musicxml(xml)
    assert not errors, errors
    assert result is not None
    return result


def import_fail(xml):
    result, errors = import_musicxml(xml)
    assert result is None
    assert errors
    return errors


def seq(result):
    return [v["source_measure"] for v in result["expansion"]["visits"]]


def forward():
    return '<barline location="left"><repeat direction="forward"/></barline>'


def backward(times=None):
    t = f' times="{times}"' if times is not None else ""
    return ('<barline location="right"><repeat direction="backward"'
            f'{t}/></barline>')


# --------------------------------------------------------------------------
# 基本转换
# --------------------------------------------------------------------------

def test_plain_score():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert [r["number"] for r in rows] == ["1", "2"]
    assert [r["beats"] for r in rows] == [4, 4]  # 拍号后续沿用
    assert rows[0]["tempo"] == 90
    assert rows[1]["tempo"] is None  # 后续留空沿用
    assert seq(result) == ["1", "2"]
    assert result["expansion"]["tempos"][1]["tempo"] == 90


def test_metronome_direction_tempo():
    direction = (
        '<direction><direction-type><metronome>'
        '<beat-unit>quarter</beat-unit><per-minute>72</per-minute>'
        '</metronome></direction-type></direction>'
    )
    xml = build([measure("1", direction + NOTE), measure("2", NOTE, "")])
    result = import_ok(xml)
    assert result["measures"][0]["tempo"] == 72


def test_simple_repeat_with_times():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", backward(3) + NOTE),
        measure("4", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[1]["left_repeat"] is True and rows[1]["times"] == 3
    assert rows[2]["right_repeat"] is True
    assert seq(result) == ["1", "2", "3", "2", "3", "2", "3", "4"]


def test_backward_times_defaults_to_two():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", backward() + NOTE),
    ])
    result = import_ok(xml)
    assert result["measures"][1]["times"] == 2
    assert seq(result) == ["1", "2", "3", "2", "3"]


def test_nested_repeats():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4", backward() + NOTE),
        measure("5", backward() + NOTE),
    ])
    result = import_ok(xml)
    assert seq(result) == ["1", "2", "3", "4", "3", "4", "5",
                           "2", "3", "4", "3", "4", "5"]


# --------------------------------------------------------------------------
# 替代结尾
# --------------------------------------------------------------------------

def test_volta_musescore_style():
    # 左 barline start、右 barline stop（MuseScore 导出风格）
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE, ""),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right"><ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>'),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right"><ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>'),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[3]["volta"] == 1
    assert rows[4]["volta"] == 2
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6"]


def test_volta_multi_measure_house():
    # 房子跨多个小节：{1 房子 = m3,m4；{2 房子 = m5
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE, ""),
        measure("4",
                NOTE
                + '<barline location="right"><ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>'),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right"><ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>'),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[2]["volta"] == 1 and rows[3]["volta"] == 1
    assert rows[4]["volta"] == 2
    assert seq(result) == ["1", "2", "3", "4", "2", "5", "6"]


# --------------------------------------------------------------------------
# 右 barline 上的 ending start 边界（回归）
# --------------------------------------------------------------------------

def right_start(num):
    """第 num 小节右 barline 上的编号 1 房子 start（线后才是房子）。"""
    return (f'<barline location="right"><ending number="{num}" '
            'type="start"/></barline>')


def right_stop_back(num, times=None):
    t = f' times="{times}"' if times is not None else ""
    return ('<barline location="right">'
            f'<ending number="{num}" type="stop"/>'
            f'<repeat direction="backward"{t}/></barline>')


def left_start_right_stop(num):
    return (f'<barline location="left"><ending number="{num}" '
            'type="start"/></barline>'
            f'<barline location="right"><ending number="{num}" type="stop"/>'
            '<repeat direction="backward"/></barline>')


def test_volta_right_start_common_measure_played_twice():
    # 回归：第一房子 start 在公共段末节（m3）的右 barline，stop 与
    # backward 在下一节（m4）右侧，后接第二房子。右 barline 上的 start
    # 从下一节开始覆盖：m3 仍是公共小节，两遍都必须演奏。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE + right_start(1), ""),
        measure("4", NOTE + right_stop_back(1), ""),
        measure("5", left_start_right_stop(2) + NOTE, ""),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    # 预览 / 录入行：公共末节不得被标成第一房子
    assert rows[2]["volta"] is None
    assert rows[3]["volta"] == 1
    assert rows[4]["volta"] == 2
    assert rows[2]["right_repeat"] is False
    assert rows[3]["right_repeat"] is True
    # 展开演奏带：第 2 遍不得漏演 m3
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6"]


def test_volta_right_start_multi_measure_house():
    # 右 barline start + 跨多小节第一房子：{1 = m4,m5；公共 m3 两遍都演
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE + right_start(1), ""),
        measure("4", NOTE, ""),
        measure("5", NOTE + right_stop_back(1), ""),
        measure("6", left_start_right_stop(2) + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert [r["volta"] for r in rows] == [None, None, None, 1, 1, 2, None]
    assert seq(result) == ["1", "2", "3", "4", "5", "2", "3", "6", "7"]


def test_volta_sameline_start_and_backward_still_covers_measure():
    # 同线规范写法：编号 1 房子 start 与 backward 同在公共小节（m4）
    # 右 barline —— 该小节即第一房子，主终点落在它上面。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE, ""),
        measure("4",
                NOTE
                + '<barline location="right"><ending number="1" type="start"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5", left_start_right_stop(2) + NOTE, ""),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[3]["volta"] == 1 and rows[3]["right_repeat"] is True
    assert rows[4]["volta"] == 2
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6"]


def test_volta_sameline_implicit_close():
    # 同线写法且省略 {1 的显式 stop：{2 在下一小节左 barline 开始即隐式
    # 闭合 {1（m4 仍是 {1 房子，m3 仍是公共小节）。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE, ""),
        measure("4",
                NOTE
                + '<barline location="right"><ending number="1" type="start"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right"><ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[2]["volta"] is None
    assert rows[3]["volta"] == 1
    assert rows[4]["volta"] == 2
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6"]


def left_stop_back_start(num, times=None):
    """下一小节左 barline：旧房子 stop + backward + 新房子 start（同一道线）。"""
    t = f' times="{times}"' if times is not None else ""
    return ('<barline location="left">'
            f'<ending number="{num}" type="stop"/>'
            f'<repeat direction="backward"{t}/>'
            f'<ending number="{num + 1}" type="start"/></barline>')


def test_volta_sameline_split_sides_stop_on_left_barline():
    # 同一道小节线两侧配对：{1 start 在 m4 右 barline，stop 与 backward
    # 写在 m5 左 barline（同一物理线），{2 从该线开始 —— 线前小节 m4 即
    # 第一房子，主反复终点落在 m4；不得再判空报 MUSICXML_ENDING。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE, ""),
        measure("4", NOTE + right_start(1), ""),
        measure("5",
                left_stop_back_start(1) + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    # 预览 / 确认后的录入行：线前小节归第一房子并带主反复终点
    assert rows[2]["volta"] is None
    assert rows[3]["volta"] == 1 and rows[3]["right_repeat"] is True
    assert rows[4]["volta"] == 2 and rows[4]["right_repeat"] is False
    assert rows[1]["left_repeat"] is True and rows[1]["times"] == 2
    # 展开顺序与预览一致
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6"]


def test_volta_sameline_split_sides_discontinue():
    # 与上同构，但 {1 用 discontinue 闭合（写在 m5 左 barline）。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE, ""),
        measure("4", NOTE + right_start(1), ""),
        measure("5",
                '<barline location="left">'
                '<ending number="1" type="discontinue"/>'
                '<repeat direction="backward"/>'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[3]["volta"] == 1 and rows[3]["right_repeat"] is True
    assert rows[4]["volta"] == 2
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6"]


def test_volta_sameline_split_sides_implicit_close():
    # 跨侧同线且省略 {1 的显式 stop：{2 在同一物理线另一侧（m5 左
    # barline）开始即隐式闭合 {1，m4 仍是 {1 房子兼主反复终点。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE, ""),
        measure("4", NOTE + right_start(1), ""),
        measure("5",
                '<barline location="left">'
                '<repeat direction="backward"/>'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[2]["volta"] is None
    assert rows[3]["volta"] == 1 and rows[3]["right_repeat"] is True
    assert rows[4]["volta"] == 2
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6"]


def test_volta_right_start_left_barline_closers_multi_measure():
    # 分离写法的闭合记号同样按物理线识别：{1 start 在 m3 右 barline，
    # stop 与 backward 写在房子末节 m5 之后那道线的左侧（m6 左 barline），
    # 房子仍从线后 m4 起覆盖、跨 m4..m5；{2 从 m6 开始。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE + right_start(1), ""),
        measure("4", NOTE, ""),
        measure("5", NOTE, ""),
        measure("6",
                left_stop_back_start(1) + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert [r["volta"] for r in rows] == [None, None, None, 1, 1, 2, None]
    assert rows[4]["right_repeat"] is True   # 主终点在房子末节 m5
    assert rows[5]["right_repeat"] is False
    assert seq(result) == ["1", "2", "3", "4", "5", "2", "3", "6", "7"]


def test_volta_sameline_split_sides_nested_repeat():
    # 嵌套反复兼容：内层（m3..m5）与外层（m2..m7）的房子都用跨侧同线
    # 写法 —— {1 start 在房子所在小节右 barline，stop+backward 与 {2
    # start 在下一小节左 barline。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4", NOTE + right_start(1), ""),
        measure("5",
                left_stop_back_start(1) + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("6", NOTE + right_start(1), ""),
        measure("7",
                left_stop_back_start(1) + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("8", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert [r["volta"] for r in rows] == [None, None, None, 1, 2, 1, 2, None]
    assert rows[3]["right_repeat"] is True   # 内层主终点在 m4
    assert rows[5]["right_repeat"] is True   # 外层主终点在 m6
    assert rows[1]["times"] == 2 and rows[2]["times"] == 2
    assert seq(result) == [
        "1", "2", "3", "4", "3", "5", "6",
        "2", "3", "4", "3", "5", "7", "8",
    ]


def test_volta_split_sides_without_backward_still_rejected():
    # 跨侧但同一边界上没有 backward：不构成同线规范写法，房子不覆盖
    # 任何小节，仍按源小节报 MUSICXML_ENDING，不产生结果。
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE + right_start(1), ""),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="stop"/></barline>' + NOTE, ""),
    ])
    errors = import_fail(xml)
    err = next(e for e in errors if e["code"] == "MUSICXML_ENDING")
    assert err["index"] == 3 and err["measure"] == "4"


def test_volta_right_start_inside_nested_repeat():
    # 嵌套反复兼容：内层反复（右 barline 起点的房子）完整位于外层公共段；
    # 外层房子（同样右 barline 起点非同线）在内层闭合之后。
    # m2 外 forward；m3 内 forward；m4 内公共末节（右 {1 start）；
    # m5 内 {1（右 stop+back）；m6 内 {2；m7 外公共末节（右 {1 start）；
    # m8 外 {1（右 stop+back）；m9 外 {2；m10 尾。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4", NOTE + right_start(1), ""),
        measure("5", NOTE + right_stop_back(1), ""),
        measure("6", left_start_right_stop(2) + NOTE, ""),
        measure("7", NOTE + right_start(1), ""),
        measure("8", NOTE + right_stop_back(1), ""),
        measure("9", left_start_right_stop(2) + NOTE, ""),
        measure("10", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    # 两个公共末节（m4、m7）都不得被误标成房子
    assert [r["volta"] for r in rows] == [
        None, None, None, None, 1, 2, None, 1, 2, None]
    assert seq(result) == [
        "1", "2", "3", "4", "5", "3", "4", "6", "7", "8",
        "2", "3", "4", "5", "3", "4", "6", "7", "9", "10",
    ]


def test_inner_backward_collinear_with_outer_ending_same_side():
    # 回归（误判修复）：内层 backward 与外层第一房子的右 barline start
    # 同在 m4 右线，但外层房子到 m5 右线 stop+外层 backward 才闭合。
    # 内层终点必须留在 m4，外层 {1 从线后 m5 覆盖、外层终点落在 m5；
    # 第二房子 m6 紧随其后。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                NOTE
                + '<barline location="right">'
                  '<repeat direction="backward"/>'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("5", NOTE + right_stop_back(1), ""),
        measure("6", left_start_right_stop(2) + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    # 预览 / 确认后的录入行
    assert [r["volta"] for r in rows] == [
        None, None, None, None, 1, 2, None]
    assert rows[3]["right_repeat"] is True    # 内层终点留在线前小节 m4
    assert rows[3]["volta"] is None           # m4 不是外层房子
    assert rows[4]["right_repeat"] is True    # 外层终点落在房子末节 m5
    assert rows[2]["left_repeat"] is True     # 内层起点
    assert rows[1]["left_repeat"] is True     # 外层起点
    # 展开顺序与预览一致
    assert seq(result) == [
        "1", "2", "3", "4", "3", "4", "5",
        "2", "3", "4", "3", "4", "6", "7",
    ]


def test_inner_backward_collinear_with_outer_ending_cross_sides():
    # 同上但外层房子在 m5 左 barline 之后开始（右 start 在线前），跨侧
    # 显式闭合在 m5/m6 线：m6 左 barline 上 {1 stop + 外层 backward +
    # {2 start；内层 backward 仍在 m4 右线，终点不挪动。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                NOTE
                + '<barline location="right">'
                  '<repeat direction="backward"/>'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("5", NOTE, ""),
        measure("6",
                left_stop_back_start(1) + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert [r["volta"] for r in rows] == [
        None, None, None, None, 1, 2, None]
    assert rows[3]["right_repeat"] is True    # 内层终点留在 m4
    assert rows[4]["right_repeat"] is True    # 外层终点在房子末节 m5
    assert seq(result) == [
        "1", "2", "3", "4", "3", "4", "5",
        "2", "3", "4", "3", "4", "6", "7",
    ]


def test_inner_backward_collinear_outer_ending_mixed_sides():
    # 外层房子右 start 在 m4 右线，覆盖 m5，stop+backward 写在 m5 右
    # barline（与 {2 左 start 跨侧）；内层 backward 在 m4 右线。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                NOTE
                + '<barline location="right">'
                  '<repeat direction="backward"/>'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("5", NOTE + right_stop_back(1), ""),
        measure("6",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert [r["volta"] for r in rows] == [
        None, None, None, None, 1, 2, None]
    assert rows[3]["right_repeat"] is True
    assert rows[4]["right_repeat"] is True
    assert seq(result) == [
        "1", "2", "3", "4", "3", "4", "5",
        "2", "3", "4", "3", "4", "6", "7",
    ]


def test_inner_backward_collinear_outer_ending_preview_rows_match_expansion():
    # 预览行与确认后重新展开必须一致：对转换出的录入行再跑一次 expand，
    # 得到的演奏带与导入结果内嵌的 expansion 完全相同。
    from app.expander import expand
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                NOTE
                + '<barline location="right">'
                  '<repeat direction="backward"/>'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("5", NOTE + right_stop_back(1), ""),
        measure("6", left_start_right_stop(2) + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    reexpanded, errors = expand(result["measures"])
    assert not errors
    embedded = [v["source_measure"]
                for v in result["expansion"]["visits"]]
    confirmed = [v["source_measure"] for v in reexpanded["visits"]]
    assert embedded == confirmed


def test_inner_backward_collinear_missing_outer_backward_rejected():
    # 无效配对：外层房子 stop 处缺少外层 backward，外层 forward 未闭合，
    # 仍按外层 forward 的源小节（m2）报错且不产生结果（不改表格）。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                NOTE
                + '<barline location="right">'
                  '<repeat direction="backward"/>'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("5",
                NOTE
                + '<barline location="right">'
                  '<ending number="1" type="stop"/></barline>', ""),
        measure("6", NOTE, ""),
    ])
    errors = import_fail(xml)
    err = next(e for e in errors
               if e["code"] == "MUSICXML_REPEAT_UNMATCHED")
    assert err["index"] == 1 and err["measure"] == "2"


def test_inner_backward_collinear_without_inner_forward_rejected():
    # 无效配对：m4 右线的 backward 没有自己的内层 forward（只有外层
    # forward m2）。按层级它必须留在 m4 闭合内层，因此报“没有对应的
    # forward”，定位在源小节 m5（0 基下标 3）。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("4",
                NOTE
                + '<barline location="right">'
                  '<repeat direction="backward"/>'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("5", NOTE + right_stop_back(1), ""),
        measure("6", NOTE, ""),
    ])
    errors = import_fail(xml)
    err = next(e for e in errors
               if e["code"] == "MUSICXML_REPEAT_UNMATCHED")
    assert err["index"] == 3


def test_cross_side_secondary_end_left_barline():
    # 次级终点跨侧：{2 房子覆盖 m5，{2 stop 与 backward 都写在 m6 左
    # barline（该线右侧没有自己的 backward）——识别为次级终点，不弹栈。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE + right_start(1), ""),
        measure("4", NOTE + right_stop_back(1), ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE, ""),
        measure("6",
                '<barline location="left">'
                '<ending number="2" type="stop"/>'
                '<repeat direction="backward"/></barline>' + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[3]["right_repeat"] is True    # 主终点 m4
    assert rows[4]["right_repeat"] is True    # 次级终点落在 m5（线前小节）
    assert rows[5]["right_repeat"] is False
    assert seq(result) == ["1", "2", "3", "4", "2", "3", "5", "6", "7"]


def nested_cross_side_xml(drop_outer_backward=False):
    """嵌套反复 + 跨侧次级终点 + 外层同线隐式闭合。

    外层 m2..m7；内层 m3..m5。内层 {2 的 stop 写在 m5 右 barline，
    次级 backward 跨侧写在 m6 左 barline（与 stop 同一物理线）；外层
    继续，外层 {1 的右 barline start 写在 m6 右线，与写在 m7 左线的
    外层主 backward 同线，外层 {1 由 m7 左线的 {2 start 隐式闭合。
    """
    outer_back = "" if drop_outer_backward else "<repeat direction='backward'/>"
    return build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("6",
                # 左线：内层次级 backward（m5 右 stop 跨侧配对）
                '<barline location="left">'
                '<repeat direction="backward"/></barline>' + NOTE
                # 右线：外层 {1 start（线前 m6 即外层第一房子）
                + '<barline location="right">'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("7",
                # 左线：外层主 backward + 外层 {2 start（隐式闭合外层 {1）
                '<barline location="left">'
                f'{outer_back}'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>', ""),
        measure("8", NOTE, ""),
    ])


def test_nested_cross_side_secondary_outer_ending_implicit_close():
    # 回归：预配对曾把 m6 左线的内层次级 backward 当成主终点，提前弹出
    # 外层 forward，导致外层 {1（m6）被判空并返回 MUSICXML_ENDING。
    result = import_ok(nested_cross_side_xml())
    rows = result["measures"]
    assert [r["volta"] for r in rows] == [
        None, None, None, 1, 2, 1, 2, None]
    assert rows[1]["left_repeat"] is True and rows[1]["times"] == 2  # 外层起点
    assert rows[2]["left_repeat"] is True and rows[2]["times"] == 2  # 内层起点
    assert rows[3]["right_repeat"] is True   # 内层主终点 m4（{1）
    assert rows[4]["right_repeat"] is True   # 内层次级终点标记 m5（线前小节）
    assert rows[5]["right_repeat"] is True   # 外层主 backward 同线 → m6
    assert rows[6]["right_repeat"] is False  # m7 只有 {2 stop，无 backward
    # 展开顺序（外层两遍；内层两遍）
    assert seq(result) == [
        "1", "2", "3", "4", "3", "5", "6",
        "2", "3", "4", "3", "5", "7", "8",
    ]


def test_nested_cross_side_secondary_preview_rows_match_expansion():
    # 预览行与确认后重新展开必须一致。
    from app.expander import expand
    result = import_ok(nested_cross_side_xml())
    reexpanded, errors = expand(result["measures"])
    assert not errors
    embedded = [v["source_measure"]
                for v in result["expansion"]["visits"]]
    confirmed = [v["source_measure"] for v in reexpanded["visits"]]
    assert embedded == confirmed
    # 配对摘要同样一致（主 / 次级终点行）
    assert result["expansion"]["repeats"] == reexpanded["repeats"]


def test_nested_cross_side_secondary_missing_outer_backward_rejected():
    # 无效配对：m7 左线缺少外层主 backward，外层 forward（源小节 m2）未
    # 闭合——按源小节报错且不产生结果（不改当前表格）。
    errors = import_fail(nested_cross_side_xml(drop_outer_backward=True))
    err = next(e for e in errors
               if e["code"] == "MUSICXML_REPEAT_UNMATCHED")
    assert err["index"] == 1 and err["measure"] == "2"


def test_sameline_double_backward_each_side_owns_its_close():
    # 同一物理线两侧各有一个 backward：右侧（m5 右 barline）与内层 {2
    # stop 同侧 → 次级；左侧（m6 左 barline）不被对侧内层 stop 误吞，
    # 仍是外层主 backward。预配对与正式配对共用同一分类，结论一致。
    from app.musicxml_import import _classify_secondary_backwards
    from app.musicxml_import import _collect_events
    import xml.etree.ElementTree as ET

    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("6",
                '<barline location="left">'
                '<repeat direction="backward"/></barline>' + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    root = ET.fromstring(xml)
    part = root.find("part")
    assert part is not None
    measures = [c for c in part if c.tag == "measure"]
    events, event_errors = _collect_events(measures, ["1", "2", "3", "4", "5", "6", "7"])
    assert not event_errors
    secondary = _classify_secondary_backwards(events)
    backs = [(ev[2], ev[1], ev[6]) for ev in events
             if ev[0] == "repeat" and ev[4] == "backward"]
    # 边界 3 右（内层 {1 主终点）、边界 4 右（内层 {2 次级）、
    # 边界 4 左（外层主 backward）
    assert backs == [(3, "right", backs[0][2]),
                     (4, "right", backs[1][2]),
                     (4, "left", backs[2][2])]
    assert backs[1][2] in secondary      # 内层 {2 闭合同侧：次级
    assert backs[0][2] not in secondary  # {1 主终点
    assert backs[2][2] not in secondary  # 同线另一侧的外层主 backward


def test_sameline_secondary_and_outer_backward_split_sides():
    # 同一道物理小节线两侧各有一个 backward（回归核心）：内层第二房子的
    # stop + 次级 backward 在 m5 右 barline；外层主 backward 在下一节 m6
    # 左 barline。二者落到同一录入行时必须保留两个终点及其反复归属，
    # 不得合并成一个 right_repeat 而漏掉外层闭合。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("6",
                '<barline location="left">'
                '<repeat direction="backward"/></barline>' + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert [r["volta"] for r in rows] == [
        None, None, None, 1, 2, None, None]
    assert rows[1]["left_repeat"] and rows[1]["times"] == 2  # 外层起点
    assert rows[2]["left_repeat"] and rows[2]["times"] == 2  # 内层起点
    assert rows[3]["right_repeat"] is True and rows[3]["right_closes"] is None
    # m5 一行两个终点：right_repeat 置真，right_closes 保留
    # [内层起点行, 外层起点行]
    assert rows[4]["right_repeat"] is True
    assert rows[4]["right_closes"] == [2, 1]
    assert rows[5]["right_repeat"] is False
    # 展开：该边界先完成内层第二房子、再闭合外层反复
    assert seq(result) == [
        "1", "2", "3", "4", "3", "5",
        "2", "3", "4", "3", "5", "6", "7"]
    # 配对摘要：内层主终点 m4、次级 m5；外层主终点 m5
    reps = result["expansion"]["repeats"]
    summary = [(r["open_measure"], r["close_measure"],
                r["secondary_close_indices"]) for r in reps]
    assert summary == [("2", "5", []), ("3", "4", [4])]


def test_sameline_double_backward_preview_rows_match_expansion():
    # 预览（导入内嵌 expansion）与确认后用录入行重新 expand 必须一致。
    from app.expander import expand
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("6",
                '<barline location="left">'
                '<repeat direction="backward"/></barline>' + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    reexpanded, errors = expand(result["measures"])
    assert not errors
    embedded = [v["source_measure"]
                for v in result["expansion"]["visits"]]
    confirmed = [v["source_measure"] for v in reexpanded["visits"]]
    assert embedded == confirmed
    assert result["expansion"]["repeats"] == reexpanded["repeats"]


def test_sameline_two_main_backwards_split_sides():
    # 无房子：内层与外层主 backward 同在一道线（右 barline + 下一节左
    # barline），两终点落到同一行，外层闭合延后到内层最终遍。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4", NOTE, ""),
        measure("5",
                '<barline location="right">'
                '<repeat direction="backward"/></barline>' + NOTE, ""),
        measure("6",
                '<barline location="left">'
                '<repeat direction="backward"/></barline>' + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[4]["right_repeat"] is True
    assert rows[4]["right_closes"] == [2, 1]
    assert rows[5]["right_repeat"] is False
    assert seq(result) == [
        "1",
        "2", "3", "4", "5", "3", "4", "5",
        "2", "3", "4", "5", "3", "4", "5",
        "6", "7"]


def test_sameline_double_endpoint_missing_outer_backward_rejected():
    # 无效配对：同线的外层主 backward 缺失（只有内层 {2 stop + 次级
    # backward 在 m5 右 barline）。外层 forward（源小节 m2）未闭合——
    # 仍按源小节报 REPEAT_UNMATCHED_OPEN，不产生结果（不改表格）。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("6", NOTE, ""),
        measure("7", NOTE, ""),
    ])
    errors = import_fail(xml)
    err = next(e for e in errors
               if e["code"] == "MUSICXML_REPEAT_UNMATCHED")
    assert err["index"] == 1 and err["measure"] == "2"


def test_sameline_double_endpoint_missing_inner_forward_rejected():
    # 无效配对：m5 右线次级 backward 与 m6 左线主 backward 同线，但没有
    # 内层 forward（只有外层 m2）。配对无法成立——未配对记号按源小节
    # （m5 / m6）报 MUSICXML_REPEAT_UNMATCHED，不产生结果（不改表格）。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", NOTE, ""),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="1" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("5",
                '<barline location="left">'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/>'
                  '<repeat direction="backward"/></barline>', ""),
        measure("6",
                '<barline location="left">'
                '<repeat direction="backward"/></barline>' + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    errors = import_fail(xml)
    unmatched = [e for e in errors
                 if e["code"] == "MUSICXML_REPEAT_UNMATCHED"]
    assert unmatched
    # 未配对的是线两侧的两个 backward，源小节为 m5 / m6（下标 4 / 5）
    assert {e["index"] for e in unmatched} <= {4, 5}


def test_inner_backward_collinear_times_propagated():
    # 嵌套共线且内层 times=3：内层起点行写 3，展开遍数正确；外层仍是 2。
    xml = build([
        measure("1", '<sound tempo="100"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", forward() + NOTE),
        measure("4",
                NOTE
                + '<barline location="right">'
                  '<repeat direction="backward" times="3"/>'
                  '<ending number="1" type="start"/></barline>', ""),
        measure("5", NOTE + right_stop_back(1), ""),
        measure("6", left_start_right_stop(2) + NOTE, ""),
        measure("7", NOTE, ""),
    ])
    result = import_ok(xml)
    rows = result["measures"]
    assert rows[2]["times"] == 3                 # 内层起点
    assert rows[1]["times"] == 2                 # 外层起点
    assert rows[3]["right_repeat"] is True       # 内层终点 m4
    assert rows[4]["right_repeat"] is True       # 外层终点 m5
    # 内层 3 遍（m3,m4 各三次），外层 2 遍；{1=m5 只在外层第 1 遍
    assert seq(result) == [
        "1", "2", "3", "4", "3", "4", "3", "4", "5",
        "2", "3", "4", "3", "4", "3", "4", "6", "7",
    ]


def test_volta_right_start_empty_house_rejected_at_source():
    # 右 barline start（非同线）在下一边界另一侧即被闭合：房子不覆盖任何
    # 小节，按源小节报错且不产生结果。
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3",
                NOTE
                + '<barline location="right">'
                  '<ending number="2" type="start"/></barline>', ""),
        measure("4",
                '<barline location="left">'
                '<ending number="2" type="stop"/></barline>' + NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_ENDING" for e in errors)


def test_volta_three_times_discontinue():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3",
                '<barline location="left">'
                '<ending number="1" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<repeat direction="backward" times="3"/></barline>'),
        measure("4",
                '<barline location="left">'
                '<ending number="1" type="stop"/>'
                '<ending number="2" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="2" type="discontinue"/>'
                  '<repeat direction="backward"/></barline>'),
        measure("5",
                '<barline location="left">'
                '<ending number="3" type="start"/></barline>' + NOTE
                + '<barline location="right">'
                  '<ending number="3" type="stop"/></barline>'),
        measure("6", NOTE, ""),
    ])
    result = import_ok(xml)
    assert seq(result) == ["1", "2", "3", "2", "4", "2", "5", "6"]


# --------------------------------------------------------------------------
# 不支持的结构与未配对记号
# --------------------------------------------------------------------------

def test_wrong_root_element():
    errors = import_fail('<?xml version="1.0"?><score-timewise></score-timewise>')
    assert errors[0]["code"] == "MUSICXML_FORMAT"


def test_invalid_xml():
    errors = import_fail("<score-partwise><part></score-partwise>")
    assert errors[0]["code"] == "MUSICXML_PARSE"


def test_multiple_parts_rejected():
    xml = build([measure("1", '<sound tempo="90"/>' + NOTE),
                 measure("2", NOTE, "")]).replace(
        "</part>", "</part><part id='P2'/>")
    xml = xml.replace(
        '<score-part id="P1">',
        '<score-part id="P1"/><score-part id="P2">')
    errors = import_fail(xml)
    assert errors[0]["code"] == "MUSICXML_PARTS"


def test_measure_count_out_of_range():
    xml = build([measure("1", '<sound tempo="90"/>' + NOTE)])
    errors = import_fail(xml)
    assert errors[0]["code"] == "MUSICXML_MEASURE_COUNT"


def test_denominator_not_four():
    xml = build([
        measure("1",
                '<attributes><time><beats>6</beats>'
                '<beat-type>8</beat-type></time></attributes>'
                '<sound tempo="90"/>' + NOTE),
        measure("2", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_TIME" and e["index"] == 0 for e in errors)


def test_senza_misura_rejected():
    xml = build([
        measure("1",
                '<attributes><time><senza-misura/></time></attributes>'
                '<sound tempo="90"/>' + NOTE),
        measure("2", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_TIME" for e in errors)


def test_first_measure_time_required():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE, ""),
        measure("2", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_TIME" and e["index"] == 0 for e in errors)


def test_mixed_meter_rejected():
    xml = build([
        measure("1",
                '<attributes><time>'
                '<beats>3</beats><beats>2</beats>'
                '<beat-type>8</beat-type><beat-type>8</beat-type>'
                '</time></attributes><sound tempo="90"/>' + NOTE),
        measure("2", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_TIME" for e in errors)


def test_first_measure_tempo_required():
    xml = build([
        measure("1", NOTE),
        measure("2", NOTE, ""),
    ])
    errors = import_fail(xml)
    # 转换后须通过现有校验：首节缺 BPM 由现有规则按源小节报出
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED" and e["index"] == 0
               for e in errors)


def test_tempo_must_be_integer():
    xml = build([
        measure("1", '<sound tempo="90.5"/>' + NOTE),
        measure("2", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_TEMPO" for e in errors)


def test_tempo_range_enforced():
    xml = build([
        measure("1", '<sound tempo="300"/>' + NOTE),
        measure("2", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_TEMPO" for e in errors)


def test_dotted_quarter_metronome_rejected():
    direction = (
        '<direction><direction-type><metronome>'
        '<beat-unit>quarter</beat-unit><beat-unit-dot/>'
        '<per-minute>80</per-minute>'
        '</metronome></direction-type></direction>'
    )
    xml = build([measure("1", direction + NOTE), measure("2", NOTE, "")])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_TEMPO" for e in errors)


def test_tempo_after_note_ignored():
    xml = build([
        measure("1", NOTE + '<sound tempo="200"/>'),
        measure("2", NOTE, ""),
    ])
    # 小节起始处无 BPM → 首节速度缺失（现有校验报出）
    errors = import_fail(xml)
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED" for e in errors)


def test_unmatched_forward_reported_at_source():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
    ])
    errors = import_fail(xml)
    err = next(e for e in errors if e["code"] == "MUSICXML_REPEAT_UNMATCHED")
    assert err["index"] == 1


def test_unmatched_backward_reported_at_source():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", backward() + NOTE),
    ])
    errors = import_fail(xml)
    err = next(e for e in errors if e["code"] == "MUSICXML_REPEAT_UNMATCHED")
    assert err["index"] == 1


def test_crossing_repeats_rejected():
    xml = build([
        measure("1", '<sound tempo="90"/>'
                '<barline location="right">'
                '<repeat direction="forward"/></barline>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", backward() + NOTE),
        measure("4", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_REPEAT_UNMATCHED" for e in errors)


def test_backward_times_out_of_range():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward() + NOTE),
        measure("3", backward(5) + NOTE),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_REPEAT_TIMES" for e in errors)


def test_ending_number_list_rejected():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward()
                + '<barline location="right">'
                  '<ending number="1,2" type="start"/>'
                  '<repeat direction="backward"/></barline>' + NOTE),
        measure("3",
                '<barline location="left">'
                '<ending number="1,2" type="stop"/></barline>' + NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_ENDING" for e in errors)


def test_ending_without_stop_rejected():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", forward()
                + '<barline location="right">'
                  '<ending number="1" type="start"/>'
                  '<repeat direction="backward"/></barline>' + NOTE),
        measure("3", NOTE, ""),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_ENDING" for e in errors)


def test_stop_without_start_rejected():
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE),
        measure("2", NOTE
                + '<barline location="right">'
                  '<ending number="2" type="stop"/></barline>'),
    ])
    errors = import_fail(xml)
    assert any(e["code"] == "MUSICXML_ENDING" and e["index"] == 1
               for e in errors)


def test_namespace_stripped():
    xml = (
        '<?xml version="1.0"?>'
        '<score-partwise xmlns="http://www.musicxml.org/xsd/MusicXML">'
        '<part id="P1">'
        + measure("1", '<sound tempo="100"/>' + NOTE)
        + measure("2", NOTE, "")
        + '</part></score-partwise>'
    )
    result = import_ok(xml)
    assert result["measures"][0]["tempo"] == 100


def test_empty_input():
    result, errors = import_musicxml("   ")
    assert result is None
    assert errors[0]["code"] == "MUSICXML_EMPTY"


def test_notes_are_ignored():
    # 音符内容（含 backup/forward、和弦）不影响结构
    extra = (
        '<note><chord/></note>'
        '<backup><duration>4</duration></backup>'
        '<forward><duration>2</duration></forward>'
    )
    xml = build([
        measure("1", '<sound tempo="90"/>' + NOTE + extra),
        measure("2", NOTE + extra, ""),
    ])
    result = import_ok(xml)
    assert seq(result) == ["1", "2"]
