"""反复次数反求（app.solve.solve 与 /api/solve）测试。"""

from fastapi.testclient import TestClient

from app.expander import expand
from app.main import app
from app.solve import solve

client = TestClient(app)


def m(number, beats=4, tempo=None, left=False, right=False, times=None,
      volta=None):
    return {
        "number": str(number),
        "beats": beats,
        "tempo": tempo,
        "left_repeat": left,
        "right_repeat": right,
        "times": times,
        "volta": volta,
    }


def expansion_of(rows):
    result, errors = expand(rows)
    assert errors == [], errors
    return [v["source_measure"] for v in result["visits"]]


def solve_ok(measures, unknowns, target):
    result, errors = solve({
        "measures": measures, "unknowns": unknowns, "target": target,
    })
    assert errors is None, errors
    assert result is not None
    return result


def solve_err(measures, unknowns, target):
    result, errors = solve({
        "measures": measures, "unknowns": unknowns, "target": target,
    })
    assert result is None
    assert errors is not None
    return errors


# --------------------------------------------------------------------------
# expand 的 expect 剪枝（与完整展开后比对结论一致）
# --------------------------------------------------------------------------

def rows_simple():
    return [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", right=True),
        m("T"),
    ]


def test_expect_match():
    result, errors = expand(rows_simple(), expect=["S", "A", "B", "A", "B", "T"])
    assert errors == []
    assert result["visit_count"] == 6


def test_expect_mismatch_midway():
    result, errors = expand(rows_simple(), expect=["S", "A", "X"])
    assert result is None
    assert errors[0]["code"] == "EXPECT_MISMATCH"


def test_expect_too_short():
    # 目标只是展开序列的前缀：访问数超出目标长度即中止
    result, errors = expand(rows_simple(), expect=["S", "A", "B"])
    assert result is None
    assert errors[0]["code"] == "EXPECT_MISMATCH"


def test_expect_too_long():
    # 展开结束仍不足目标长度
    result, errors = expand(rows_simple(),
                            expect=["S", "A", "B", "A", "B", "T", "T"])
    assert result is None
    assert errors[0]["code"] == "EXPECT_MISMATCH"


# --------------------------------------------------------------------------
# 基本反求
# --------------------------------------------------------------------------

def test_solve_single_unknown():
    rows = rows_simple()
    target = ["S", "A", "B", "A", "B", "A", "B", "T"]  # times=3 的展开
    result = solve_ok(rows, [1], target)
    assert result["unknowns"] == [{"index": 1, "measure": "A"}]
    assert result["target_count"] == 8
    assert result["combination_count"] == 3
    assert result["solution_count"] == 1
    assert result["solutions"] == [{"times": [3]}]


def test_solve_two_unknowns_siblings():
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B", right=True),
        m("C", left=True, times=2), m("D", right=True),
        m("T"),
    ]
    # (A=4, C=2) 的展开
    target = (["S"] + ["A", "B"] * 4 + ["C", "D"] * 2 + ["T"])
    result = solve_ok(rows, [1, 3], target)
    assert result["combination_count"] == 9
    assert result["solution_count"] == 1
    assert result["solutions"] == [{"times": [4, 2]}]
    assert [u["measure"] for u in result["unknowns"]] == ["A", "C"]


def test_solve_no_solution_reported():
    rows = rows_simple()
    # 任何次数组合都展开不出的序列
    result = solve_ok(rows, [1], ["S", "A", "B", "A", "T"])
    assert result["solution_count"] == 0
    assert result["solutions"] == []


def test_solve_other_times_kept_as_entered():
    """其余次数照录入值：非待求反复的次数不被改动。"""
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B", right=True),
        m("C", left=True, times=3), m("D", right=True),  # 固定 3 遍
        m("T"),
    ]
    target = ["S"] + ["A", "B"] * 2 + ["C", "D"] * 3 + ["T"]
    result = solve_ok(rows, [1], target)
    assert result["solutions"] == [{"times": [2]}]
    # 若 C 的次数被当成可求，(2,?) 之外的目标不可能匹配；这里再验证
    # 目标要求 C=4 时无解（C 固定为录入的 3）。
    target4 = ["S"] + ["A", "B"] * 2 + ["C", "D"] * 4 + ["T"]
    result = solve_ok(rows, [1], target4)
    assert result["solution_count"] == 0


def test_solve_unknowns_sorted_by_row_order():
    """待求位置按录入行顺序组成次数元组（与传入顺序无关）。"""
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B", right=True),
        m("C", left=True, times=2), m("D", right=True),
        m("T"),
    ]
    target = ["S"] + ["A", "B"] * 3 + ["C", "D"] * 2 + ["T"]
    result = solve_ok(rows, [3, 1], target)  # 乱序传入
    assert [u["index"] for u in result["unknowns"]] == [1, 3]
    assert result["solutions"] == [{"times": [3, 2]}]


# --------------------------------------------------------------------------
# 组合级约束：每个组合都须通过现有校验与展开
# --------------------------------------------------------------------------

def test_combo_over_512_not_counted():
    """超过 512 次访问的组合不计入，但不影响其余组合求解。"""
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", left=True, times=2),
        m("C", left=True, times=2),
        m("D"), m("E"), m("F"), m("G"), m("H"), m("I"),
        m("J", right=True),
        m("K", right=True),
        m("L", right=True),
    ]
    # 全 2 时 77 次访问（合法）；含 4 的嵌套组合远超 512。
    target = expansion_of(rows)
    assert len(target) == 77
    result = solve_ok(rows, [1, 2, 3], target)
    assert result["combination_count"] == 27
    assert result["solution_count"] == 1
    assert result["solutions"] == [{"times": [2, 2, 2]}]


def test_combo_failing_validation_not_counted():
    """组合未通过现有校验（缺房子）时不计入；只剩合法组合参与匹配。"""
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=2, right=True),
        m("D", volta=3),
        m("T"),
    ]
    # times=2 / 4 的组合缺 {3 / {4 房子，校验失败被跳过；只有 times=3 合法。
    target = expansion_of(rows)
    assert target == ["S", "A", "B", "A", "C", "A", "D", "T"]
    result = solve_ok(rows, [1], target)
    assert result["solution_count"] == 1
    assert result["solutions"] == [{"times": [3]}]


def test_solve_eight_unknowns_combination_space():
    """8 个待求位置：组合空间 3^8，求解有界且结果正确。"""
    rows = [m("S", tempo=120)]
    unknowns = []
    for k in range(8):
        unknowns.append(len(rows))
        rows.append(m(f"R{k}", left=True, times=2))
        rows.append(m(f"b{k}"))
        rows.append(m(f"c{k}", right=True))
    target = expansion_of(rows)  # 全 2
    result = solve_ok(rows, unknowns, target)
    assert result["combination_count"] == 3 ** 8
    assert result["solution_count"] == 1
    assert result["solutions"] == [{"times": [2] * 8}]


# --------------------------------------------------------------------------
# 录入校验：谱面 / 待求位置 / 目标序列
# --------------------------------------------------------------------------

def test_score_errors_reported_first():
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2),   # 无配对右终点
        m("B"),
    ]
    errors = solve_err(rows, [1], ["S", "A", "B"])
    assert any(e["code"] == "REPEAT_UNMATCHED_OPEN"
               for e in errors["score_errors"])
    assert errors["unknown_errors"] == []
    assert errors["target_errors"] == []


def test_score_tempo_rules_still_apply():
    rows = [m("S"), m("A", left=True, times=2), m("B", right=True)]
    errors = solve_err(rows, [1], ["S", "A", "B", "A", "B"])
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED"
               for e in errors["score_errors"])


def test_unknown_count_limits():
    rows = rows_simple()
    errors = solve_err(rows, [], ["S"])
    assert errors["unknown_errors"][0]["code"] == "UNKNOWN_COUNT"

    many = [m("S", tempo=120)]
    for k in range(9):
        many.append(m(f"r{k}", left=True, times=2))
        many.append(m(f"c{k}", right=True))
    errors = solve_err(many, [1, 3, 5, 7, 9, 11, 13, 15, 17], ["S"])
    assert errors["unknown_errors"][0]["code"] == "UNKNOWN_COUNT"


def test_unknown_must_have_left_repeat():
    rows = rows_simple()
    errors = solve_err(rows, [0], ["S"])
    assert errors["unknown_errors"][0]["code"] == "UNKNOWN_NOT_LEFT_REPEAT"
    assert errors["unknown_errors"][0]["index"] == 0


def test_unknown_range_and_duplicate():
    rows = rows_simple()
    errors = solve_err(rows, [1, 99], ["S"])
    assert any(e["code"] == "UNKNOWN_RANGE" for e in errors["unknown_errors"])

    errors = solve_err(rows, [1, 1], ["S"])
    assert any(e["code"] == "UNKNOWN_DUPLICATE" for e in errors["unknown_errors"])


def test_target_empty_and_too_many():
    rows = rows_simple()
    errors = solve_err(rows, [1], [])
    assert errors["target_errors"][0]["code"] == "TARGET_EMPTY"

    errors = solve_err(rows, [1], ["S"] * 513)
    assert errors["target_errors"][0]["code"] == "TARGET_TOO_MANY"


def test_target_unknown_measure():
    rows = rows_simple()
    errors = solve_err(rows, [1], ["S", "ZZZ"])
    errs = errors["target_errors"]
    assert errs[0]["code"] == "TARGET_UNKNOWN_MEASURE"
    assert errs[0]["index"] == 1
    assert errs[0]["measure"] == "ZZZ"


def test_target_item_format():
    rows = rows_simple()
    errors = solve_err(rows, [1], ["S", "  "])
    assert errors["target_errors"][0]["code"] == "TARGET_ITEM_FORMAT"

    errors = solve_err(rows, [1], ["S", "x" * 17])
    assert errors["target_errors"][0]["code"] == "TARGET_ITEM_FORMAT"


def test_all_error_groups_returned_together():
    rows = [m("S"), m("A", left=True, times=2), m("B", right=True)]
    errors = solve_err(rows, [0], ["S", "NOPE"])
    assert errors["score_errors"]      # 首节缺速度
    assert errors["unknown_errors"]    # 第 1 行无左反复
    assert errors["target_errors"]     # NOPE 不是小节编号


# --------------------------------------------------------------------------
# /api/solve 路由
# --------------------------------------------------------------------------

def api_rows():
    return [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", right=True),
        m("T"),
    ]


def test_api_solve_ok():
    resp = client.post("/api/solve", json={
        "measures": api_rows(),
        "unknowns": [1],
        "target": ["S", "A", "B", "A", "B", "A", "B", "T"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    assert result["solution_count"] == 1
    assert result["solutions"] == [{"times": [3]}]
    assert result["unknowns"] == [{"index": 1, "measure": "A"}]
    assert result["target_count"] == 8
    assert result["combination_count"] == 3


def test_api_solve_no_solution_ok():
    resp = client.post("/api/solve", json={
        "measures": api_rows(),
        "unknowns": [1],
        "target": ["S", "A", "A", "T"],
    })
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["solution_count"] == 0
    assert result["solutions"] == []


def test_api_solve_422_groups():
    resp = client.post("/api/solve", json={
        "measures": [m("S"), m("A", left=True, times=2), m("B", right=True)],
        "unknowns": [0],
        "target": ["S", "NOPE"],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["score_errors"]
    assert body["unknown_errors"]
    assert body["target_errors"]


def test_api_solve_invalid_json_400():
    resp = client.post("/api/solve", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_api_solve_missing_fields_400():
    resp = client.post("/api/solve", json={"measures": api_rows()})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"


def test_api_solve_solutions_capped_and_sorted():
    """方案列表按次数元组字典序、最多 10 个（本模型下解唯一，仍验证契约）。"""
    resp = client.post("/api/solve", json={
        "measures": api_rows(),
        "unknowns": [1],
        "target": ["S", "A", "B", "A", "B", "T"],
    })
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert len(result["solutions"]) <= 10
    times_list = [s["times"] for s in result["solutions"]]
    assert times_list == sorted(times_list)
    assert result["solutions"] == [{"times": [2]}]
