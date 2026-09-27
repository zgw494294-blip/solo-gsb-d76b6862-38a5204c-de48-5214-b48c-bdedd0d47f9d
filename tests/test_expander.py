"""展开引擎测试。"""

from app.expander import MAX_VISITS, expand


def m(number, beats=4, left=False, right=False, times=None, volta=None,
      tempo=120, right_closes=None):
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


def check(raw):
    """成功时返回 (visits, result)，失败时 AssertionError 并打印错误。"""
    result, errors = expand(raw)
    assert errors == [], errors
    assert result is not None
    return result["visits"], result


def seq(visits):
    return [v["source_measure"] for v in visits]


# --------------------------------------------------------------------------
# 基本展开
# --------------------------------------------------------------------------

def test_simple_repeat():
    visits, result = check([
        m("A"), m("B", left=True, times=2), m("C", right=True), m("D"),
    ])
    assert seq(visits) == ["A", "B", "C", "B", "C", "D"]
    assert result["visit_count"] == 6
    assert result["total_beats"] == 24
    # 反复段内每次访问路径一致：repeat 1，遍次 1 / 2
    assert [p["pass"] for p in visits[1]["path"]] == [1]
    assert [p["pass"] for p in visits[4]["path"]] == [2]
    assert visits[0]["path"] == []
    assert [v["global_index"] for v in visits] == [1, 2, 3, 4, 5, 6]


def test_single_measure_repeat():
    visits, _ = check([
        m("A"), m("B", left=True, right=True, times=3), m("C"),
    ])
    assert seq(visits) == ["A", "B", "B", "B", "C"]
    assert [v["path"][0]["pass"] for v in visits[1:4]] == [1, 2, 3]


def test_three_times():
    visits, _ = check([
        m("A", left=True, times=3), m("B", right=True), m("C"),
    ])
    assert seq(visits) == ["A", "B", "A", "B", "A", "B", "C"]


# --------------------------------------------------------------------------
# 替代结尾（volta）
# --------------------------------------------------------------------------

def test_volta_before_close_marker_still_acts():
    """经典房子写法：跳过的 {1 小节末尾仍有终点，控制标记必须生效。"""
    visits, _ = check([
        m("A", left=True, times=2),
        m("B"),
        m("C", volta=1, right=True),   # 第 1 遍演奏；右标记第 2 遍跳过本小节时也要跳回
        m("D", volta=2),               # 尾部 volta：第 2 遍演奏
        m("E"),
    ])
    assert seq(visits) == ["A", "B", "C", "A", "B", "D", "E"]
    # C 的路径是 repeat1 pass1；D 的路径是尾部最终遍 pass2
    c = visits[2]
    d = visits[5]
    assert c["path"][0] == {"repeat_id": 1, "pass": 1, "times": 2}
    assert d["path"][0] == {"repeat_id": 1, "pass": 2, "times": 2, "tail": True}


def test_volta_filtering_inside_body():
    """voltat 也可放在反复体内部：非本遍小节整体跳过。"""
    visits, _ = check([
        m("A", left=True, times=2),
        m("B", volta=1),
        m("C", volta=2),
        m("D", right=True),
        m("E"),
    ])
    assert seq(visits) == ["A", "B", "D", "A", "C", "D", "E"]


def test_multi_measure_volta_houses():
    visits, _ = check([
        m("A", left=True, times=3),
        m("B", volta=1, right=True),   # {1 房子，含主终点
        m("C", volta=2, right=True),   # {2 房子，含次级终点
        m("D", volta=3),               # {3 房子（最终房子，无终点）
        m("E"),
    ])
    # 第 1 遍：A B；跳回；第 2 遍：A（跳过 B）C；跳回；第 3 遍：A（跳过 B、C）D
    assert seq(visits) == ["A", "B", "A", "C", "A", "D", "E"]
    houses = [v for v in visits if v["source_measure"] in ("B", "C", "D")]
    assert [v["path"][0]["pass"] for v in houses] == [1, 2, 3]
    # C 的路径是最终遍之前的普通栈帧（pass 2 时该反复还没闭合）
    c = houses[1]
    assert c["path"][0].get("tail") is None
    # D 是最终遍房子：反复在 C 的次级终点惰性退出后进入尾部
    d = houses[2]
    assert d["path"][0].get("tail") is True


def test_two_house_volta_canonical():
    """最常见两房子：{1 含主终点，{2 无终点（尾部 volta）。"""
    visits, _ = check([
        m("A", left=True, times=2),
        m("B", volta=1, right=True),
        m("C", volta=2),
        m("D"),
    ])
    assert seq(visits) == ["A", "B", "A", "C", "D"]


def test_marker_on_skipped_house_still_controls_flow():
    """{1 房子小节在第 2/3 遍被跳过，但它末尾的 :| 仍驱动跳回；
    被跳过的 {2 房子小节在最终遍到达时让惰性退出发生。"""
    visits, _ = check([
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=2, right=True),
        m("D", volta=3),
        m("E"),
    ])
    assert seq(visits) == ["A", "B", "A", "C", "A", "D", "E"]
    # pass 3 时 B、C 都被跳过，却依然完成了两次“惰性退出”推进
    a3 = [v for v in visits if v["source_measure"] == "A"][2]
    assert a3["path"][0]["pass"] == 3


def test_four_houses():
    visits, _ = check([
        m("A", left=True, times=4),
        m("B", volta=1, right=True),
        m("C", volta=2, right=True),
        m("D", volta=3, right=True),
        m("E", volta=4),
        m("F"),
    ])
    assert seq(visits) == ["A", "B", "A", "C", "A", "D", "A", "E", "F"]


def test_sibling_repeats_in_sequence():
    visits, _ = check([
        m("A"),
        m("B", left=True, times=2), m("C", right=True),
        m("D", left=True, times=3), m("E", right=True),
        m("F"),
    ])
    assert seq(visits) == ["A", "B", "C", "B", "C",
                           "D", "E", "D", "E", "D", "E", "F"]


def test_new_repeat_start_breaks_tail():
    """尾部房子之后立刻开始一对新的反复，尾巴归属不得延伸进新反复。"""
    visits, result = check([
        m("A", left=True, times=2),
        m("B", volta=1, right=True),
        m("C", volta=2),
        m("D", left=True, times=2),
        m("E", right=True),
        m("F"),
    ])
    assert seq(visits) == ["A", "B", "A", "C", "D", "E", "D", "E", "F"]
    assert [r["open_measure"] for r in result["repeats"]] == ["A", "D"]


# --------------------------------------------------------------------------
# 嵌套
# --------------------------------------------------------------------------

def test_nested_repeats_paths():
    visits, _ = check([
        m("A"),
        m("B", left=True, times=2),
        m("C", left=True, times=2),
        m("D", right=True),
        m("E", right=True),
        m("F"),
    ])
    assert seq(visits) == [
        "A",
        "B", "C", "D", "C", "D", "E",   # 外层第 1 遍
        "B", "C", "D", "C", "D", "E",   # 外层第 2 遍：内层计数重新从 1 开始
        "F",
    ]
    # 外层第 2 遍的第一次内层访问，路径为 [repeat1 pass2, repeat2 pass1]
    sample = visits[7]
    assert sample["source_measure"] == "B"
    assert [(p["repeat_id"], p["pass"]) for p in sample["path"]] == [(1, 2)]
    inner = visits[8]
    assert inner["source_measure"] == "C"
    assert [(p["repeat_id"], p["pass"]) for p in inner["path"]] == [(1, 2), (2, 1)]
    assert inner["path"][1]["times"] == 2


def test_nested_with_volta_tail_on_inner():
    """内层 {2 房子紧接内层终点；外层每一遍内层都从头计数。"""
    visits, _ = check([
        m("A", left=True, times=2),
        m("B", left=True, times=2),
        m("C", volta=1, right=True),   # {1：内含内层终点
        m("D", volta=2),               # {2：内层尾部 volta
        m("E", right=True),            # 外层终点
        m("F"),
    ])
    # 外层每遍：A B C B D E
    assert seq(visits) == [
        "A", "B", "C", "B", "D", "E",
        "A", "B", "C", "B", "D", "E",
        "F",
    ]
    # 第 2 个 D（外层 pass2）路径同时携带两层
    d2 = [v for v in visits if v["source_measure"] == "D"][1]
    assert [(p["repeat_id"], p["pass"]) for p in d2["path"]] == [(1, 2), (2, 2)]
    assert d2["path"][-1].get("tail") is True
    # 外层第 2 遍的第一个 C 内层遍次必须重新从 1 开始
    c2 = [v for v in visits if v["source_measure"] == "C"][1]
    assert [(p["repeat_id"], p["pass"]) for p in c2["path"]] == [(1, 2), (2, 1)]


def test_cumulative_beats():
    visits, _ = check([
        m("A", beats=3), m("B", beats=2, left=True, times=2),
        m("C", beats=1, right=True),
    ])
    # A=3, B=5, C=6, B=8, C=9
    assert [v["cumulative_beats"] for v in visits] == [3, 5, 6, 8, 9]


# --------------------------------------------------------------------------
# 校验错误
# --------------------------------------------------------------------------

def errors_of(raw):
    result, errors = expand(raw)
    assert result is None
    return errors


def test_measure_count_limits():
    codes = {e["code"] for e in errors_of([m("A"), m("B")][:1])}
    assert "MEASURE_COUNT" in codes

    big = [m(f"n{i}") for i in range(65)]
    assert "MEASURE_COUNT" in {e["code"] for e in errors_of(big)}


def test_duplicate_number_located():
    errs = errors_of([m("A"), m("A", left=True, times=2), m("B", right=True)])
    dup = [e for e in errs if e["code"] == "NUMBER_DUPLICATE"]
    assert len(dup) == 1
    assert dup[0]["index"] == 1
    assert dup[0]["measure"] == "A"


def test_unmatched_close():
    errs = errors_of([m("A"), m("B", right=True)])
    assert errs[0]["code"] == "REPEAT_UNMATCHED_CLOSE"
    assert errs[0]["index"] == 1


def test_unmatched_open():
    errs = errors_of([m("A", left=True, times=2), m("B")])
    assert errs[0]["code"] == "REPEAT_UNMATCHED_OPEN"
    assert errs[0]["index"] == 0


def test_proper_nesting_accepted():
    visits, _ = check([
        m("A", left=True, times=2),
        m("B", left=True, times=2),
        m("C", right=True),
        m("D", right=True),
    ])
    assert seq(visits) == ["A", "B", "C", "B", "C", "D",
                           "A", "B", "C", "B", "C", "D"]


def test_crossed_repeats_rejected():
    """交叉括号 |: ... |: ... :| ... :| 在栈式配对下表现为起点未闭合。

    想表达 A 开、B 开、先闭 A、再闭 B 时，栈会把第一个 :| 配给 B，
    A 最终找不到终点。
    """
    errs = errors_of([
        m("A", left=True, times=2),   # 开 A
        m("B", left=True, times=2),   # 开 B
        m("C", right=True),           # 闭 B（栈顶）
        m("D"),                       # A 永远等不到闭
    ])
    assert {e["code"] for e in errs} == {"REPEAT_UNMATCHED_OPEN"}
    assert errs[0]["measure"] == "A"
    assert errs[0]["index"] == 0


def test_times_required_and_range():
    errs = errors_of([m("A"), m("B", left=True), m("C", right=True)])
    assert any(e["code"] == "TIMES_REQUIRED" and e["index"] == 1 for e in errs)

    errs = errors_of([
        m("A", left=True, times=5), m("B", right=True),
    ])
    assert any(e["code"] == "TIMES_RANGE" for e in errs)

    errs = errors_of([
        m("A"), m("B", times=3), m("C"),
    ])
    assert any(e["code"] == "TIMES_WITHOUT_LEFT" for e in errs)


def test_volta_outside_repeat():
    errs = errors_of([m("A", volta=1), m("B")])
    assert errs[0]["code"] == "VOLTA_OUTSIDE_REPEAT"
    assert errs[0]["index"] == 0


def test_volta_pass_out_of_range():
    # times=2 的反复，尾部 {2 房子位置却写成 {3：配对阶段即发现缺 {2 房子。
    errs = errors_of([
        m("A", left=True, times=2),
        m("B", volta=1, right=True),
        m("C", volta=3),
        m("D"),
    ])
    codes = {e["code"] for e in errs}
    assert "VOLTA_HOUSE_MISSING" in codes

    # 孤立的 volta 小节（不在任何反复内、也不是尾巴）单独报归属错误。
    errs = errors_of([
        m("A"), m("X", volta=2), m("B"),
    ])
    assert any(e["code"] == "VOLTA_OUTSIDE_REPEAT" and e["index"] == 1 for e in errs)


def test_volta_body_out_of_range():
    # 反复体内部的 volta 小节遍次超出 times
    errs = errors_of([
        m("A", left=True, times=2),
        m("B", volta=3),
        m("C", right=True),
    ])
    assert any(e["code"] == "VOLTA_PASS_INVALID" and e["index"] == 1 for e in errs)


def test_main_close_wrong_house():
    errs = errors_of([
        m("A", left=True, times=3),
        m("B", volta=2, right=True),
        m("C"),
    ])
    assert errs[0]["code"] == "REPEAT_MAIN_CLOSE_VOLTA"
    assert errs[0]["index"] == 1


def test_house_sequence_incomplete():
    # times=3、主终点在 {1，但只给了 {2 就被普通小节打断，缺 {3
    errs = errors_of([
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=2, right=True),
        m("D"),
    ])
    assert any(e["code"] == "VOLTA_HOUSE_MISSING" for e in errs)

    # {2 缺失却直接给 {3：同样报缺失
    errs = errors_of([
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=3),
        m("D"),
    ])
    assert any(e["code"] == "VOLTA_HOUSE_MISSING" for e in errs)


def test_house_multiple_measures():
    """同一个房子可以跨多个小节（仅最后一个小节带 :|）。"""
    visits, _ = check([
        m("A", left=True, times=2),
        m("B", volta=1),
        m("C", volta=1, right=True),    # {1 房子两小节
        m("D", volta=2),
        m("E", volta=2),                # {2 房子两小节，无终点也合法（最终房子）
        m("F"),
    ])
    assert seq(visits) == ["A", "B", "C", "A", "D", "E", "F"]


def test_outer_house_after_inner_tail_not_swallowed():
    """内层 {2 房子之后紧跟外层 {1 房子：房子编号 1≠2，外层终点必须配对给外层。"""
    visits, result = check([
        m("A", left=True, times=2),       # 外层开
        m("B", left=True, times=2),       # 内层开
        m("C", volta=1, right=True),      # 内层 {1 + 主终点
        m("D", volta=2),                  # 内层 {2
        m("E", volta=1, right=True),      # 外层 {1 + 主终点
        m("F", volta=2),                  # 外层 {2
        m("G"),
    ])
    # 内层：B C B D；外层两遍各重复一次
    assert seq(visits) == [
        "A",
        "B", "C", "B", "D",
        "E",
        "A",
        "B", "C", "B", "D",
        "F",
        "G",
    ]
    # 外层主终点在 E（索引 4），内层主终点在 C（索引 2）
    closes = sorted((r["id"], r["close_index"]) for r in result["repeats"])
    assert closes == [(1, 4), (2, 2)]


def test_unclosed_multi_measure_house_rejected():
    errs = errors_of([
        m("A", left=True, times=3),
        m("B", volta=1, right=True),
        m("C", volta=2),                  # {2 房子开始
        m("D", volta=3),                  # 未闭合就进 {3
        m("E"),
    ])
    assert any(e["code"] == "VOLTA_HOUSE_UNCLOSED" and e["index"] == 3 for e in errs)


# --------------------------------------------------------------------------
# 同线双终点（right_closes）
# --------------------------------------------------------------------------

def test_sameline_double_close_with_volta():
    # 内层 {2 房子次级终点与外层主终点落在同一行（right_closes 内层在前）。
    visits, result = check([
        m("1"),
        m("2", left=True, times=2),                 # 外层起点
        m("3", left=True, times=2),                 # 内层起点
        m("4", volta=1, right=True),                # 内层 {1 主终点
        m("5", volta=2, right=True,                 # 内层 {2 次级 + 外层主
          right_closes=[2, 1]),
        m("6"),
        m("7"),
    ])
    assert seq(visits) == [
        "1", "2", "3", "4", "3", "5",
        "2", "3", "4", "3", "5", "6", "7"]
    closes = sorted((r["id"], r["open_index"], r["close_index"],
                     r["secondary_close_indices"]) for r in result["repeats"])
    assert closes == [(1, 1, 4, []), (2, 2, 3, [4])]


def test_sameline_double_close_plain_two_main_backwards():
    # 无房子：同一行两个主终点（内层 + 外层 backward）。外层闭合延后到
    # 内层最终遍；m6 在闭合线之后，只在外层最终遍后演奏。
    visits, result = check([
        m("1"),
        m("2", left=True, times=2),
        m("3", left=True, times=2),
        m("4"),
        m("5", right=True, right_closes=[2, 1]),
        m("6"),
        m("7"),
    ])
    assert seq(visits) == [
        "1",
        "2", "3", "4", "5", "3", "4", "5",
        "2", "3", "4", "5", "3", "4", "5",
        "6", "7"]


def test_sameline_double_close_times_three_inner():
    # 内层 3 遍：{1 主终点 m4，{2 次级 m5，{3 次级与外层主终点同在 m6
    # 一行（right_closes 内层在前）；外层两遍。
    visits, _ = check([
        m("1"),
        m("2", left=True, times=2),
        m("3", left=True, times=3),
        m("4", volta=1, right=True),
        m("5", volta=2, right=True),
        m("6", volta=3, right=True, right_closes=[2, 1]),
        m("7"),
    ])
    assert seq(visits) == [
        "1",
        "2", "3", "4", "3", "5", "3", "6",
        "2", "3", "4", "3", "5", "3", "6",
        "7"]


def test_right_closes_format_errors():
    base = [
        m("A"),
        m("B", left=True, times=2),
        m("C", left=True, times=2),
        m("D", right=True, right_closes=[1, 2]),
    ]
    # 不是数组
    bad = [dict(r) for r in base]
    bad[3] = {**bad[3], "right_closes": "x"}
    assert errors_of(bad)[0]["code"] == "RIGHT_CLOSES_FORMAT"
    # 空数组
    bad = [dict(r) for r in base]
    bad[3] = {**bad[3], "right_closes": []}
    assert errors_of(bad)[0]["code"] == "RIGHT_CLOSES_FORMAT"
    # 超过 2 个
    bad = [dict(r) for r in base]
    bad[3] = {**bad[3], "right_closes": [0, 1, 2]}
    assert errors_of(bad)[0]["code"] == "RIGHT_CLOSES_FORMAT"
    # 行号越界
    bad = [dict(r) for r in base]
    bad[3] = {**bad[3], "right_closes": [2, 9]}
    assert errors_of(bad)[0]["code"] == "RIGHT_CLOSES_FORMAT"
    # 重复
    bad = [dict(r) for r in base]
    bad[3] = {**bad[3], "right_closes": [2, 2]}
    assert errors_of(bad)[0]["code"] == "RIGHT_CLOSES_FORMAT"
    # 有 right_closes 但 right_repeat 为假
    bad = [dict(r) for r in base]
    bad[3] = {**bad[3], "right_repeat": False, "right_closes": [1, 2]}
    codes = {e["code"] for e in errors_of(bad)}
    assert "RIGHT_CLOSES_WITHOUT_RIGHT" in codes


def test_right_closes_owner_mismatch_rejected():
    # 归属与栈配对不一致（顺序写反：外层在前、内层在后）
    bad = [
        m("A"),
        m("B", left=True, times=2),
        m("C", left=True, times=2),
        m("D", right=True, right_closes=[1, 2]),
        m("E"),
    ]
    errs = errors_of(bad)
    assert any(e["code"] == "REPEAT_CLOSE_OWNER" and e["index"] == 3 for e in errs)


def test_single_endpoint_scores_unaffected_without_right_closes():
    # 不带 right_closes 的既有谱面行为不变（同线位置只有一个右标记时
    # 外层起点未闭合仍报 REPEAT_UNMATCHED_OPEN）。
    errs = errors_of([
        m("A"),
        m("B", left=True, times=2),
        m("C", left=True, times=2),
        m("D", right=True),
        m("E"),
    ])
    assert any(e["code"] == "REPEAT_UNMATCHED_OPEN" for e in errs)


def test_field_errors_collected():
    errs = errors_of([
        {"number": " ", "beats": 0, "left_repeat": "x",
         "right_repeat": True, "times": None, "volta": 9},
        {"number": 2, "beats": 4, "left_repeat": False,
         "right_repeat": False, "times": None, "volta": None},
    ])
    codes = {e["code"] for e in errs}
    assert "NUMBER_REQUIRED" in codes
    assert "BEATS_RANGE" in codes
    assert "LEFT_REPEAT_FORMAT" in codes
    assert "VOLTA_FORMAT" in codes
    # 字段错误在结构配对前返回，错误全部定位到具体行
    assert all(e["index"] in (0, 1) for e in errs)
    assert any(e["index"] == 0 for e in errs)


def test_invalid_request_shapes():
    assert errors_of({"not": "a list"})[0]["code"] == "INVALID_REQUEST"
    assert errors_of("nope")[0]["code"] == "INVALID_REQUEST"
    assert errors_of([m("A")])[0]["code"] == "MEASURE_COUNT"


# --------------------------------------------------------------------------
# 512 上限
# --------------------------------------------------------------------------

def test_near_limit_accepted():
    # 3 层 ×4 遍。深度计数：A=4, B=16, C=64；5 个普通体内小节各 64；
    # 闭合小节 I=64, J=16, K=4。合计 4+16+64+320+64+16+4 = 488 ≤ 512。
    raw = [
        m("A", left=True, times=4),
        m("B", left=True, times=4),
        m("C", left=True, times=4),
        m("D"), m("E"), m("F"), m("G"), m("H"),
        m("I", right=True),
        m("J", right=True),
        m("K", right=True),
    ]
    visits, result = check(raw)
    assert result["visit_count"] == 488


def test_over_limit_rejected():
    # 在上一例基础上把体内普通小节增加到 6 个：488 + 64 = 552 > 512。
    raw = [
        m("A", left=True, times=4),
        m("B", left=True, times=4),
        m("C", left=True, times=4),
        m("D"), m("E"), m("F"), m("G"), m("H"), m("I"),
        m("J", right=True),
        m("K", right=True),
        m("L", right=True),
    ]
    result, errors = expand(raw)
    assert result is None
    assert errors[0]["code"] == "TOO_MANY_VISITS"
    assert MAX_VISITS == 512


# --------------------------------------------------------------------------
# 速度标记（BPM）
# --------------------------------------------------------------------------

def test_tempo_first_required():
    result, errors = expand([
        {"number": "A", "beats": 4},
        {"number": "B", "beats": 4},
    ])
    assert result is None
    codes = {e["code"] for e in errors}
    assert "TEMPO_FIRST_REQUIRED" in codes


def test_tempo_range():
    result, errors = expand([
        {"number": "A", "beats": 4, "tempo": 39},
        {"number": "B", "beats": 4, "tempo": 241},
    ])
    assert result is None
    bad = [e for e in errors if e["code"] == "TEMPO_RANGE"]
    assert {e["index"] for e in bad} == {0, 1}
    # 首小节已是数值超界，不再叠加“必填”错误
    assert all(e["code"] != "TEMPO_FIRST_REQUIRED" for e in errors)


def test_tempo_carries_forward_and_attaches_to_visits():
    # 60 设在首小节，中间留空沿用；反复回来的 B 仍取 B 来源小节的生效值。
    visits, result = check([
        m("A", tempo=60),
        m("B", left=True, times=2, tempo=None),  # 留空沿用 60
        m("C", right=True, tempo=180),           # 新标记 180
        m("D", tempo=None),                      # 沿用 180
    ])
    assert seq(visits) == ["A", "B", "C", "B", "C", "D"]
    assert [v["tempo"] for v in visits] == [60, 60, 180, 60, 180, 180]
    assert [v["tempo_marked"] for v in visits] == [
        True, False, True, False, True, False]

    tempos = result["tempos"]
    assert [t["tempo"] for t in tempos] == [60, 60, 180, 180]
    assert [t["marked"] for t in tempos] == [True, False, True, False]
    assert result["tempo_range"] == [40, 240]


def test_tempo_boundaries_accepted():
    visits, _ = check([m("A", tempo=40), m("B", tempo=240)])
    assert [v["tempo"] for v in visits] == [40, 240]
