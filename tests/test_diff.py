"""改谱影响对照（LCS 对齐、差异分类与时间偏移）测试。"""

import pytest

from app.diff import _lcs_pairs, compare
from app.expander import expand


def m(number, beats=4, tempo=120, left=False, right=False, times=None,
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


def expand_ok(rows):
    result, errors = expand(rows)
    assert errors == [], errors
    assert result is not None
    return result


def compare_rows(base_rows, curr_rows):
    return compare(expand_ok(base_rows), expand_ok(curr_rows))


# --------------------------------------------------------------------------
# LCS：配对下标对
# --------------------------------------------------------------------------

def test_lcs_basic():
    assert _lcs_pairs(["A", "B", "C"], ["A", "C"]) == [(0, 0), (2, 1)]


def test_lcs_repeated_visits_pair_individually():
    # 重复访问逐次参与配对：基准 3 次 A 对当前 2 次 A，配两对、剩一个删去
    assert _lcs_pairs(["A", "A", "A"], ["A", "A"]) == [(0, 0), (1, 1)]


def test_lcs_tiebreak_prefers_earliest_base_index():
    # 两种最优解 (B,B)=[(0,1)] 与 (A,A)=[(1,0)]：字典序最小为 [(0,1)]
    assert _lcs_pairs(["B", "A"], ["A", "B"]) == [(0, 1)]


def test_lcs_tiebreak_prefers_earliest_curr_index():
    # 基准下标相同时取最小当前下标
    assert _lcs_pairs(["A"], ["A", "A"]) == [(0, 0)]


def test_lcs_tiebreak_sequence_level():
    # 候选 [(0,0),(1,2)] / [(0,0),(2,1)] / [(0,1),(1,2)] → 最小 [(0,0),(1,2)]
    assert _lcs_pairs(["A", "B", "A"], ["A", "A", "B"]) == [(0, 0), (1, 2)]


def test_lcs_empty():
    assert _lcs_pairs([], ["A"]) == []
    assert _lcs_pairs(["A"], []) == []
    assert _lcs_pairs(["A"], ["B"]) == []


# --------------------------------------------------------------------------
# 对照：统计与差异分类
# --------------------------------------------------------------------------

def test_compare_identical_no_difference():
    rows = [m("A"), m("B", left=True, times=2), m("C", right=True)]
    result = compare_rows(rows, [dict(r) for r in rows])
    assert result["stats"] == {"paired": 5, "unchanged": 5, "changed": 0,
                               "added": 0, "removed": 0}
    assert result["first_difference"] is None
    assert all(not ev["differs"] for ev in result["events"])
    assert result["baseline"]["total_seconds"] == pytest.approx(
        result["current"]["total_seconds"])


def test_compare_added_visit():
    result = compare_rows(
        [m("A"), m("B"), m("C")],
        [m("A"), m("X"), m("B"), m("C")],
    )
    assert result["stats"]["added"] == 1
    added = [ev for ev in result["events"] if ev["kind"] == "added"]
    assert added[0]["curr"]["source_measure"] == "X"
    assert added[0]["curr"]["global_index"] == 2
    # 首次差异即该新增事件（对齐第 2 位）
    assert result["first_difference"]["kind"] == "added"
    assert result["first_difference"]["align_index"] == 1


def test_compare_removed_visit():
    result = compare_rows(
        [m("A"), m("B"), m("C")],
        [m("A"), m("C")],
    )
    assert result["stats"]["removed"] == 1
    removed = [ev for ev in result["events"] if ev["kind"] == "removed"]
    assert removed[0]["base"]["source_measure"] == "B"
    assert removed[0]["base"]["global_index"] == 2


def test_compare_removed_before_added_in_same_gap():
    # 基准 A B C，当前 A X C：B 删去、X 新增，同一缺口内删去排在新增之前
    result = compare_rows(
        [m("A"), m("B"), m("C")],
        [m("A"), m("X"), m("C")],
    )
    assert [ev["kind"] for ev in result["events"]] == [
        "pair", "removed", "added", "pair"]


def test_compare_repeated_visits_pair_individually():
    # 反复访问逐次配对：遍数 2 → 3 时，前两遍逐次配对，第三遍两次访问为新增
    result = compare_rows(
        [m("A", left=True, times=2), m("B", right=True)],
        [m("A", left=True, times=3), m("B", right=True)],
    )
    assert result["stats"]["paired"] == 4
    assert result["stats"]["added"] == 2
    added = [ev for ev in result["events"] if ev["kind"] == "added"]
    assert [ev["curr"]["source_measure"] for ev in added] == ["A", "B"]
    assert all(ev["curr"]["path"][0]["pass"] == 3 for ev in added)


# --------------------------------------------------------------------------
# 配对访问的字段比较
# --------------------------------------------------------------------------

def test_compare_beats_change():
    result = compare_rows(
        [m("A"), m("B")],
        [m("A"), m("B", beats=2)],
    )
    pair = result["events"][1]
    assert pair["kind"] == "pair"
    assert pair["changes"] == ["beats"]
    assert result["stats"]["changed"] == 1


def test_compare_tempo_change_inherited():
    # B 留空沿用 A 的标记：A 改 BPM 后，两次访问的生效速度都变
    result = compare_rows(
        [m("A", tempo=60), m("B", tempo=None)],
        [m("A", tempo=120), m("B", tempo=None)],
    )
    assert result["events"][0]["changes"] == ["tempo"]
    assert result["events"][1]["changes"] == ["tempo"]


def test_compare_path_change_and_added_pass():
    result = compare_rows(
        [m("S"), m("A", left=True, times=2), m("B", right=True)],
        [m("S"), m("A", left=True, times=3), m("B", right=True)],
    )
    pairs = [ev for ev in result["events"] if ev["kind"] == "pair"]
    assert pairs[0]["changes"] == []  # S 在反复段外
    # 反复段内配对访问的总遍数 2→3，遍次路径全部标记变更
    assert all(ev["changes"] == ["path"] for ev in pairs[1:])
    # 首次差异是第一次进入反复的访问（对齐第 2 位）
    assert result["first_difference"]["kind"] == "pair"
    assert result["first_difference"]["align_index"] == 1


def test_compare_volta_tail_path_preserved_in_refs():
    result = compare_rows(
        [m("A", left=True, times=2), m("B", volta=1, right=True),
         m("C", volta=2)],
        [m("A", left=True, times=2), m("B", volta=1, right=True),
         m("C", volta=2, beats=2)],
    )
    pairs = [ev for ev in result["events"] if ev["kind"] == "pair"]
    tail_pair = pairs[-1]
    assert tail_pair["curr"]["source_measure"] == "C"
    assert tail_pair["changes"] == ["beats"]
    assert tail_pair["curr"]["path"][0]["tail"] is True


# --------------------------------------------------------------------------
# 起拍时间与偏移（每拍 60/BPM 秒）
# --------------------------------------------------------------------------

def test_compare_time_offset_accumulates():
    # 120 BPM → 每拍 0.5s
    result = compare_rows(
        [m("A", beats=4, tempo=120), m("B", beats=4, tempo=120)],
        [m("A", beats=2, tempo=120), m("B", beats=4, tempo=120)],
    )
    e0, e1 = result["events"]
    # A：拍数变更，起拍不变
    assert e0["changes"] == ["beats"]
    assert e0["time_offset"] == pytest.approx(0.0)
    # B：自身未变，但起拍提前 2 拍 × 0.5s = 1s
    assert e1["changes"] == []
    assert e1["time_offset"] == pytest.approx(-1.0)
    assert e1["base"]["start_seconds"] == pytest.approx(2.0)
    assert e1["curr"]["start_seconds"] == pytest.approx(1.0)
    assert result["baseline"]["total_seconds"] == pytest.approx(4.0)
    assert result["current"]["total_seconds"] == pytest.approx(3.0)
    # 首次差异是 A 的拍数变更
    assert result["first_difference"]["align_index"] == 0
    assert result["first_difference"]["changes"] == ["beats"]


def test_compare_tempo_change_shifts_start_times():
    result = compare_rows(
        [m("A", tempo=60), m("B", tempo=None)],
        [m("A", tempo=120), m("B", tempo=None)],
    )
    # A 时长 4×(60/60)=4s → 4×(60/120)=2s；B 起拍 4s → 2s
    assert result["events"][1]["time_offset"] == pytest.approx(-2.0)
    assert result["baseline"]["total_seconds"] == pytest.approx(8.0)
    assert result["current"]["total_seconds"] == pytest.approx(4.0)


def test_compare_added_visit_shifts_following_pairs():
    # 新增 4 拍 120 BPM（2s）的小节后，后续配对访问偏移 +2s
    result = compare_rows(
        [m("A"), m("B")],
        [m("A"), m("X"), m("B")],
    )
    pair_b = [ev for ev in result["events"]
              if ev["kind"] == "pair" and ev["curr"]["source_measure"] == "B"][0]
    assert pair_b["changes"] == []
    assert pair_b["time_offset"] == pytest.approx(2.0)
