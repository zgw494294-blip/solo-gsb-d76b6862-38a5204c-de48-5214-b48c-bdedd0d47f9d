"""排练记录定位（app.locate.locate_records 与 /api/locate）测试。"""

from fastapi.testclient import TestClient

from app.locate import (
    OP_DELETE,
    OP_INSERT,
    OP_MATCH,
    OP_SUBSTITUTE,
    locate_records,
)
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


def rows_repeat2():
    """S |: A B C :| T → S A B C A B C T（两次遍次各自计数）。"""
    return [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B"),
        m("C", right=True),
        m("T"),
    ]


def locate_ok(measures, record, tolerance):
    result, errors = locate_records({
        "measures": measures, "record": record, "tolerance": tolerance,
    })
    assert errors is None, errors
    assert result is not None
    return result


def locate_err(measures, record, tolerance):
    result, errors = locate_records({
        "measures": measures, "record": record, "tolerance": tolerance,
    })
    assert result is None
    assert errors is not None
    return errors


def ops_of(match):
    return [s["op"] for s in match["alignment"]]


# --------------------------------------------------------------------------
# 基本命中：不同遍次的同编号区间分别计数
# --------------------------------------------------------------------------

def test_exact_matches_each_pass_counted():
    result = locate_ok(rows_repeat2(), "A,B,C", 0)
    assert result["record_count"] == 3
    assert result["match_count"] == 2
    assert [(x["distance"], x["start"], x["end"]) for x in result["matches"]] == [
        (0, 2, 4), (0, 5, 7)]
    first = result["matches"][0]
    assert first["interval_length"] == 3
    assert first["start_path"] == [{"repeat_id": 1, "pass": 1, "times": 2}]
    assert first["end_path"] == [{"repeat_id": 1, "pass": 1, "times": 2}]
    second = result["matches"][1]
    assert second["start_path"][0]["pass"] == 2
    assert ops_of(first) == [OP_MATCH] * 3


def test_visit_refs_are_one_based_global():
    result = locate_ok(rows_repeat2(), "A,B,C", 0)
    mt = result["matches"][0]
    assert mt["start_visit"]["global_index"] == 2
    assert mt["end_visit"]["global_index"] == 4
    assert mt["alignment"][0]["visit"]["source_measure"] == "A"


def test_outside_score_numbers_allowed():
    # 谱面外的误记编号不报错，按替换参与匹配
    result = locate_ok(rows_repeat2(), "A,X,C", 1)
    assert result["match_count"] == 2
    steps = result["matches"][0]["alignment"]
    assert [(s["op"], s["record_item"], s["visit"]["source_measure"])
            for s in steps] == [
        (OP_MATCH, "A", "A"),
        (OP_SUBSTITUTE, "X", "B"),
        (OP_MATCH, "C", "C")]


def test_tolerance_zero_rejects_any_error():
    result = locate_ok(rows_repeat2(), "A,X,C", 0)
    assert result["match_count"] == 0
    assert result["matches"] == []
    assert result["shown_count"] == 0


def test_delete_extra_record_item():
    # 记录多写 D：删除计 1，区间仍是 A B C
    result = locate_ok(rows_repeat2(), "A,B,D,C", 1)
    hit = [x for x in result["matches"]
           if (x["start"], x["end"]) == (2, 4)][0]
    assert hit["distance"] == 1
    assert ops_of(hit) == [OP_MATCH, OP_MATCH,
                           OP_DELETE, OP_MATCH]
    deleted = hit["alignment"][2]
    assert deleted["record_item"] == "D"
    assert deleted["record_pos"] == 3
    assert deleted["visit"] is None


def test_insert_missing_visit():
    # 漏记 B：补入计 1
    result = locate_ok(rows_repeat2(), "A,C", 1)
    hit = [x for x in result["matches"]
           if (x["start"], x["end"]) == (2, 4)][0]
    assert hit["distance"] == 1
    assert ops_of(hit) == [OP_MATCH, OP_INSERT,
                           OP_MATCH]
    inserted = hit["alignment"][1]
    assert inserted["record_item"] is None
    assert inserted["record_pos"] is None
    assert inserted["visit"]["source_measure"] == "B"


def test_interval_outside_not_counted():
    # 记录 A 与区间 S A（起点 S、终点 A）只需补入 1（S 在 A 之前属区间内）；
    # 而与区间 A B 的距离 1（B 在 A 之后）。A 之外更远的访问不额外计费。
    result = locate_ok(rows_repeat2(), "A", 1)
    ranges = [(x["distance"], x["start"], x["end"]) for x in result["matches"]]
    assert (0, 2, 2) in ranges          # 精确单访问区间
    assert (1, 1, 2) in ranges          # 区间 S A：补入 S
    assert (1, 2, 3) in ranges          # 区间 A B：补入 B
    # 距离 1 下不会包含长度 3 的区间（那要补入 2 次）
    assert all(not (e - s) > 2 for _, s, e in ranges)


def test_sorting_distance_start_end_and_top10():
    result = locate_ok(rows_repeat2(), "B", 2)
    keys = [(x["distance"], x["start"], x["end"]) for x in result["matches"]]
    assert keys == sorted(keys)
    assert result["shown_count"] == min(10, result["match_count"])


def test_volta_tail_path_in_alignment():
    # |: A B C{1 :| D{2 T → A B C A B D T：尾部房子 D 的遍次路径带 tail
    rows = [
        m("A", tempo=120, left=True, times=2), m("B"),
        m("C", right=True, volta=1), m("D", volta=2), m("T"),
    ]
    result = locate_ok(rows, "B,D,T", 0)
    assert result["match_count"] == 1
    mt = result["matches"][0]
    assert (mt["start"], mt["end"]) == (5, 7)
    d_step = [s for s in mt["alignment"]
              if s["op"] == OP_MATCH and s["record_item"] == "D"][0]
    assert d_step["visit"]["path"] == [
        {"repeat_id": 1, "pass": 2, "times": 2, "tail": True}]


def test_empty_result_has_zero_matches():
    result = locate_ok(rows_repeat2(), "Z,Q,W", 0)
    assert result["match_count"] == 0
    assert result["matches"] == []


# --------------------------------------------------------------------------
# 回溯并列：匹配 → 替换 → 删除 → 补入
# --------------------------------------------------------------------------

def test_traceback_prefers_match_over_substitute():
    # "A" 对区间 "A"：只能匹配（末端相同），回溯选 match 而非“补入+删除”
    result = locate_ok(rows_repeat2(), "A", 1)
    exact = [x for x in result["matches"]
             if (x["start"], x["end"]) == (2, 2)][0]
    assert ops_of(exact) == [OP_MATCH]


def test_traceback_prefers_substitute_over_delete_insert():
    # 末端编号不同且替换/删补并列时，回溯选替换
    result = locate_ok(rows_repeat2(), "X", 2)
    # 区间 A（#2）：距离 1 = 替换
    cand = [x for x in result["matches"]
            if (x["start"], x["end"]) == (2, 2)]
    assert cand and cand[0]["distance"] == 1
    assert ops_of(cand[0]) == [OP_SUBSTITUTE]


def test_traceback_prefers_delete_before_insert():
    # "A" 对齐到区间 "B A"（#3 B, #2? 不相邻）——改用相邻构造：
    # 区间 S A（#1,#2），记录 "A"：s=1,e=2 距离 1（补入 S），无删补并列；
    # 这里验证记录 "S,A" 与区间 S（#1）：删除 A 计 1（删除优先于
    # “补入+…”之外的并列），区间 #1 单格距离 1。
    result = locate_ok(rows_repeat2(), "S,A", 1)
    cand = [x for x in result["matches"]
            if (x["start"], x["end"]) == (1, 1)]
    assert cand and cand[0]["distance"] == 1
    assert ops_of(cand[0]) == [OP_MATCH, OP_DELETE]


# --------------------------------------------------------------------------
# 输入校验
# --------------------------------------------------------------------------

def test_record_separators_chinese_comma_newline():
    result = locate_ok(rows_repeat2(), " A，B、C\n", 0)
    assert result["record"] == ["A", "B", "C"]
    assert result["match_count"] == 2


def test_record_empty_too_many_and_long_item():
    errs = locate_err(rows_repeat2(), "", 0)
    assert errs["record_errors"][0]["code"] == "LOCATE_RECORD_COUNT"

    errs = locate_err(rows_repeat2(), ",".join(str(i) for i in range(65)), 0)
    assert errs["record_errors"][0]["code"] == "LOCATE_RECORD_COUNT"

    errs = locate_err(rows_repeat2(), ["A"], 0)  # 非字符串
    assert errs["record_errors"][0]["code"] == "LOCATE_RECORD_FORMAT"

    errs = locate_err(rows_repeat2(), "x" * 17, 0)
    assert errs["record_errors"][0]["code"] == "LOCATE_RECORD_ITEM_FORMAT"


def test_tolerance_validation():
    errs = locate_err(rows_repeat2(), "A", -1)
    assert errs["tolerance_errors"][0]["code"] == "LOCATE_TOLERANCE_FORMAT"
    errs = locate_err(rows_repeat2(), "A", 5)
    assert errs["tolerance_errors"][0]["code"] == "LOCATE_TOLERANCE_FORMAT"
    errs = locate_err(rows_repeat2(), "A", "1")
    assert errs["tolerance_errors"][0]["code"] == "LOCATE_TOLERANCE_FORMAT"
    errs = locate_err(rows_repeat2(), "A", True)  # bool 拒绝
    assert errs["tolerance_errors"]


def test_score_errors_reported_separately():
    bad = [m("S", tempo=120), m("A", left=True, times=2)]  # 左起点未闭合
    errs = locate_err(bad, "A", 0)
    assert errs["score_errors"]
    assert errs["record_errors"] == []
    assert errs["tolerance_errors"] == []


# --------------------------------------------------------------------------
# DP 状态复用：单张表一次扫描，不随区间数重算（规模 / 性能回归）
# --------------------------------------------------------------------------

def test_single_table_handles_long_tape_and_many_intervals():
    # 4 层嵌套 ×3 遍 = 162 次访问；记录 64 项、容错 4：区间数很多，
    # 一次建表 + 枚举即可完成，命中数与“直接对每段算编辑距离”一致。
    rows = [
        m("S", tempo=100),
        m("A", left=True, times=3),
        m("B", left=True, times=3),
        m("C", left=True, times=3),
        m("D", left=True, times=3),
        m("E"),
        m("F", right=True),
        m("G", right=True),
        m("H", right=True),
        m("I", right=True),
        m("T"),
    ]
    from app.expander import expand
    expanded, errs = expand(rows)
    assert errs == []
    text = [v["source_measure"] for v in expanded["visits"]]

    record = ",".join(text[10:74])  # 64 项真实片段
    result = locate_ok(rows, record, 4)

    # 用标准 DP 逐区间核对距离（区间外免费开始 / 结束的半全局对齐）
    def semi_distance(p, t_segment):
        dp = list(range(len(t_segment) + 1))
        for a in p:
            prev = dp
            dp = [prev[0] + 1]
            for j, b in enumerate(t_segment, start=1):
                cost = 0 if a == b else 1
                dp.append(min(prev[j] + 1, dp[j - 1] + 1,
                              prev[j - 1] + cost))
        return dp[-1]

    expected = []
    for s in range(len(text)):
        # 只在容差有希望的长度窗口内枚举（长度差 ≤ 4）
        for e in range(s + 1, min(len(text), s + 64 + 4) + 1):
            d = semi_distance(record.split(","), text[s:e])
            if d <= 4:
                expected.append((d, s + 1, e))  # 1 基全局序号
    expected.sort()
    got = [(x["distance"], x["start"], x["end"]) for x in result["matches"]]
    assert result["match_count"] == len(expected)
    assert got == expected[:10]


# --------------------------------------------------------------------------
# HTTP /api/locate
# --------------------------------------------------------------------------

def test_api_locate_ok():
    resp = client.post("/api/locate", json={
        "measures": rows_repeat2(),
        "record": "A,B,C",
        "tolerance": 0,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    assert result["match_count"] == 2
    assert result["tolerance"] == 0
    assert result["matches"][0]["start"] == 2


def test_api_locate_422_groups():
    resp = client.post("/api/locate", json={
        "measures": [m("S", tempo=120), m("A", left=True, times=2)],
        "record": "",
        "tolerance": 9,
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["score_errors"]
    assert any(e["code"] == "LOCATE_RECORD_COUNT" for e in body["record_errors"])
    assert any(e["code"] == "LOCATE_TOLERANCE_FORMAT"
               for e in body["tolerance_errors"])


def test_api_locate_invalid_json_400():
    resp = client.post("/api/locate", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_api_locate_missing_fields_400():
    resp = client.post("/api/locate", json={
        "measures": rows_repeat2(), "record": "A"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"
