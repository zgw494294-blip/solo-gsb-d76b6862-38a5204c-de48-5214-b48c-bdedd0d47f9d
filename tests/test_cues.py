"""遍次排练提示单（app.cues.build_cue_sheet 与 /api/cues）测试。"""

from fastapi.testclient import TestClient

from app.cues import MAX_CUES, MAX_TEXT_LEN, build_cue_sheet
from app.main import app

client = TestClient(app)


def m(number, beats=4, tempo=None, left=False, right=False, times=None,
      volta=None):
    return {
        "number": str(number),
        "beats": beats,
        "tempo": tempo,
        "left_repeat": left,
        "right_repeat": right,
        "right_closes": None,
        "times": times,
        "volta": volta,
    }


def cue(measure, beat=1, text="提示", passes=None):
    c = {"measure": measure, "beat": beat, "text": text}
    if passes is not None:
        c["passes"] = passes
    return c


def rows_nested():
    """S |: A |: B C :| D :| T → S (A (B C)×2 D)×2 T（外层重入内层重新计数）。"""
    return [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", left=True, times=2),
        m("C", right=True),
        m("D", right=True),
        m("T"),
    ]


def rows_volta_tail():
    """S |: A B{1 :| C{2 T → S A B A C T（C 是主终点后的尾部房子）。"""
    return [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", right=True, volta=1),
        m("C", volta=2),
        m("T"),
    ]


def sheet_ok(measures, cues):
    result, errors = build_cue_sheet({"measures": measures, "cues": cues})
    assert errors is None, errors
    assert result is not None
    return result


def sheet_err(measures, cues):
    result, errors = build_cue_sheet({"measures": measures, "cues": cues})
    assert result is None
    assert errors is not None
    return errors


def hit_keys(result):
    return [(e["cue_index"], e["visit"]["global_index"], e["beat"], e["seconds"])
            for e in result["entries"]]


# --------------------------------------------------------------------------
# 基本命中与计时
# --------------------------------------------------------------------------

def test_no_constraint_hits_all_visits_of_measure():
    # 不限定遍次：B 的 4 次访问全部命中；120BPM 下每 4 拍 2 秒
    result = sheet_ok(rows_nested(), [cue("B", beat=2)])
    assert result["cue_count"] == 1
    assert result["hit_count"] == 4
    assert result["visit_count"] == 14
    assert result["total_seconds"] == 28.0
    assert hit_keys(result) == [
        (0, 3, 2, 4.5), (0, 5, 2, 8.5), (0, 9, 2, 16.5), (0, 11, 2, 20.5)]
    assert result["entries"][0]["passes"] == []
    assert result["entries"][0]["text"] == "提示"


def test_single_level_constraint_inner_pass_restart():
    # 内层第 1 遍：外层重入后内层重新计数 → 两个外层遍各命中一次
    result = sheet_ok(rows_nested(),
                      [cue("B", beat=1, passes=[{"open": 2, "pass": 1}])])
    assert [(e["visit"]["global_index"], e["seconds"])
            for e in result["entries"]] == [(3, 4.0), (9, 16.0)]
    paths = [e["visit"]["path"] for e in result["entries"]]
    assert paths[0] == [{"repeat_id": 1, "pass": 1, "times": 2},
                        {"repeat_id": 2, "pass": 1, "times": 2}]
    assert paths[1][0]["pass"] == 2 and paths[1][1]["pass"] == 1


def test_multi_level_constraints_intersect():
    # 外层第 2 遍 + 内层第 1 遍：只命中访问 #9
    result = sheet_ok(rows_nested(), [cue("B", beat=3, passes=[
        {"open": 1, "pass": 2}, {"open": 2, "pass": 1}])])
    assert hit_keys(result) == [(0, 9, 3, 17.0)]
    assert result["entries"][0]["passes"] == [
        {"open": 1, "pass": 2}, {"open": 2, "pass": 1}]

    # 只限定外层第 2 遍：内层任意遍都命中
    result = sheet_ok(rows_nested(),
                      [cue("B", beat=1, passes=[{"open": 1, "pass": 2}])])
    assert [e["visit"]["global_index"] for e in result["entries"]] == [9, 11]


def test_tail_house_path_participates():
    # 尾部房子 C 的访问路径带 tail：限定该反复第 2 遍可命中
    result = sheet_ok(rows_volta_tail(),
                      [cue("C", beat=1, passes=[{"open": 1, "pass": 2}])])
    assert result["hit_count"] == 1
    entry = result["entries"][0]
    assert entry["visit"]["global_index"] == 5
    assert entry["visit"]["path"] == [
        {"repeat_id": 1, "pass": 2, "times": 2, "tail": True}]


def test_tail_house_wrong_pass_no_match():
    # C 只在第 2 遍（尾部）演奏：限定第 1 遍组合无命中 → 按提示行报错
    errs = sheet_err(rows_volta_tail(),
                     [cue("C", beat=1, passes=[{"open": 1, "pass": 1}])])
    assert errs["score_errors"] == []
    assert [e["code"] for e in errs["cue_errors"]] == ["CUE_NO_MATCH"]
    assert errs["cue_errors"][0]["index"] == 0
    assert errs["cue_errors"][0]["measure"] == "C"


def test_volta_measure_other_pass_no_match():
    # B 是 {1 房子：只在第 1 遍演奏，限定第 2 遍无命中
    errs = sheet_err(rows_volta_tail(),
                     [cue("B", beat=1, passes=[{"open": 1, "pass": 2}])])
    assert [e["code"] for e in errs["cue_errors"]] == ["CUE_NO_MATCH"]


def test_seconds_accumulate_with_tempo_change():
    rows = [m("S", tempo=120), m("A", tempo=60), m("B")]
    result = sheet_ok(rows, [cue("A", beat=3), cue("B", beat=1)])
    # S 4 拍 @120 = 2s；A 第 3 拍 = 2 + 2×1.0 = 4s；B 起拍 = 2 + 4 = 6s
    assert hit_keys(result) == [(0, 2, 3, 4.0), (1, 3, 1, 6.0)]
    assert result["total_seconds"] == 10.0


def test_sorting_by_time_then_entry_order():
    # 先录入的提示时间更晚：结果按时间在前
    result = sheet_ok(rows_nested(), [cue("T", beat=1), cue("S", beat=1)])
    assert hit_keys(result) == [(1, 1, 1, 0.0), (0, 14, 1, 26.0)]
    # 同刻多条：按录入顺序
    result = sheet_ok(rows_nested(), [cue("S", beat=1), cue("S", beat=1)])
    assert hit_keys(result) == [(0, 1, 1, 0.0), (1, 1, 1, 0.0)]


def test_max_cues_32_ok():
    cues = [cue("S", beat=1, text=f"第{i + 1}条") for i in range(MAX_CUES)]
    result = sheet_ok(rows_nested(), cues)
    assert result["cue_count"] == MAX_CUES
    assert result["hit_count"] == MAX_CUES
    assert [e["cue_index"] for e in result["entries"]] == list(range(MAX_CUES))


def test_result_metadata():
    result = sheet_ok(rows_nested(), [cue("B", beat=1)])
    assert result["max_cues"] == MAX_CUES
    assert result["max_text_len"] == MAX_TEXT_LEN
    assert result["max_visits"] == 512


# --------------------------------------------------------------------------
# 提示校验
# --------------------------------------------------------------------------

def test_cues_count_and_format():
    errs = sheet_err(rows_nested(), [])
    assert errs["cue_errors"][0]["code"] == "CUES_COUNT"

    errs = sheet_err(rows_nested(),
                     [cue("S") for _ in range(MAX_CUES + 1)])
    assert errs["cue_errors"][0]["code"] == "CUES_COUNT"

    errs = sheet_err(rows_nested(), "not-a-list")
    assert errs["cue_errors"][0]["code"] == "CUES_FORMAT"

    errs = sheet_err(rows_nested(), ["not-a-dict"])
    assert errs["cue_errors"][0]["code"] == "CUE_FORMAT"
    assert errs["cue_errors"][0]["index"] == 0


def test_measure_validation():
    errs = sheet_err(rows_nested(), [cue("", beat=1)])
    assert errs["cue_errors"][0]["code"] == "CUE_MEASURE_FORMAT"

    errs = sheet_err(rows_nested(), [{"beat": 1, "text": "x"}])
    assert errs["cue_errors"][0]["code"] == "CUE_MEASURE_FORMAT"

    errs = sheet_err(rows_nested(), [cue("x" * 17, beat=1)])
    assert errs["cue_errors"][0]["code"] == "CUE_MEASURE_FORMAT"

    errs = sheet_err(rows_nested(), [cue("ZZ", beat=1)])
    assert [e["code"] for e in errs["cue_errors"]] == ["CUE_MEASURE_UNKNOWN"]
    assert errs["cue_errors"][0]["index"] == 0


def test_beat_validation():
    for bad in (0, -1, "x", 2.5, True, None):
        errs = sheet_err(rows_nested(), [cue("B", beat=bad)])
        assert errs["cue_errors"][0]["code"] == "CUE_BEAT_FORMAT", bad

    # 拍序不得超过该小节拍数
    errs = sheet_err(rows_nested(), [cue("B", beat=5)])
    assert [e["code"] for e in errs["cue_errors"]] == ["CUE_BEAT_RANGE"]
    assert errs["cue_errors"][0]["index"] == 0

    # 边界：拍序等于拍数合法
    result = sheet_ok(rows_nested(), [cue("B", beat=4)])
    assert result["hit_count"] == 4


def test_text_validation():
    errs = sheet_err(rows_nested(), [cue("S", text="")])
    assert errs["cue_errors"][0]["code"] == "CUE_TEXT"

    errs = sheet_err(rows_nested(), [cue("S", text="   ")])
    assert errs["cue_errors"][0]["code"] == "CUE_TEXT"

    errs = sheet_err(rows_nested(), [cue("S", text="x" * 81)])
    assert errs["cue_errors"][0]["code"] == "CUE_TEXT"

    result = sheet_ok(rows_nested(), [cue("S", text="x" * 80)])
    assert result["entries"][0]["text"] == "x" * 80


def test_pass_shape_validation():
    errs = sheet_err(rows_nested(), [cue("B", passes="not-a-list")])
    assert errs["cue_errors"][0]["code"] == "CUE_PASS_FORMAT"

    errs = sheet_err(rows_nested(), [cue("B", passes=["x"])])
    assert errs["cue_errors"][0]["code"] == "CUE_PASS_FORMAT"

    errs = sheet_err(rows_nested(), [cue("B", passes=[{"open": -1, "pass": 1}])])
    assert errs["cue_errors"][0]["code"] == "CUE_PASS_FORMAT"

    errs = sheet_err(rows_nested(), [cue("B", passes=[{"open": 1, "pass": 0}])])
    assert errs["cue_errors"][0]["code"] == "CUE_PASS_FORMAT"

    errs = sheet_err(rows_nested(), [cue("B", passes=[
        {"open": 1, "pass": 1}, {"open": 1, "pass": 2}])])
    assert errs["cue_errors"][0]["code"] == "CUE_PASS_DUPLICATE"


def test_pass_semantic_validation():
    # open 不是反复起点行
    errs = sheet_err(rows_nested(),
                     [cue("B", passes=[{"open": 0, "pass": 1}])])
    assert [e["code"] for e in errs["cue_errors"]] == ["CUE_PASS_OPEN"]

    # 遍次超出该层次数
    errs = sheet_err(rows_nested(),
                     [cue("B", passes=[{"open": 1, "pass": 3}])])
    assert [e["code"] for e in errs["cue_errors"]] == ["CUE_PASS_RANGE"]

    # 所选反复未出现在该小节的任何访问路径中
    errs = sheet_err(rows_nested(),
                     [cue("T", passes=[{"open": 1, "pass": 1}])])
    assert [e["code"] for e in errs["cue_errors"]] == ["CUE_PASS_REPEAT"]
    assert errs["cue_errors"][0]["measure"] == "T"


def test_multiple_cue_errors_reported_together():
    # 形状错误与语义错误并存：全部按提示行汇总，按行号排序
    errs = sheet_err(rows_volta_tail(), [
        cue("B", beat=9),                          # 第 1 条：拍序越界
        cue("C", passes=[{"open": 1, "pass": 1}]),  # 第 2 条：组合无命中
        {"measure": "S", "beat": 1, "text": ""},   # 第 3 条：提示语为空
    ])
    codes = [(e["index"], e["code"]) for e in errs["cue_errors"]]
    assert codes == [(0, "CUE_BEAT_RANGE"), (1, "CUE_NO_MATCH"),
                     (2, "CUE_TEXT")]


def test_score_errors_reported_separately():
    bad = [m("S", tempo=120), m("A", left=True, times=2)]  # 左起点未闭合
    errs = sheet_err(bad, [cue("A", beat=1)])
    assert errs["score_errors"]
    assert errs["cue_errors"] == []


def test_measures_not_list():
    errs = sheet_err("not-a-list", [cue("A")])
    assert errs["score_errors"][0]["code"] == "INVALID_REQUEST"


# --------------------------------------------------------------------------
# HTTP /api/cues
# --------------------------------------------------------------------------

def test_api_cues_ok():
    resp = client.post("/api/cues", json={
        "measures": rows_nested(),
        "cues": [cue("B", beat=2, passes=[{"open": 2, "pass": 1}])],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    assert result["hit_count"] == 2
    assert [(e["visit"]["global_index"], e["seconds"])
            for e in result["entries"]] == [(3, 4.5), (9, 16.5)]


def test_api_cues_422_groups():
    resp = client.post("/api/cues", json={
        "measures": [m("S", tempo=120), m("A", left=True, times=2)],
        "cues": [cue("A", beat=9)],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["score_errors"]
    # 谱面非法时不做语义检查，拍序越界（语义）不出现
    assert body["cue_errors"] == []


def test_api_cues_422_cue_errors():
    resp = client.post("/api/cues", json={
        "measures": rows_nested(),
        "cues": [cue("B", beat=9)],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["score_errors"] == []
    assert body["cue_errors"][0]["code"] == "CUE_BEAT_RANGE"
    assert body["cue_errors"][0]["index"] == 0


def test_api_cues_invalid_json_400():
    resp = client.post("/api/cues", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_api_cues_missing_fields_400():
    resp = client.post("/api/cues", json={"measures": rows_nested()})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"
