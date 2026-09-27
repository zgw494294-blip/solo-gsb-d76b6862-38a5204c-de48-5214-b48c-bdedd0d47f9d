"""右反复终点纠错（app.rightfix.fix_right_repeats 与 /api/fix-right）测试。"""

from fastapi.testclient import TestClient

from app.expander import expand
from app.main import app
from app.rightfix import MAX_SOLUTIONS, MAX_TOGGLES, fix_right_repeats

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


def fix_ok(measures):
    result, errors = fix_right_repeats({"measures": measures})
    assert errors is None, errors
    assert result is not None
    return result


def fix_score_errors(measures):
    result, errors = fix_right_repeats({"measures": measures})
    assert result is None
    assert errors is not None
    return errors["score_errors"]


def change_sig(sol):
    return [(c["index"], c["action"]) for c in sol["changes"]]


# --------------------------------------------------------------------------
# 原谱合法：无需纠错
# --------------------------------------------------------------------------

def test_already_valid():
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B", right=True),
        m("T"),
    ]
    result = fix_ok(rows)
    assert result["already_valid"] is True
    assert result["visit_count"] == 6
    assert result["solutions"] == []
    assert result["max_toggles"] == MAX_TOGGLES


def test_already_valid_nested_volta():
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=2, right=True),
        m("D", volta=3),
        m("T"),
    ]
    result = fix_ok(rows)
    assert result["already_valid"] is True


# --------------------------------------------------------------------------
# 行级校验复用：行级错误直接回报，不进入搜索
# --------------------------------------------------------------------------

def test_row_errors_short_circuit():
    rows = [m("S"), m("A", left=True, times=2), m("B")]  # 首节缺速度
    errors = fix_score_errors(rows)
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED" for e in errors)


def test_duplicate_number_rejected():
    rows = [
        m("X", tempo=120),
        m("X", left=True, times=2),
        m("B", right=True),
    ]
    errors = fix_score_errors(rows)
    assert any(e["code"] == "NUMBER_DUPLICATE" for e in errors)


def test_measures_not_array():
    result, errors = fix_right_repeats({"measures": "nope"})
    assert result is None
    assert errors["score_errors"][0]["code"] == "INVALID_REQUEST"


# --------------------------------------------------------------------------
# 最少切换数：k 从小到大
# --------------------------------------------------------------------------

def test_missing_close_single_add():
    # 唯一未闭合起点；在 A 自闭合或 B/T 加右终点均可
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B"), m("T"),
    ]
    result = fix_ok(rows)
    assert result["already_valid"] is False
    assert result["minimal_toggles"] == 1
    assert result["solution_count"] == 3
    sigs = [change_sig(s) for s in result["solutions"]]
    # 同切换数按改动行号序列字典序
    assert sigs == [[(1, "add")], [(2, "add")], [(3, "add")]]
    for sol in result["solutions"]:
        assert sol["toggles"] == 1
        assert sol["visit_count"] <= 512


def test_extra_close_single_remove():
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", left=True, times=2),
        m("r1", right=True), m("r2", right=True), m("r3", right=True),
    ]
    result = fix_ok(rows)
    assert result["minimal_toggles"] == 1
    assert change_sig(result["solutions"][0]) == [(3, "remove")]
    assert change_sig(result["solutions"][1]) == [(4, "remove")]
    assert change_sig(result["solutions"][2]) == [(5, "remove")]


def test_solutions_cap_five_lexicographic():
    # 30 个平级反复后最后一对缺右终点：可加在第 62/63/64 行，共 3 个 k=1 方案
    rows = [m("S", tempo=120)]
    for k in range(30):
        rows += [m(f"L{k}", left=True, times=2), m(f"p{k}", right=True)]
    rows += [m("Lx", left=True, times=2), m("px"), m("T")]
    result = fix_ok(rows)
    assert result["minimal_toggles"] == 1
    assert result["solution_count"] == 3
    assert [[c["index"] for c in s["changes"]] for s in result["solutions"]] == [
        [61], [62], [63]]


def test_lexicographic_first_five_at_four_toggles():
    # 4 个未闭合起点 + 10 个普通行：必须恰好切换 4 处
    rows = [m("S", tempo=120)]
    rows += [m(f"L{k}", left=True, times=2) for k in range(4)]
    rows += [m(f"p{k}") for k in range(10)]
    result = fix_ok(rows)
    assert result["minimal_toggles"] == 4
    assert result["solution_count"] == MAX_SOLUTIONS == 5
    # 最小组合不是 (1,2,3,4)：L0 自闭合后 L1..L3 仍无处闭合
    first = [c["index"] for c in result["solutions"][0]["changes"]]
    assert first == [1, 2, 3, 4]
    seqs = [[c["index"] for c in s["changes"]] for s in result["solutions"]]
    assert seqs == sorted(seqs)


def test_no_solution_within_budget():
    # 6 个未闭合起点，最多切换 4 处：无解
    rows = [m("S", tempo=120)]
    rows += [m(f"L{k}", left=True, times=2) for k in range(6)]
    rows += [m("tail"), m("end")]
    result = fix_ok(rows)
    assert result["already_valid"] is False
    assert result["minimal_toggles"] == 0
    assert result["solutions"] == []
    assert result["solution_count"] == 0


# --------------------------------------------------------------------------
# 替代结尾：只切换右终点即可补齐的情形
# --------------------------------------------------------------------------

def test_volta_missing_secondary_close():
    # |: A B{1:| C{2 D{3 （times=3）：C 之后缺次级终点
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=2),
        m("D", volta=3),
        m("T"),
    ]
    result = fix_ok(rows)
    assert result["minimal_toggles"] == 1
    assert change_sig(result["solutions"][0]) == [(3, "add")]
    assert result["solutions"][0]["visit_count"] == 8


def test_volta_multi_measure_house_secondary_close():
    # {2 房子跨两小节 C/D，次级终点应加在 D 上而非 C 上
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=2),
        m("D", volta=2),
        m("E", volta=3),
        m("T"),
    ]
    result = fix_ok(rows)
    assert result["minimal_toggles"] == 1
    assert change_sig(result["solutions"][0]) == [(4, "add")]


def test_volta_pass_out_of_range_not_fixable():
    # {4 房子但 times=2：切右终点无法改变次数，无解
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2),
        m("B", volta=4, right=True),
        m("T"),
    ]
    result = fix_ok(rows)
    assert result["solutions"] == []
    assert result["minimal_toggles"] == 0


# --------------------------------------------------------------------------
# 512 上限：超过的候选不计入
# --------------------------------------------------------------------------

def test_candidates_over_512_excluded():
    # 5 层 times=2 嵌套（156 次访问）删除最外层终点：补回终点的各种方案
    # 访问数不同，但全部方案都必须 <= 512 才计入。
    rows = [m("L0", tempo=120, left=True, times=2)]
    rows += [m(f"L{k}", left=True, times=2) for k in range(1, 5)]
    rows.append(m("body"))
    rows += [m(f"c{k}", right=True) for k in range(5)]
    assert expand(rows)[1] == []
    rows[-1]["right_repeat"] = False
    result = fix_ok(rows)
    assert result["minimal_toggles"] == 1
    assert result["solution_count"] == 5
    for sol in result["solutions"]:
        assert sol["visit_count"] <= 512
    # 把外层终点前移到起点行会形成单小节反复，字典序在前
    assert [c["index"] for c in result["solutions"][0]["changes"]] == [0]

def test_deep_nesting_all_candidates_exceed_512():
    # 31 层嵌套 + 32 个右终点：多出一个；任何 ≤4 切换的合法结构都是深层
    # 嵌套，访问数远超 512，必须无解且不得跑大量 expand
    rows = [m("S", tempo=120)]
    rows += [m(f"L{k}", left=True, times=2) for k in range(31)]
    rows += [m(f"R{k}", right=True) for k in range(32)]
    result = fix_ok(rows)
    assert result["solutions"] == []
    # 访问数剪枝使绝大多数候选在 DFS 内被拒，几乎不跑完整 expand
    assert result["expanded_candidates"] == 0


# --------------------------------------------------------------------------
# 只改右标记：写回后其它字段保持（通过方案内容隐含；这里验证 changes 语义）
# --------------------------------------------------------------------------

def test_changes_describe_add_and_remove():
    rows = [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B", right=True),
        m("C", right=True), m("T"),
    ]
    result = fix_ok(rows)
    assert result["minimal_toggles"] == 1
    # 删除 B:|（让 C:| 配 A）或删除多余的 C:| 都合法，全是“删除”动作
    actions = {c["action"] for s in result["solutions"] for c in s["changes"]}
    assert actions == {"remove"}
    indices = {c["index"] for s in result["solutions"] for c in s["changes"]}
    assert indices == {2, 3}


# --------------------------------------------------------------------------
# /api/fix-right 路由
# --------------------------------------------------------------------------

def test_api_already_valid():
    resp = client.post("/api/fix-right", json={"measures": [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B", right=True),
    ]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["already_valid"] is True


def test_api_solutions():
    resp = client.post("/api/fix-right", json={"measures": [
        m("S", tempo=120),
        m("A", left=True, times=2), m("B"), m("T"),
    ]})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["already_valid"] is False
    assert result["minimal_toggles"] == 1
    assert result["solution_count"] == 3
    first = result["solutions"][0]
    assert first["changes"] == [
        {"index": 1, "measure": "A", "action": "add"}]
    assert "visit_count" in first and first["visit_count"] <= 512


def test_api_score_errors_422():
    resp = client.post("/api/fix-right", json={"measures": [
        m("S"), m("A", left=True, times=2), m("B", right=True)]})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert any(e["code"] == "TEMPO_FIRST_REQUIRED"
               for e in body["score_errors"])


def test_api_invalid_json_400():
    resp = client.post("/api/fix-right", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_api_missing_measures_400():
    resp = client.post("/api/fix-right", json={"foo": 1})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"


# --------------------------------------------------------------------------
# 剪枝完备性：小规模全枚举与暴力结论一致
# --------------------------------------------------------------------------

def test_matches_bruteforce_exhaustive():
    """对一批小规模谱面，搜索结果必须与“枚举全部 ≤4 切换子集”完全一致。"""
    import itertools

    from app.expander import normalize_rows

    def brute(measures):
        rows, line_errors = normalize_rows(measures)
        if line_errors:
            return None
        if not expand(rows)[1]:
            return True, []
        by_level: dict[int, list[tuple[tuple[int, ...], int]]] = {}
        n = len(rows)
        for r in range(1, min(MAX_TOGGLES, n) + 1):
            for bits in itertools.combinations(range(n), r):
                trial = [dict(row) for row in rows]
                for idx in bits:
                    trial[idx]["right_repeat"] = not trial[idx]["right_repeat"]
                res, es = expand(trial)
                if not es and res["visit_count"] <= 512:
                    by_level.setdefault(r, []).append(
                        (bits, res["visit_count"]))
        for level in sorted(by_level):
            return False, sorted(by_level[level])[:MAX_SOLUTIONS]
        return False, []

    cases = [
        # 未闭合起点
        [m("S", tempo=120), m("A", left=True, times=2), m("B"), m("T")],
        # 多余终点
        [m("S", tempo=120), m("A", left=True, times=2),
         m("B", left=True, times=2), m("c1", right=True),
         m("c2", right=True), m("c3", right=True)],
        # volta 缺次级终点
        [m("S", tempo=120), m("A", left=True, times=3),
         m("B", volta=1, right=True), m("C", volta=2),
         m("D", volta=3), m("T")],
        # 交叉括号
        [m("S", tempo=120), m("A", left=True, times=2),
         m("B", left=True, times=2), m("x"),
         m("c1", right=True), m("y"), m("c2", right=True), m("T")],
    ]
    for measures in cases:
        expected = brute(measures)
        assert expected is not None
        already_valid, expected_sols = expected
        result = fix_ok(measures)
        assert result["already_valid"] is already_valid
        got = [
            (tuple(c["index"] for c in sol["changes"]),
             sol["visit_count"])
            for sol in result["solutions"]
        ]
        assert got == expected_sols

