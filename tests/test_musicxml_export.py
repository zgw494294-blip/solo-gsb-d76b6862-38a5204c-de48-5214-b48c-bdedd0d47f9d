"""MusicXML 结构导出测试。

覆盖：
- 文件形状（score-partwise、单 part、每小节编号 / 分母 4 拍号 / 显式
  BPM / 全小节休止符）；
- 反复、嵌套反复、连续替代结尾、终点后房子、同线双终点的 barline 编码；
- 经现有导入入口往返后：录入行、拍数、显式 BPM、反复边界、房子遍次、
  双终点归属与展开访问（来源编号、遍次路径、生效 BPM）完全一致；
  双终点后紧接新左反复起点时拆成两道左 barline（先闭后开）正常往返；
- 非法谱面按原始行报错、不产生下载；双终点位于末小节等不可编码结构
  返回导出错误。
"""

import xml.etree.ElementTree as ET

import pytest

from app.expander import expand
from app.musicxml_export import export_musicxml
from app.musicxml_import import import_musicxml


def m(number, **kw):
    row = {"number": str(number), "beats": 4, "tempo": None,
           "left_repeat": False, "right_repeat": False,
           "right_closes": None, "times": None, "volta": None}
    row.update(kw)
    return row


def export_ok(rows):
    result, errors = export_musicxml(rows)
    assert not errors, errors
    assert result is not None and result["musicxml"]
    return result["musicxml"]


def roundtrip(rows):
    """导出 → 现有入口重新导入；返回 (xml, 导入结果)。"""
    xml = export_ok(rows)
    reimp, errors = import_musicxml(xml)
    assert not errors, errors
    return xml, reimp


def assert_expansion_equal(rows, reimp):
    original, errors = expand(rows)
    assert not errors
    assert reimp["expansion"] == original
    return original


def seq_of(result):
    return [v["source_measure"] for v in result["expansion"]["visits"]]


# --------------------------------------------------------------------------
# 文件形状
# --------------------------------------------------------------------------

def test_plain_score_shape():
    xml = export_ok([m("1", tempo=90), m("2")])
    root = ET.fromstring(xml)  # 必须是合法 XML
    assert root.tag == "score-partwise"
    parts = root.findall("part")
    assert len(parts) == 1
    measures = parts[0].findall("measure")
    assert [mm.get("number") for mm in measures] == ["1", "2"]
    # 每小节分母为 4 的拍号
    for mm, beats in zip(measures, (4, 4)):
        time_el = mm.find("attributes/time")
        assert time_el.find("beats").text == str(beats)
        assert time_el.find("beat-type").text == "4"
    # 仅显式标记行有 BPM；全小节休止符（rest）每小节一个
    assert measures[0].find("sound").get("tempo") == "90"
    assert measures[1].find("sound") is None
    assert len(measures[0].findall("note/rest")) == 1
    assert len(measures[1].findall("note/rest")) == 1


def test_beats_and_explicit_tempos_written_per_measure():
    rows = [m("A", beats=2, tempo=60), m("B", beats=7),
            m("C", beats=128, tempo=240)]
    xml, reimp = roundtrip(rows)
    root = ET.fromstring(xml)
    measures = root.find("part").findall("measure")
    assert [mm.find("attributes/time/beats").text for mm in measures] == \
        ["2", "7", "128"]
    assert [mm.find("sound") is not None for mm in measures] == \
        [True, False, True]
    # 生效 BPM：B 沿用 60，C 改 240
    assert [t["tempo"] for t in reimp["expansion"]["tempos"]] == [60, 60, 240]
    assert [t["marked"] for t in reimp["expansion"]["tempos"]] == \
        [True, False, True]


def test_measure_numbers_are_xml_escaped():
    rows = [m("前&奏<A>", tempo=90), m('b"1'), m("尾")]
    xml, reimp = roundtrip(rows)
    assert "前&amp;奏" in xml and "&lt;A&gt;" in xml
    assert [r["number"] for r in reimp["measures"]] == ['前&奏<A>', 'b"1', "尾"]


# --------------------------------------------------------------------------
# 反复与次数
# --------------------------------------------------------------------------

def test_simple_repeat_roundtrip():
    rows = [m("A", tempo=90), m("B", left_repeat=True, times=3),
            m("C", right_repeat=True), m("D")]
    xml, reimp = roundtrip(rows)
    assert 'direction="forward"' in xml
    assert 'direction="backward" times="3"' in xml
    imported_rows = reimp["measures"]
    assert imported_rows[1]["left_repeat"] is True
    assert imported_rows[1]["times"] == 3
    assert imported_rows[2]["right_repeat"] is True
    assert seq_of(reimp) == ["A", "B", "C", "B", "C", "B", "C", "D"]
    assert_expansion_equal(rows, reimp)


def test_nested_repeats_roundtrip():
    rows = [m("1", tempo=90),
            m("2", left_repeat=True, times=2),
            m("3", left_repeat=True, times=2),
            m("4", right_repeat=True),
            m("5", right_repeat=True)]
    _xml, reimp = roundtrip(rows)
    assert seq_of(reimp) == ["1", "2", "3", "4", "3", "4", "5",
                             "2", "3", "4", "3", "4", "5"]
    assert_expansion_equal(rows, reimp)


def test_single_measure_repeat_roundtrip():
    rows = [m("1", tempo=90),
            m("2", left_repeat=True, times=2, right_repeat=True)]
    _xml, reimp = roundtrip(rows)
    assert seq_of(reimp) == ["1", "2", "2"]


# --------------------------------------------------------------------------
# 连续替代结尾 / 终点后房子
# --------------------------------------------------------------------------

def test_volta_houses_roundtrip():
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=3),
        m("3", volta=1, right_repeat=True),
        m("4", volta=2, right_repeat=True),
        m("5", volta=3),
        m("6"),
    ]
    xml, reimp = roundtrip(rows)
    # 三个房子 start/stop 各一对；主 / 次级 backward 共 2 个带 times 的主终点
    assert xml.count('type="start"') == 3
    assert xml.count('type="stop"') == 3
    assert seq_of(reimp) == ["1", "2", "3", "2", "4", "2", "5", "6"]
    assert [r["volta"] for r in reimp["measures"]] == \
        [None, None, 1, 2, 3, None]
    assert_expansion_equal(rows, reimp)


def test_multi_measure_house_roundtrip():
    rows = [
        m("1", tempo=100), m("2", left_repeat=True, times=2),
        m("3", volta=1), m("4", volta=1, right_repeat=True),
        m("5", volta=2), m("6"),
    ]
    _xml, reimp = roundtrip(rows)
    assert [r["volta"] for r in reimp["measures"]] == \
        [None, None, 1, 1, 2, None]
    assert seq_of(reimp) == ["1", "2", "3", "4", "2", "5", "6"]
    assert_expansion_equal(rows, reimp)


def test_volta_after_endpoint_tail_roundtrip():
    # 主终点在 {1，{2 是终点之后的尾部房子（最末房子无 backward）
    rows = [
        m("1", tempo=90), m("2", left_repeat=True, times=2), m("3"),
        m("4", volta=1, right_repeat=True),
        m("5", volta=2), m("6"),
    ]
    _xml, reimp = roundtrip(rows)
    assert seq_of(reimp) == ["1", "2", "3", "4", "2", "3", "5", "6"]
    assert_expansion_equal(rows, reimp)


def test_nested_with_inner_and_outer_houses_roundtrip():
    # 内层 {1/{2} + 外层 {1/{2}}（nested_cross_side 行模型）
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", left_repeat=True, times=2),
        m("4", volta=1, right_repeat=True),
        m("5", volta=2, right_repeat=True),
        m("6", volta=1, right_repeat=True),
        m("7", volta=2),
        m("8"),
    ]
    _xml, reimp = roundtrip(rows)
    assert [r["volta"] for r in reimp["measures"]] == \
        [None, None, None, 1, 2, 1, 2, None]
    assert seq_of(reimp) == [
        "1", "2", "3", "4", "3", "5", "6",
        "2", "3", "4", "3", "5", "7", "8",
    ]
    assert_expansion_equal(rows, reimp)
    # 配对摘要（主终点、次级终点）也一致
    assert reimp["expansion"]["repeats"] == expand(rows)[0]["repeats"]


# --------------------------------------------------------------------------
# 同线双终点：两个 backward 分置同一物理小节线两侧，保留内外层归属
# --------------------------------------------------------------------------

def test_sameline_double_close_houses_roundtrip():
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", left_repeat=True, times=2),
        m("4", volta=1, right_repeat=True),
        m("5", volta=2, right_repeat=True, right_closes=[2, 1]),
        m("6"), m("7"),
    ]
    xml, reimp = roundtrip(rows)
    imported = reimp["measures"]
    # 双终点归属保留：内层起点行 2、外层起点行 1
    assert imported[4]["right_repeat"] is True
    assert imported[4]["right_closes"] == [2, 1]
    assert imported[5]["right_repeat"] is False
    assert seq_of(reimp) == [
        "1", "2", "3", "4", "3", "5",
        "2", "3", "4", "3", "5", "6", "7"]
    assert_expansion_equal(rows, reimp)

    # 编码形状：内层 {2 stop + 次级 backward 在 m5 右 barline，外层主
    # backward（带 times）在 m6 左 barline —— 同一物理线两侧。
    root = ET.fromstring(xml)
    measures = root.find("part").findall("measure")
    m5_right = measures[4].find('barline[@location="right"]')
    m6_left = measures[5].find('barline[@location="left"]')
    assert m5_right.find('ending[@number="2"][@type="stop"]') is not None
    inner_back = m5_right.find('repeat[@direction="backward"]')
    outer_back = m6_left.find('repeat[@direction="backward"]')
    assert inner_back is not None and inner_back.get("times") is None
    assert outer_back.get("times") == "2"


def test_sameline_double_main_no_houses_roundtrip():
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", left_repeat=True, times=2),
        m("4"),
        m("5", right_repeat=True, right_closes=[2, 1]),
        m("6"), m("7"),
    ]
    _xml, reimp = roundtrip(rows)
    assert reimp["measures"][4]["right_closes"] == [2, 1]
    assert seq_of(reimp) == [
        "1",
        "2", "3", "4", "5", "3", "4", "5",
        "2", "3", "4", "5", "3", "4", "5",
        "6", "7"]
    assert_expansion_equal(rows, reimp)


def test_double_close_visits_carry_correct_path_and_bpm():
    rows = [
        m("1", tempo=80),
        m("2", left_repeat=True, times=2, tempo=120),
        m("3", left_repeat=True, times=2),
        m("4", volta=1, right_repeat=True),
        m("5", volta=2, right_repeat=True, right_closes=[2, 1]),
        m("6"), m("7"),
    ]
    _xml, reimp = roundtrip(rows)
    original = expand(rows)[0]
    # 每次访问的遍次路径与生效 BPM 逐一相等
    for a, b in zip(original["visits"], reimp["expansion"]["visits"]):
        assert a["source_measure"] == b["source_measure"]
        assert a["path"] == b["path"]
        assert a["tempo"] == b["tempo"]
        assert a["tempo_marked"] == b["tempo_marked"]


# --------------------------------------------------------------------------
# 非法谱面：按原始行报错，不产生下载
# --------------------------------------------------------------------------

def test_invalid_score_returns_row_errors_no_file():
    rows = [m("A", tempo=90), m("B", left_repeat=True, times=2), m("C")]
    result, errors = export_musicxml(rows)
    assert result is None
    code = next(e["code"] for e in errors
                if e["code"] == "REPEAT_UNMATCHED_OPEN")
    assert code
    located = next(e for e in errors if e["code"] == "REPEAT_UNMATCHED_OPEN")
    assert located["index"] == 1 and located["measure"] == "B"


def test_missing_first_tempo_reports_original_error():
    rows = [m("A"), m("B")]
    result, errors = export_musicxml(rows)
    assert result is None
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED" and e["index"] == 0
               for e in errors)


def test_too_many_visits_still_rejected():
    rows = [m("S", tempo=90)]
    for i in range(5):  # 5 层 ×4 遍 = 1024 次访问，超过上限 512
        rows.append(m(f"L{i}", left_repeat=True, times=4))
    rows.append(m("M"))
    for i in range(5):
        rows.append(m(f"R{i}", right_repeat=True))
    result, errors = export_musicxml(rows)
    assert result is None
    assert any(e["code"] == "TOO_MANY_VISITS" for e in errors)


def test_double_close_then_forward_roundtrip():
    # 双终点行的下一小节恰好又是左反复起点：两个 backward 分置同一物理
    # 小节线两侧，新 forward 作为独立左侧 barline，按“先闭合旧反复、后
    # 开启新反复”的顺序写出（两道左 barline），可正常下载并往返。
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", left_repeat=True, times=2),
        m("4", volta=1, right_repeat=True),
        m("5", volta=2, right_repeat=True, right_closes=[2, 1]),
        m("6", left_repeat=True, times=2),
        m("7", right_repeat=True),
        m("8"),
    ]
    assert not expand(rows)[1]  # 展开器接受该谱面
    xml, reimp = roundtrip(rows)

    imported = reimp["measures"]
    # 双终点归属与各层起点全部保留
    assert imported[4]["right_closes"] == [2, 1]
    assert imported[5]["left_repeat"] is True and imported[5]["times"] == 2
    assert imported[5]["right_repeat"] is False
    assert seq_of(reimp) == [
        "1", "2", "3", "4", "3", "5",
        "2", "3", "4", "3", "5",
        "6", "7", "6", "7", "8"]
    assert_expansion_equal(rows, reimp)

    # 编码形状：m6 开头两道左 barline——第一道是带 times 的外层主
    # backward（先闭合），第二道是 forward（后开启）。
    root = ET.fromstring(xml)
    measures = root.find("part").findall("measure")
    lefts = measures[5].findall('barline[@location="left"]')
    assert len(lefts) == 2
    outer_back = lefts[0].find('repeat[@direction="backward"]')
    new_forward = lefts[1].find('repeat[@direction="forward"]')
    assert outer_back is not None and outer_back.get("times") == "2"
    assert new_forward is not None
    # 每道 barline 至多一个 repeat
    assert len(lefts[0].findall("repeat")) == 1
    assert len(lefts[1].findall("repeat")) == 1


def test_double_close_then_forward_with_house_start_roundtrip():
    # 新左反复起点同时是新反复 {1 房子首小节：外层主 backward 独占第一道
    # 左 barline，房子 start 与 forward 同处第二道；层级归属不丢。
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", left_repeat=True, times=2),
        m("4", volta=1, right_repeat=True),
        m("5", volta=2, right_repeat=True, right_closes=[2, 1]),
        m("6", left_repeat=True, times=2, volta=1),
        m("7", volta=1, right_repeat=True),
        m("8", volta=2),
        m("9"),
    ]
    xml, reimp = roundtrip(rows)
    imported = reimp["measures"]
    assert imported[4]["right_closes"] == [2, 1]
    assert imported[5]["left_repeat"] is True
    assert imported[5]["volta"] == 1
    assert seq_of(reimp) == [
        "1", "2", "3", "4", "3", "5",
        "2", "3", "4", "3", "5",
        "6", "7", "8", "9"]
    assert_expansion_equal(rows, reimp)

    root = ET.fromstring(xml)
    measures = root.find("part").findall("measure")
    lefts = measures[5].findall('barline[@location="left"]')
    assert len(lefts) == 2
    assert lefts[0].find('repeat[@direction="backward"]') is not None
    assert lefts[1].find('ending[@number="1"][@type="start"]') is not None
    assert lefts[1].find('repeat[@direction="forward"]') is not None


def test_double_main_then_forward_roundtrip():
    # 无房子的两个同线主 backward + 下一行新 forward，同样往返。
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", left_repeat=True, times=2),
        m("4"),
        m("5", right_repeat=True, right_closes=[2, 1]),
        m("6", left_repeat=True, times=2),
        m("7", right_repeat=True),
        m("8"),
    ]
    _xml, reimp = roundtrip(rows)
    assert reimp["measures"][4]["right_closes"] == [2, 1]
    assert reimp["measures"][5]["left_repeat"] is True
    assert_expansion_equal(rows, reimp)


def test_double_close_at_last_measure_reports_export_error():
    # 双终点行恰好是最后一个小节：外层主 backward 需要下一小节左
    # barline，已无小节可写——仍返回 MUSICXML_EXPORT_BARLINE，不下载。
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", left_repeat=True, times=2),
        m("4", volta=1, right_repeat=True),
        m("5", volta=2, right_repeat=True, right_closes=[2, 1]),
    ]
    assert not expand(rows)[1]  # 展开器本身接受该谱面
    result, errors = export_musicxml(rows)
    assert result is None
    assert any(e["code"] == "MUSICXML_EXPORT_BARLINE"
               and e["index"] == 4 for e in errors)


def test_export_does_not_mutate_rows():
    rows = [
        m("1", tempo=100),
        m("2", left_repeat=True, times=2),
        m("3", right_repeat=True),
        m("4"),
    ]
    import copy
    snapshot = copy.deepcopy(rows)
    export_musicxml(rows)
    assert rows == snapshot
