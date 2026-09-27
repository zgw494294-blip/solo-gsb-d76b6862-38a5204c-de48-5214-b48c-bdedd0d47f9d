"""FastAPI 路由测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def m(number, **kw):
    row = {"number": str(number), "beats": 4, "tempo": 120,
           "left_repeat": False, "right_repeat": False,
           "times": None, "volta": None}
    row.update(kw)
    return row


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["max_visits"] == 512


def test_index_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_expand_ok():
    resp = client.post("/api/expand", json=[
        m("A"),
        m("B", left_repeat=True, times=2),
        m("C", right_repeat=True),
        m("D"),
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    assert [v["source_measure"] for v in result["visits"]] == [
        "A", "B", "C", "B", "C", "D"]
    assert result["visit_count"] == 6
    assert result["repeats"][0]["times"] == 2


def test_expand_validation_422_located():
    resp = client.post("/api/expand", json=[
        m("A", left_repeat=True, times=2),
        m("B"),
    ])
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    err = body["errors"][0]
    assert err["code"] == "REPEAT_UNMATCHED_OPEN"
    assert err["index"] == 0
    assert err["measure"] == "A"


def test_expand_too_many_visits_422():
    resp = client.post("/api/expand", json=[
        m("S"),
        m("A", left_repeat=True, times=4),
        m("B", left_repeat=True, times=4),
        m("C", left_repeat=True, times=4),
        m("D"), m("E"), m("F"), m("G"), m("H"), m("I"),
        m("J", right_repeat=True),
        m("K", right_repeat=True),
        m("L", right_repeat=True),
    ])
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["code"] == "TOO_MANY_VISITS"


def test_invalid_json_400():
    resp = client.post("/api/expand", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_static_assets_mounted_after_api():
    # 确认 /api 路由没有被 StaticFiles 兜底吃掉
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_expand_tempo_carry_over():
    resp = client.post("/api/expand", json=[
        m("A", tempo=72),
        m("B", tempo=None),
    ])
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert [v["tempo"] for v in result["visits"]] == [72, 72]
    assert [t["tempo"] for t in result["tempos"]] == [72, 72]


def test_expand_tempo_missing_first_422():
    resp = client.post("/api/expand", json=[
        {"number": "A", "beats": 4, "left_repeat": False,
         "right_repeat": False, "times": None, "volta": None},
        m("B"),
    ])
    assert resp.status_code == 422
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED"
               for e in resp.json()["errors"])


# --------------------------------------------------------------------------
# MusicXML 结构导入 /api/import-musicxml
# --------------------------------------------------------------------------

MX_HEADER = """<?xml version="1.0"?>
<score-partwise version="4.0">
<part-list><score-part id="P1"><part-name>M</part-name></score-part></part-list>
<part id="P1">{}</part>
</score-partwise>"""


def mxm(num, inner="", attrs='<attributes><time><beats>4</beats>'
                            '<beat-type>4</beat-type></time></attributes>'):
    return f'<measure number="{num}">{attrs}{inner}</measure>'


MX_NOTE = '<note/>'


def test_import_musicxml_ok():
    xml = MX_HEADER.format(
        mxm("1", '<sound tempo="90"/>' + MX_NOTE)
        + mxm("2",
              '<barline location="left"><repeat direction="forward"/></barline>'
              + MX_NOTE
              + '<barline location="right"><repeat direction="backward"/></barline>')
    )
    resp = client.post("/api/import-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert [m["number"] for m in body["result"]["measures"]] == ["1", "2"]
    # forward / backward 分别在 m2 左、右边界：m2 单小节反复
    assert [v["source_measure"] for v in body["result"]["expansion"]["visits"]] \
        == ["1", "2", "2"]


def test_import_musicxml_422_located_at_source_measure():
    xml = MX_HEADER.format(
        mxm("1", '<sound tempo="90"/>' + MX_NOTE)
        + mxm("2",
              '<barline location="left"><repeat direction="forward"/></barline>'
              + MX_NOTE)
    )
    resp = client.post("/api/import-musicxml", json={"musicxml": xml})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert any(e["code"] == "MUSICXML_REPEAT_UNMATCHED" and e["index"] == 1
               for e in body["errors"])


def test_import_musicxml_sameline_split_sides_ok():
    # 回归：{1 start 在 m3 右 barline，stop+backward 与 {2 start 写在
    # 下一小节（m4）左 barline —— 同一道物理线两侧按边界配对：
    # m3 即第一房子兼主反复终点，{2 从 m4 开始，预览行与展开一致。
    xml = MX_HEADER.format(
        mxm("1", '<sound tempo="90"/>' + MX_NOTE)
        + mxm("2",
              '<barline location="left"><repeat direction="forward"/></barline>'
              + MX_NOTE)
        + mxm("3",
              MX_NOTE
              + '<barline location="right">'
                '<ending number="1" type="start"/></barline>', "")
        + mxm("4",
              '<barline location="left">'
              '<ending number="1" type="stop"/>'
              '<repeat direction="backward"/>'
              '<ending number="2" type="start"/></barline>'
              + MX_NOTE
              + '<barline location="right">'
                '<ending number="2" type="stop"/></barline>', "")
        + mxm("5", MX_NOTE, "")
    )
    resp = client.post("/api/import-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    rows = body["result"]["measures"]
    assert [r["volta"] for r in rows] == [None, None, 1, 2, None]
    assert rows[2]["right_repeat"] is True
    assert rows[1]["left_repeat"] is True and rows[1]["times"] == 2
    assert [v["source_measure"] for v in body["result"]["expansion"]["visits"]] \
        == ["1", "2", "3", "2", "4", "5"]


def test_import_musicxml_sameline_double_endpoint_ok():
    # 同一道线两侧：内层 {2 stop+次级 backward 在 m5 右 barline，外层主
    # backward 在 m6 左 barline —— 一行保留两个终点及归属，可正常预览。
    xml = MX_HEADER.format(
        mxm("1", '<sound tempo="90"/>' + MX_NOTE)
        + mxm("2",
              '<barline location="left"><repeat direction="forward"/></barline>'
              + MX_NOTE)
        + mxm("3",
              '<barline location="left"><repeat direction="forward"/></barline>'
              + MX_NOTE)
        + mxm("4",
              '<barline location="left">'
              '<ending number="1" type="start"/></barline>'
              + MX_NOTE
              + '<barline location="right">'
              '<ending number="1" type="stop"/>'
              '<repeat direction="backward"/></barline>')
        + mxm("5",
              '<barline location="left">'
              '<ending number="2" type="start"/></barline>'
              + MX_NOTE
              + '<barline location="right">'
              '<ending number="2" type="stop"/>'
              '<repeat direction="backward"/></barline>')
        + mxm("6",
              '<barline location="left">'
              '<repeat direction="backward"/></barline>'
              + MX_NOTE, "")
        + mxm("7", MX_NOTE, "")
    )
    resp = client.post("/api/import-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    rows = body["result"]["measures"]
    assert rows[4]["right_repeat"] is True
    assert rows[4]["right_closes"] == [2, 1]
    assert [v["source_measure"] for v in body["result"]["expansion"]["visits"]] \
        == ["1", "2", "3", "4", "3", "5",
            "2", "3", "4", "3", "5", "6", "7"]
    # 确认后用录入行再调 /api/expand，演奏顺序一致
    resp2 = client.post("/api/expand", json=rows)
    assert resp2.status_code == 200
    assert [v["source_measure"] for v in resp2.json()["result"]["visits"]] \
        == [v["source_measure"]
            for v in body["result"]["expansion"]["visits"]]


def test_import_musicxml_invalid_json_400():
    resp = client.post("/api/import-musicxml", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_import_musicxml_missing_field_400():
    resp = client.post("/api/import-musicxml", json={"score": "x"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"


def test_import_musicxml_bad_xml_422():
    resp = client.post("/api/import-musicxml",
                       json={"musicxml": "<score-partwise>"})
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["code"] == "MUSICXML_PARSE"


def test_compare_ok():
    resp = client.post("/api/compare", json={
        "baseline": [m("A"), m("B")],
        "current": [m("A"), m("B", beats=2)],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    assert result["stats"] == {"paired": 2, "unchanged": 1, "changed": 1,
                               "added": 0, "removed": 0}
    pair = result["events"][1]
    assert pair["kind"] == "pair"
    assert pair["changes"] == ["beats"]
    assert pair["time_offset"] == 0
    assert result["first_difference"]["kind"] == "pair"
    assert result["first_difference"]["align_index"] == 1


def test_compare_added_removed_and_first_difference():
    resp = client.post("/api/compare", json={
        "baseline": [m("A"), m("B"), m("C")],
        "current": [m("A"), m("X"), m("C")],
    })
    assert resp.status_code == 200
    result = resp.json()["result"]
    kinds = [e["kind"] for e in result["events"]]
    assert kinds == ["pair", "removed", "added", "pair"]
    assert result["first_difference"]["kind"] == "removed"
    assert result["first_difference"]["align_index"] == 1


def test_compare_current_invalid_422():
    resp = client.post("/api/compare", json={
        "baseline": [m("A"), m("B")],
        "current": [m("A", left_repeat=True, times=2), m("B")],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["baseline_errors"] == []
    assert any(e["code"] == "REPEAT_UNMATCHED_OPEN"
               for e in body["current_errors"])


def test_compare_baseline_invalid_422():
    resp = client.post("/api/compare", json={
        "baseline": [m("A", tempo=None), m("B")],   # 首节缺速度标记
        "current": [m("A"), m("B")],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED"
               for e in body["baseline_errors"])
    assert body["current_errors"] == []


def test_compare_both_invalid_reports_both_sides():
    resp = client.post("/api/compare", json={
        "baseline": [m("A", tempo=None), m("B")],
        "current": [m("A"), m("B", right_repeat=True)],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["baseline_errors"]
    assert body["current_errors"]


def test_compare_invalid_json_400():
    resp = client.post("/api/compare", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_compare_missing_fields_400():
    resp = client.post("/api/compare", json={"baseline": [m("A"), m("B")]})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"
