"""MusicXML 小节时值审计测试。"""

from fastapi.testclient import TestClient

from app.main import app
from app.musicxml_audit import audit_musicxml

client = TestClient(app)

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

ATTRS = (
    '<attributes><divisions>1</divisions>'
    '<time><beats>4</beats><beat-type>4</beat-type></time></attributes>'
)


def measure(num, inner="", attrs=ATTRS):
    return f'<measure number="{num}">{attrs}{inner}</measure>'


def build(measures):
    return HEADER.format(measures="".join(measures))


def note(dur, voice=None, chord=False, grace=False, rest=False):
    parts = ["<note>"]
    if grace:
        parts.append("<grace/>")
    if chord:
        parts.append("<chord/>")
    if rest:
        parts.append("<rest/>")
    else:
        parts.append("<pitch><step>C</step><octave>4</octave></pitch>")
    if dur is not None:
        parts.append(f"<duration>{dur}</duration>")
    if voice is not None:
        parts.append(f"<voice>{voice}</voice>")
    parts.append("</note>")
    return "".join(parts)


def backup(dur):
    return f"<backup><duration>{dur}</duration></backup>"


def forward(dur):
    return f"<forward><duration>{dur}</duration></forward>"


def audit_ok(xml):
    result, errors = audit_musicxml(xml)
    assert not errors, errors
    assert result is not None
    return result


def audit_fail(xml):
    result, errors = audit_musicxml(xml)
    assert result is None
    assert errors
    return errors


# --------------------------------------------------------------------------
# 通过：各声部时值完整
# --------------------------------------------------------------------------

def test_whole_notes_pass():
    xml = build([
        measure("1", note(4)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["measure_count"] == 2
    assert result["issue_count"] == 0
    assert result["issues"] == []


def test_rest_and_mixed_durations_pass():
    xml = build([
        measure("1", note(1) + note(1, rest=True) + note(2)),
        measure("2", note(4, rest=True), ""),
    ])
    assert audit_ok(xml)["issue_count"] == 0


def test_forward_fills_nothing_but_cursor_moves():
    # forward 只移动游标不占区间：note(1) + forward(2) + note(1) → 缺拍 1~3
    xml = build([
        measure("1", note(1) + forward(2) + note(1)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["start"], i["end"]) for i in result["issues"]] == [
        ("gap", "1", "3"),
    ]
    assert result["issues"][0]["voice"] == "1"
    assert result["issues"][0]["index"] == 0
    assert result["issues"][0]["measure"] == "1"


def test_grace_notes_take_no_time():
    # grace 不占时、不要求 duration、不参与区间
    xml = build([
        measure("1", note(None, grace=True) + note(4)),
        measure("2", note(2, grace=True) + note(4), ""),
    ])
    assert audit_ok(xml)["issue_count"] == 0


def test_chord_merges_into_single_interval():
    # 同起点和弦合成一个区间：不构成重叠、不推进游标
    xml = build([
        measure("1", note(4) + note(4, chord=True)),
        measure("2", note(2) + note(2, chord=True) + note(2), ""),
    ])
    assert audit_ok(xml)["issue_count"] == 0


def test_chord_group_end_takes_max():
    # 和弦音比主音长：区间终点取组内最大（0~3），后续音符从游标 2 起
    xml = build([
        measure("1", note(2) + note(3, chord=True) + note(1) + note(1)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    # 区间 [0,3) 与 [2,3)（游标只按主音推进到 2）→ 重叠 2~3
    assert [(i["kind"], i["start"], i["end"]) for i in result["issues"]] == [
        ("overlap", "2", "3"),
    ]


def test_two_voices_pass():
    xml = build([
        measure("1", note(4, voice="1") + backup(4) + note(4, voice="2")),
        measure("2", note(4), ""),
    ])
    assert audit_ok(xml)["issue_count"] == 0


def test_divisions_carry_over_measures():
    # divisions 只在首节声明（=2），跨小节沿用：次节 duration 8 → 4 拍
    attrs = ('<attributes><divisions>2</divisions>'
             '<time><beats>4</beats><beat-type>4</beat-type></time>'
             '</attributes>')
    xml = build([
        measure("1", note(8), attrs),
        measure("2", note(4) + note(4), ""),  # 2 拍 + 2 拍
    ])
    assert audit_ok(xml)["issue_count"] == 0


def test_divisions_rational_positions():
    # divisions=2：duration 3 → 3/2 拍；有理数拍位
    attrs = ('<attributes><divisions>2</divisions>'
             '<time><beats>4</beats><beat-type>4</beat-type></time></attributes>')
    xml = build([
        measure("1", note(5) + note(2), attrs),   # 5/2 + 1 = 7/2 → 缺 1/2
        measure("2", note(8), ""),
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["start"], i["end"]) for i in result["issues"]] == [
        ("gap", "7/2", "4"),
    ]


def test_mid_measure_divisions_change():
    # 小节中途 attributes 更新 divisions，其后的 duration 按新值解释，
    # 并跨小节沿用（第 2 小节重新声明为 1）
    inner = (note(2)  # divisions=1：2 拍
             + '<attributes><divisions>2</divisions></attributes>'
             + note(4))  # divisions=2：2 拍
    xml = build([
        measure("1", inner),
        measure("2", note(4),
                '<attributes><divisions>1</divisions></attributes>'),
    ])
    assert audit_ok(xml)["issue_count"] == 0


def test_time_signature_change_mid_score():
    # 第 2 小节起换 3/4：该节按 3 拍核对
    attrs34 = ('<attributes><time><beats>3</beats>'
               '<beat-type>4</beat-type></time></attributes>')
    xml = build([
        measure("1", note(4)),
        measure("2", note(3), attrs34),
        measure("3", note(3), ""),
    ])
    assert audit_ok(xml)["issue_count"] == 0


def test_ignores_tempo_repeats_and_endings():
    # 速度缺失、反复未配对会导致结构导入失败，但与时值审计无关
    inner = ('<barline location="left"><repeat direction="forward"/></barline>'
             + note(4))
    xml = build([measure("1", inner), measure("2", note(4), "")])
    assert audit_ok(xml)["issue_count"] == 0


# --------------------------------------------------------------------------
# 时值问题：缺拍 / 非和弦重叠 / 越过小节边界
# --------------------------------------------------------------------------

def test_gap_tail():
    xml = build([
        measure("1", note(3)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["voice"], i["start"], i["end"])
            for i in result["issues"]] == [("gap", "1", "3", "4")]


def test_empty_measure_is_full_gap():
    xml = build([
        measure("1", note(4)),
        measure("2", "", ""),  # 无任何音符：默认声部整节缺拍
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["voice"], i["start"], i["end"], i["index"])
            for i in result["issues"]] == [("gap", "1", "0", "4", 1)]


def test_voice_gap_reported_per_voice():
    # 声部 2 只有 2 拍：缺拍按声部列出；声部 1 完整
    xml = build([
        measure("1", note(4, voice="1") + backup(4) + note(2, voice="2")),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["voice"], i["start"], i["end"])
            for i in result["issues"]] == [("gap", "2", "2", "4")]


def test_overlap_same_voice_not_chord():
    # 两个普通音符（非 chord）区间交叠：[0,2) 与 [1,3)
    xml = build([
        measure("1", note(2) + backup(1) + note(2) + note(1)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["start"], i["end"]) for i in result["issues"]] == [
        ("overlap", "1", "2"),
    ]


def test_overlap_and_gap_same_measure():
    xml = build([
        measure("1", note(2) + backup(1) + note(2)),  # [0,2) [1,3)：重叠+缺 3~4
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["start"], i["end"]) for i in result["issues"]] == [
        ("overlap", "1", "2"),
        ("gap", "3", "4"),
    ]


def test_overflow_beyond_measure():
    xml = build([
        measure("1", note(3) + note(2)),  # [3,5) 越过 4 拍边界
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert [(i["kind"], i["start"], i["end"]) for i in result["issues"]] == [
        ("overflow", "3", "5"),
    ]
    assert "4 拍" in result["issues"][0]["message"]


def test_issues_sorted_by_measure_voice_position():
    xml = build([
        measure("1", note(4)),
        measure("2",
                note(4, voice="1")
                + backup(4) + note(1, voice="2")          # v2 缺 1~4
                + backup(1) + note(1, voice="2")          # v2 [0,1) 与 [0,1) 重叠
                + forward(3),
                ""),
        measure("3", note(1), ""),
    ])
    result = audit_ok(xml)
    got = [(i["index"], i["voice"], i["kind"], i["start"], i["end"])
           for i in result["issues"]]
    assert got == [
        (1, "2", "overlap", "0", "1"),
        (1, "2", "gap", "1", "4"),
        (2, "1", "gap", "1", "4"),
    ]


# --------------------------------------------------------------------------
# 硬错误：按源小节报错，不显示通过
# --------------------------------------------------------------------------

def test_missing_divisions():
    attrs = ('<attributes><time><beats>4</beats>'
             '<beat-type>4</beat-type></time></attributes>')
    xml = build([measure("1", note(4), attrs), measure("2", note(4), "")])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_AUDIT_DIVISIONS"
    assert errors[0]["index"] == 0
    assert errors[0]["measure"] == "1"


def test_invalid_divisions():
    for bad in ("0", "-1", "1.5", "x"):
        attrs = (f'<attributes><divisions>{bad}</divisions>'
                 '<time><beats>4</beats><beat-type>4</beat-type></time>'
                 '</attributes>')
        xml = build([measure("1", note(4), attrs), measure("2", note(4), "")])
        errors = audit_fail(xml)
        assert errors[0]["code"] == "MUSICXML_AUDIT_DIVISIONS", bad


def test_invalid_duration():
    for bad in ("0", "-2", "1.5", ""):
        xml = build([
            measure("1", note(4)),
            measure("2", note(bad) + note(4), ""),
        ])
        errors = audit_fail(xml)
        assert errors[0]["code"] == "MUSICXML_AUDIT_DURATION", bad
        assert errors[0]["index"] == 1


def test_missing_duration_element():
    xml = build([
        measure("1", note(4)),
        measure("2", note(None), ""),  # 非 grace 音符缺少 duration
    ])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_AUDIT_DURATION"


def test_invalid_backup_duration():
    xml = build([
        measure("1", note(4) + backup("x") + note(4, voice="2")),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_AUDIT_DURATION"


def test_negative_cursor():
    xml = build([
        measure("1", note(2) + backup(3) + note(3)),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_AUDIT_CURSOR"
    assert errors[0]["index"] == 0


def test_orphan_chord_at_measure_start():
    xml = build([
        measure("1", note(4)),
        measure("2", note(2, chord=True) + note(4), ""),
    ])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_AUDIT_CHORD"
    assert errors[0]["index"] == 1
    assert errors[0]["measure"] == "2"


def test_orphan_chord_other_voice():
    # chord 只继承**同声部**的前一普通音符
    xml = build([
        measure("1", note(2, voice="1") + note(2, chord=True, voice="2")
                + note(2, voice="1")),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_AUDIT_CHORD"


def test_chord_after_backup_still_attaches():
    # backup 不影响 chord 归属：继承前一同声部普通音符的起点
    xml = build([
        measure("1", note(2, voice="1") + backup(2)
                + note(2, voice="2") + note(2, chord=True, voice="1")
                + note(2, voice="1")),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    # v1：[0,2) 与和弦合并后 v1 游标到 2，note(2) → [2,4)；v2：[0,2) 缺 2~4
    assert [(i["kind"], i["voice"], i["start"], i["end"])
            for i in result["issues"]] == [("gap", "2", "2", "4")]


def test_first_error_per_measure_and_sorted():
    # 每个小节只报第一个硬错误；多个小节的错误按源小节排序
    attrs_no_div = ('<attributes><time><beats>4</beats>'
                    '<beat-type>4</beat-type></time></attributes>')
    xml = build([
        measure("1", note("x") + note(4), attrs_no_div),  # 非法 duration（先）
        measure("2", note(2, chord=True), ATTRS),          # 孤立 chord
    ])
    errors = audit_fail(xml)
    assert [e["code"] for e in errors] == [
        "MUSICXML_AUDIT_DURATION", "MUSICXML_AUDIT_CHORD"]
    assert [e["index"] for e in errors] == [0, 1]


# --------------------------------------------------------------------------
# 文件范围（与结构导入一致）
# --------------------------------------------------------------------------

def test_empty_content():
    errors = audit_fail("")
    assert errors[0]["code"] == "MUSICXML_EMPTY"


def test_parse_error():
    errors = audit_fail("<score-partwise>")
    assert errors[0]["code"] == "MUSICXML_PARSE"


def test_timewise_rejected():
    errors = audit_fail("<score-timewise></score-timewise>")
    assert errors[0]["code"] == "MUSICXML_FORMAT"


def test_multi_part_rejected():
    xml = """<?xml version="1.0"?>
<score-partwise>
  <part-list>
    <score-part id="P1"><part-name>A</part-name></score-part>
    <score-part id="P2"><part-name>B</part-name></score-part>
  </part-list>
  <part id="P1"></part><part id="P2"></part>
</score-partwise>"""
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_PARTS"


def test_measure_count():
    xml = build([measure("1", note(4))])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_MEASURE_COUNT"


def test_beat_type_must_be_four():
    attrs = ('<attributes><divisions>1</divisions>'
             '<time><beats>6</beats><beat-type>8</beat-type></time>'
             '</attributes>')
    xml = build([measure("1", note(6), attrs), measure("2", note(6), "")])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_TIME"


def test_first_measure_time_required():
    xml = build([measure("1", note(4),
                         '<attributes><divisions>1</divisions></attributes>'),
                 measure("2", note(4), "")])
    errors = audit_fail(xml)
    assert errors[0]["code"] == "MUSICXML_TIME"


# --------------------------------------------------------------------------
# HTTP 接口 /api/audit-musicxml
# --------------------------------------------------------------------------

def test_api_audit_pass():
    xml = build([measure("1", note(4)), measure("2", note(4), "")])
    resp = client.post("/api/audit-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["issue_count"] == 0
    assert body["result"]["measure_count"] == 2


def test_api_audit_issues():
    xml = build([measure("1", note(3)), measure("2", note(4), "")])
    resp = client.post("/api/audit-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200
    issue = resp.json()["result"]["issues"][0]
    assert issue["kind"] == "gap"
    assert issue["measure"] == "1"
    assert issue["voice"] == "1"
    assert (issue["start"], issue["end"]) == ("3", "4")


def test_api_audit_hard_error_422():
    xml = build([measure("1", note(2, chord=True)), measure("2", note(4), "")])
    resp = client.post("/api/audit-musicxml", json={"musicxml": xml})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"][0]["code"] == "MUSICXML_AUDIT_CHORD"
    assert body["errors"][0]["index"] == 0


def test_api_audit_invalid_json_400():
    resp = client.post("/api/audit-musicxml", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_api_audit_missing_field_400():
    resp = client.post("/api/audit-musicxml", json={"score": "x"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"
