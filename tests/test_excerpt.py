"""演奏片段摘谱（app.excerpt.build_excerpt 与 /api/excerpt）测试。

覆盖：
- 选段校验（段数、形状、范围、互不重叠且按演奏先后、合计 2~64 次访问）；
- 摘谱行生成（编号重排为 1 起连续整数、保留拍数、清除反复与房子记号、
  段首写生效 BPM、段内仅速度变化时写入）；
- 映射（新行 → 原访问序号 / 来源编号 / 遍次路径）与分段信息；
- MusicXML 导出与回读：回读演奏带的拍数与生效 BPM 逐行对应所选访问；
- 原谱不被修改；/api/excerpt 的 200 / 422 / 400。
"""

import copy

from fastapi.testclient import TestClient

from app.excerpt import (
    MAX_EXCERPT_VISITS,
    MAX_SEGMENTS,
    build_excerpt,
)
from app.expander import expand
from app.main import app
from app.musicxml_import import import_musicxml

client = TestClient(app)


def m(number, beats=4, tempo=None, left=False, right=False, times=None,
      volta=None, right_closes=None):
    return {
        "number": str(number),
        "beats": beats,
        "tempo": tempo,
        "left_repeat": left,
        "right_repeat": right,
        "right_closes": right_closes,
        "times": times,
        "volta": volta,
    }


def rows_demo():
    """前奏 |: A |: B C1{1 :| C2{2 D :| 尾奏（14 次访问，D 起 60 BPM）。"""
    return [
        m("前奏", tempo=90),
        m("A", left=True, times=2),
        m("B", left=True, times=2),
        m("C1", beats=2, right=True, volta=1),
        m("C2", beats=2, volta=2),
        m("D", right=True, tempo=60),
        m("尾奏"),
    ]


def rows_nested():
    """S |: A |: B C :| D :| T → S (A (B C)×2 D)×2 T（外层重入内层重计）。"""
    return [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", left=True, times=2),
        m("C", right=True),
        m("D", right=True),
        m("T"),
    ]


def excerpt_ok(measures, segments):
    result, errors = build_excerpt(
        {"measures": measures, "segments": segments})
    assert errors is None, errors
    assert result is not None
    return result


def seg(start, end):
    return {"start": start, "end": end}


# --------------------------------------------------------------------------
# 摘谱行生成
# --------------------------------------------------------------------------

def test_rows_renumbered_and_marks_cleared():
    result = excerpt_ok(rows_demo(), [seg(2, 4), seg(9, 11)])
    rows = result["rows"]
    # 编号重排为 1 起连续整数
    assert [r["number"] for r in rows] == ["1", "2", "3", "4", "5", "6"]
    # 拍数保留（C1 为 2 拍）
    assert [r["beats"] for r in rows] == [4, 4, 2, 4, 2, 4]
    # 反复与房子记号全部清除
    for r in rows:
        assert r["left_repeat"] is False
        assert r["right_repeat"] is False
        assert r["right_closes"] is None
        assert r["times"] is None
        assert r["volta"] is None


def test_segment_first_row_always_writes_tempo():
    # 两段生效 BPM 相同（都是 90）：第 2 段首行仍写入，避免剪接处沿用前段
    result = excerpt_ok(rows_demo(), [seg(2, 4), seg(9, 11)])
    tempos = [r["tempo"] for r in result["rows"]]
    assert tempos[0] == 90          # 第 1 段首行
    assert tempos[3] == 90          # 第 2 段首行（即使与前段末尾相同也写入）
    assert tempos[1] is None and tempos[2] is None
    assert tempos[4] is None and tempos[5] is None


def test_in_segment_tempo_written_only_on_change():
    # 第 1 段 [1,1]（90），第 2 段 [7,8]（D=60 → A=90）：段内变化处写入
    result = excerpt_ok(rows_demo(), [seg(1, 1), seg(7, 8)])
    tempos = [r["tempo"] for r in result["rows"]]
    assert tempos == [90, 60, 90]


def test_tempo_change_inside_segment():
    # [6,8]：C2(90) → D(60) → A(90)，段内两次变化都写入
    result = excerpt_ok(rows_demo(), [seg(6, 8)])
    assert [r["tempo"] for r in result["rows"]] == [90, 60, 90]


def test_mapping_traces_origin_visit():
    result = excerpt_ok(rows_nested(), [seg(2, 4), seg(8, 10)])
    mapping = result["mapping"]
    assert [x["row"] for x in mapping] == [1, 2, 3, 4, 5, 6]
    assert [x["segment"] for x in mapping] == [1, 1, 1, 2, 2, 2]
    assert [x["visit"]["global_index"] for x in mapping] == [2, 3, 4, 8, 9, 10]
    assert [x["visit"]["source_measure"] for x in mapping] == \
        ["A", "B", "C", "A", "B", "C"]
    # 遍次路径：第 2 段是外层第 2 遍（内层重新计数）
    # 访问 8 = A（外层起点行，不在内层反复内）→ 仅外层路径
    assert [(p["pass"], p["times"]) for p in mapping[3]["visit"]["path"]] == \
        [(2, 2)]
    # 访问 9 = B（外层第 2 遍里的内层第 1 遍）
    assert [(p["pass"], p["times"]) for p in mapping[4]["visit"]["path"]] == \
        [(2, 2), (1, 2)]
    # 同一来源小节在不同遍次可重复选入（B 在外层第 1 / 第 2 遍各选一次）
    assert mapping[1]["visit"]["source_measure"] == "B"
    assert mapping[1]["visit"]["path"][0]["pass"] == 1
    assert mapping[4]["visit"]["source_measure"] == "B"
    assert mapping[4]["visit"]["path"][0]["pass"] == 2


def test_segments_info():
    result = excerpt_ok(rows_demo(), [seg(2, 4), seg(9, 11)])
    assert result["segments"] == [
        {"segment": 1, "start": 2, "end": 4, "visit_count": 3,
         "first_row": 1},
        {"segment": 2, "start": 9, "end": 11, "visit_count": 3,
         "first_row": 4},
    ]
    assert result["segment_count"] == 2
    assert result["visit_count"] == 6
    assert result["total_beats"] == 4 + 4 + 2 + 4 + 2 + 4
    assert result["max_segments"] == MAX_SEGMENTS
    assert result["max_excerpt_visits"] == MAX_EXCERPT_VISITS


def test_single_visit_and_adjacent_segments_allowed():
    # 单访问段与相邻闭区间都合法（合计 ≥ 2）
    result = excerpt_ok(rows_nested(), [seg(2, 2), seg(3, 3), seg(4, 5)])
    assert result["visit_count"] == 4
    assert [x["visit"]["global_index"] for x in result["mapping"]] == \
        [2, 3, 4, 5]


def test_score_not_modified():
    measures = rows_demo()
    snapshot = copy.deepcopy(measures)
    excerpt_ok(measures, [seg(1, 3)])
    assert measures == snapshot


# --------------------------------------------------------------------------
# MusicXML 导出与回读
# --------------------------------------------------------------------------

def test_musicxml_roundtrip_matches_selected_visits():
    measures = rows_demo()
    score, errors = expand(measures)
    assert not errors
    visits = score["visits"]
    selected = [visits[gi - 1] for gi in (2, 3, 4, 9, 10, 11)]

    result = excerpt_ok(measures, [seg(2, 4), seg(9, 11)])
    xml = result["musicxml"]
    assert xml.startswith("<?xml")
    assert "<score-partwise" in xml

    # 回读：拍数与生效 BPM 逐行对应所选访问
    reimp, reimp_errors = import_musicxml(xml)
    assert not reimp_errors, reimp_errors
    reimp_visits = reimp["expansion"]["visits"]
    assert len(reimp_visits) == len(selected)
    for rv, sv in zip(reimp_visits, selected):
        assert rv["beats"] == sv["beats"]
        assert rv["tempo"] == sv["tempo"]
    # 回读编号即 1 起连续整数
    assert [v["source_measure"] for v in reimp_visits] == \
        ["1", "2", "3", "4", "5", "6"]


def test_musicxml_written_tempos_at_segment_heads():
    result = excerpt_ok(rows_demo(), [seg(1, 1), seg(7, 8)])
    xml = result["musicxml"]
    # 三行分别写 90（第 1 段首行）、60（第 2 段首行）、90（段内变化）
    assert xml.count('<sound tempo="90"/>') == 2
    assert xml.count('<sound tempo="60"/>') == 1


def test_excerpt_from_double_close_score():
    # 同线双终点谱面（right_closes）也能正常摘谱（摘谱行清除一切记号）
    measures = [
        m("1", tempo=90),
        m("2", left=True, times=2),
        m("3", left=True, times=2),
        m("4", right=True, volta=1),
        m("5", right=True, volta=2, right_closes=[2, 1]),
        m("6"),
        m("7"),
    ]
    score, errors = expand(measures)
    assert not errors, errors
    result = excerpt_ok(measures, [seg(2, 5)])
    assert result["visit_count"] == 4
    reimp, reimp_errors = import_musicxml(result["musicxml"])
    assert not reimp_errors, reimp_errors
    assert [v["tempo"] for v in reimp["expansion"]["visits"]] == [90] * 4


# --------------------------------------------------------------------------
# 选段校验
# --------------------------------------------------------------------------

def test_segments_must_be_list():
    _, errors = build_excerpt({"measures": rows_nested(), "segments": "x"})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENTS_FORMAT"


def test_segment_count_bounds():
    _, errors = build_excerpt({"measures": rows_nested(), "segments": []})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_COUNT"
    _, errors = build_excerpt(
        {"measures": rows_nested(),
         "segments": [seg(1, 1), seg(2, 2), seg(3, 3), seg(4, 4), seg(5, 5)]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_COUNT"


def test_segment_shape_errors():
    _, errors = build_excerpt({"measures": rows_nested(), "segments": [1]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_FORMAT"
    _, errors = build_excerpt(
        {"measures": rows_nested(), "segments": [seg(5, 2)]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_FORMAT"
    _, errors = build_excerpt(
        {"measures": rows_nested(), "segments": [{"start": True, "end": 2}]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_FORMAT"


def test_segment_range_error_located():
    _, errors = build_excerpt(
        {"measures": rows_nested(), "segments": [seg(1, 2), seg(10, 99)]})
    err = errors["segment_errors"][0]
    assert err["code"] == "EXCERPT_SEGMENT_RANGE"
    assert err["index"] == 1  # 按段（0 基）定位


def test_segment_overlap_and_order():
    # 重叠
    _, errors = build_excerpt(
        {"measures": rows_nested(), "segments": [seg(1, 3), seg(3, 4)]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_ORDER"
    # 未按演奏先后
    _, errors = build_excerpt(
        {"measures": rows_nested(), "segments": [seg(4, 5), seg(1, 2)]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_ORDER"


def test_total_visit_count_bounds():
    # 合计不足 2 次
    _, errors = build_excerpt({"measures": rows_nested(), "segments": [seg(2, 2)]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_VISIT_COUNT"
    # 合计超过 64 次：|: A m1..m15 Z :| ×4 展开 17×4 = 68 次访问
    many = [m("A", tempo=100, left=True, times=4)] \
        + [m(f"m{i}") for i in range(1, 16)] + [m("Z", right=True)]
    score, errors2 = expand(many)
    assert not errors2
    assert score["visit_count"] == 68
    _, errors = build_excerpt({"measures": many, "segments": [seg(1, 65)]})
    assert errors["segment_errors"][0]["code"] == "EXCERPT_VISIT_COUNT"
    # 恰好 64 次合法
    result = excerpt_ok(many, [seg(1, 64)])
    assert result["visit_count"] == 64


def test_score_errors_reported_separately():
    _, errors = build_excerpt({
        "measures": [m("A", tempo=90, left=True, times=2), m("B")],
        "segments": [seg(1, 2)],
    })
    assert errors["score_errors"]
    assert errors["segment_errors"] == []
    assert errors["excerpt_errors"] == []


def test_measures_not_list():
    _, errors = build_excerpt({"measures": "x", "segments": [seg(1, 2)]})
    assert errors["score_errors"][0]["code"] == "INVALID_REQUEST"


# --------------------------------------------------------------------------
# /api/excerpt
# --------------------------------------------------------------------------

def test_api_excerpt_ok():
    resp = client.post("/api/excerpt", json={
        "measures": rows_demo(),
        "segments": [seg(2, 4), seg(9, 11)],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    assert result["segment_count"] == 2
    assert result["visit_count"] == 6
    assert [r["number"] for r in result["rows"]] == \
        ["1", "2", "3", "4", "5", "6"]
    assert result["mapping"][0]["visit"]["global_index"] == 2
    assert result["musicxml"].startswith("<?xml")


def test_api_excerpt_422_groups():
    resp = client.post("/api/excerpt", json={
        "measures": rows_demo(),
        "segments": [seg(1, 3), seg(3, 4)],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["segment_errors"][0]["code"] == "EXCERPT_SEGMENT_ORDER"
    assert body["score_errors"] == []
    assert body["excerpt_errors"] == []


def test_api_excerpt_invalid_json_400():
    resp = client.post("/api/excerpt", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_api_excerpt_missing_fields_400():
    resp = client.post("/api/excerpt", json={"measures": rows_demo()})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"
