"""MusicXML 小节时值审计测试（模块与 /api/audit-musicxml）。"""

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

ATTRS_44 = (
    '<attributes><divisions>1</divisions>'
    '<time><beats>4</beats><beat-type>4</beat-type></time></attributes>'
)


def measure(num, inner="", attrs=ATTRS_44):
    return f'<measure number="{num}">{attrs}{inner}</measure>'


def build(measures):
    return HEADER.format(measures="".join(measures))


def note(dur, voice=None, chord=False, grace=False, rest=False):
    parts = ["<note>"]
    if rest:
        parts.append("<rest/>")
    else:
        parts.append("<pitch><step>C</step><octave>4</octave></pitch>")
    if grace:
        parts.append("<grace/>")
    if chord:
        parts.append("<chord/>")
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


def voice_of(result, index, voice):
    for v in result["measures"][index]["voices"]:
        if v["voice"] == voice:
            return v
    raise AssertionError(f"voice {voice} not found: "
                         f"{result['measures'][index]['voices']}")


# --------------------------------------------------------------------------
# 通过路径
# --------------------------------------------------------------------------

def test_plain_whole_notes_pass():
    xml = build([
        measure("1", note(4)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is True
    assert result["issue_count"] == 0
    assert result["measure_count"] == 2
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [{"start": "0", "end": "4"}]
    assert v["missing"] == [] and v["overlaps"] == [] and v["overflows"] == []
    assert result["measures"][1]["divisions"] == 1  # divisions 跨节沿用


def test_rational_positions_with_divisions():
    # divisions=2：八分音符拍位为 1/2 步进
    attrs = ('<attributes><divisions>2</divisions>'
             '<time><beats>2</beats><beat-type>4</beat-type></time>'
             '</attributes>')
    xml = build([
        measure("1", note(1) + note(1) + note(2), attrs),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is True
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [
        {"start": "0", "end": "1/2"},
        {"start": "1/2", "end": "1"},
        {"start": "1", "end": "2"},
    ]


def test_chord_merges_into_one_interval():
    # 和弦不推进游标：四分 + 和弦四分 + 四分 → [0,1] [1,2]
    attrs = ('<attributes><divisions>1</divisions>'
             '<time><beats>2</beats><beat-type>4</beat-type></time>'
             '</attributes>')
    xml = build([
        measure("1", note(1) + note(1, chord=True) + note(1), attrs),
        measure("2", note(2), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is True
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [
        {"start": "0", "end": "1"},
        {"start": "1", "end": "2"},
    ]


def test_chord_merged_end_takes_max():
    # 和弦音时值更长：合并后终点取最大
    xml = build([
        measure("1", note(2) + note(4, chord=True)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [{"start": "0", "end": "4"}]
    assert result["passed"] is True


def test_grace_notes_take_no_time():
    # 装饰音不占时、不要求 duration，也不影响和弦继承
    xml = build([
        measure("1", note(None, grace=True) + note(2)
                + note(None, grace=True, chord=True)
                + note(2, chord=True) + note(2)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is True
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [
        {"start": "0", "end": "2"},
        {"start": "2", "end": "4"},
    ]


def test_multi_voice_both_full_pass():
    # 声部 2 经 backup 从头开始，两声部各自填满
    xml = build([
        measure("1", note(4) + backup(4) + note(4, voice="2")),
        measure("2", note(4) + backup(4) + note(4, voice="2"), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is True
    v1 = voice_of(result, 0, "1")
    v2 = voice_of(result, 0, "2")
    assert v1["intervals"] == [{"start": "0", "end": "4"}]
    assert v2["intervals"] == [{"start": "0", "end": "4"}]


def test_mid_measure_divisions_change():
    # divisions 中途变更：之后的 duration 按新值解释
    inner = (
        note(1)
        + '<attributes><divisions>2</divisions></attributes>'
        + note(2) + note(4)
    )
    xml = build([measure("1", inner), measure("2", note(8), "")])
    result = audit_ok(xml)
    assert result["passed"] is True
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [
        {"start": "0", "end": "1"},
        {"start": "1", "end": "2"},
        {"start": "2", "end": "4"},
    ]
    assert result["measures"][0]["divisions"] == 2
    assert result["measures"][1]["divisions"] == 2  # 沿用到下一节


def test_tempo_and_repeats_are_ignored():
    # 审计只关心时值：无速度标记、反复未配对也能审计通过
    # （同样的文件不能通过结构导入，但时值审计独立可用）
    xml = build([
        measure("1", '<barline location="left">'
                '<repeat direction="forward"/></barline>' + note(4)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is True


def test_rest_counts_as_occupied():
    xml = build([
        measure("1", note(2) + note(2, rest=True)),
        measure("2", note(4, rest=True), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is True


# --------------------------------------------------------------------------
# 缺拍 / 非和弦重叠 / 越过小节边界
# --------------------------------------------------------------------------

def test_missing_beats_reported():
    xml = build([
        measure("1", note(2)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is False
    assert result["issue_count"] == 1
    v = voice_of(result, 0, "1")
    assert v["missing"] == [{"start": "2", "end": "4"}]


def test_forward_gap_counts_as_missing():
    # forward 只移动游标、不计入占用：跳过的区域显示为缺拍
    xml = build([
        measure("1", note(2) + forward(1) + note(1)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [
        {"start": "0", "end": "2"},
        {"start": "3", "end": "4"},
    ]
    assert v["missing"] == [{"start": "2", "end": "3"}]


def test_empty_measure_is_fully_missing():
    xml = build([
        measure("1", note(4)),
        measure("2", "", ""),  # 整节无音符：声部 1 全节缺拍
    ])
    result = audit_ok(xml)
    assert result["passed"] is False
    v = voice_of(result, 1, "1")
    assert v["missing"] == [{"start": "0", "end": "4"}]
    assert v["intervals"] == []


def test_overflow_beyond_measure_boundary():
    xml = build([
        measure("1", note(4) + note(1)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["passed"] is False
    v = voice_of(result, 0, "1")
    assert v["overflows"] == [{"start": "4", "end": "5"}]
    assert v["missing"] == []  # 已铺满，只是越界


def test_non_chord_overlap_reported():
    # backup 后同声部重叠：非和弦重叠
    xml = build([
        measure("1", note(4) + backup(2) + note(2)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    v = voice_of(result, 0, "1")
    assert v["overlaps"] == [{"start": "2", "end": "4"}]
    assert v["missing"] == []


def test_same_start_via_backup_is_merged_not_overlap():
    # backup 回到同一起点的同声部音符按同起点合并（与和弦同规则），
    # 不算非和弦重叠
    xml = build([
        measure("1", note(4) + backup(4) + note(4)),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    v = voice_of(result, 0, "1")
    assert v["intervals"] == [{"start": "0", "end": "4"}]
    assert v["overlaps"] == []
    assert result["passed"] is True


def test_per_voice_findings_are_independent():
    # 声部 1 填满，声部 2 缺拍：只列声部 2
    xml = build([
        measure("1", note(4) + backup(4) + note(2, voice="2")),
        measure("2", note(4), ""),
    ])
    result = audit_ok(xml)
    assert result["issue_count"] == 1
    v1 = voice_of(result, 0, "1")
    v2 = voice_of(result, 0, "2")
    assert v1["missing"] == []
    assert v2["missing"] == [{"start": "2", "end": "4"}]


def test_rational_missing_and_overflow():
    # divisions=2 下的分数拍位：缺拍与越界都用有理数表示
    attrs = ('<attributes><divisions>2</divisions>'
             '<time><beats>2</beats><beat-type>4</beat-type></time>'
             '</attributes>')
    xml = build([
        measure("1", note(1) + note(4), attrs),   # [0,1/2] [1/2,5/2] → 越界
        measure("2", note(3), ""),                # [0,3/2] → 缺 3/2–2
    ])
    result = audit_ok(xml)
    v0 = voice_of(result, 0, "1")
    assert v0["overflows"] == [{"start": "1/2", "end": "5/2"}]
    v1 = voice_of(result, 1, "1")
    assert v1["missing"] == [{"start": "3/2", "end": "2"}]


# --------------------------------------------------------------------------
# 源小节报错（不显示通过）
# --------------------------------------------------------------------------

def test_missing_divisions_error_located():
    attrs = '<attributes><time><beats>4</beats><beat-type>4</beat-type>' \
            '</time></attributes>'
    xml = build([
        measure("1", note(4), attrs),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_DIVISIONS" and e["index"] == 0
               and e["measure"] == "1" for e in errors)


def test_invalid_divisions_value():
    attrs = ('<attributes><divisions>1.5</divisions>'
             '<time><beats>4</beats><beat-type>4</beat-type></time>'
             '</attributes>')
    xml = build([measure("1", note(4), attrs), measure("2", note(4), "")])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_DIVISIONS" for e in errors)


def test_invalid_duration_error():
    xml = build([
        measure("1", note(2) + note("x")),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_DURATION" and e["index"] == 0
               for e in errors)


def test_zero_duration_error():
    xml = build([measure("1", note(0)), measure("2", note(4), "")])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_DURATION" for e in errors)


def test_backup_to_negative_cursor_error():
    xml = build([
        measure("1", note(2) + backup(3)),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_CURSOR" and e["index"] == 0
               for e in errors)


def test_orphan_chord_error():
    # 小节内该声部此前没有普通音符：孤立 chord
    xml = build([
        measure("1", note(2, chord=True) + note(4)),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_CHORD" and e["index"] == 0
               for e in errors)


def test_chord_does_not_carry_across_measures():
    # 上一小节的普通音符不能继承到下一小节
    xml = build([
        measure("1", note(4)),
        measure("2", note(4, chord=True), ""),
    ])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_CHORD" and e["index"] == 1
               for e in errors)


def test_chord_per_voice_independent():
    # 声部 2 的 chord 不能继承声部 1 的普通音符
    xml = build([
        measure("1", note(4) + note(4, voice="2", chord=True)),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_AUDIT_CHORD" for e in errors)


def test_duplicate_errors_collapsed_per_measure():
    # 同一小节多个非法 duration 只报一次
    xml = build([
        measure("1", note("a") + note("b") + note(None)),
        measure("2", note(4), ""),
    ])
    errors = audit_fail(xml)
    dur_errors = [e for e in errors if e["code"] == "MUSICXML_AUDIT_DURATION"]
    assert len(dur_errors) == 1


# --------------------------------------------------------------------------
# 形状 / 拍号（沿用结构导入规则）
# --------------------------------------------------------------------------

def test_shape_errors():
    assert audit_fail("")[0]["code"] == "MUSICXML_EMPTY"
    assert audit_fail("<score-partwise>")[0]["code"] == "MUSICXML_PARSE"
    assert audit_fail("<score-timewise/>")[0]["code"] == "MUSICXML_FORMAT"
    assert audit_fail("<opus/>")[0]["code"] == "MUSICXML_FORMAT"

    two_parts = """<?xml version="1.0"?>
    <score-partwise>
      <part-list>
        <score-part id="P1"><part-name>A</part-name></score-part>
        <score-part id="P2"><part-name>B</part-name></score-part>
      </part-list>
      <part id="P1"><measure number="1"/></part>
      <part id="P2"><measure number="1"/></part>
    </score-partwise>"""
    assert audit_fail(two_parts)[0]["code"] == "MUSICXML_PARTS"

    one_measure = build([measure("1", note(4))])
    assert audit_fail(one_measure)[0]["code"] == "MUSICXML_MEASURE_COUNT"


def test_time_signature_required_and_denominator_four():
    no_time = '<attributes><divisions>1</divisions></attributes>'
    xml = build([measure("1", note(4), no_time), measure("2", note(4), "")])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_TIME" and e["index"] == 0
               for e in errors)

    eight = ('<attributes><divisions>1</divisions>'
             '<time><beats>6</beats><beat-type>8</beat-type></time>'
             '</attributes>')
    xml = build([measure("1", note(4), eight), measure("2", note(4), "")])
    errors = audit_fail(xml)
    assert any(e["code"] == "MUSICXML_TIME" for e in errors)


def test_time_signature_change_mid_score():
    # 中途变拍号：之后小节按新容量审计
    attrs_24 = ('<attributes>'
                '<time><beats>2</beats><beat-type>4</beat-type></time>'
                '</attributes>')
    xml = build([
        measure("1", note(4)),
        measure("2", note(2), attrs_24),
        measure("3", note(4), ""),   # 沿用 2/4：全音符越界
    ])
    result = audit_ok(xml)
    assert result["measures"][1]["beats"] == 2
    v = voice_of(result, 2, "1")
    assert v["overflows"] == [{"start": "0", "end": "4"}]


# --------------------------------------------------------------------------
# /api/audit-musicxml
# --------------------------------------------------------------------------

def test_api_audit_ok_passed():
    xml = build([measure("1", note(4)), measure("2", note(4), "")])
    resp = client.post("/api/audit-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["passed"] is True
    assert body["result"]["issue_count"] == 0


def test_api_audit_ok_with_findings():
    xml = build([measure("1", note(2)), measure("2", note(4), "")])
    resp = client.post("/api/audit-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["passed"] is False
    m0 = result["measures"][0]
    assert m0["number"] == "1" and m0["beats"] == 4
    assert m0["voices"][0]["missing"] == [{"start": "2", "end": "4"}]


def test_api_audit_422_located_at_source_measure():
    xml = build([
        measure("1", note(4)),
        measure("2", note(4, chord=True), ""),
    ])
    resp = client.post("/api/audit-musicxml", json={"musicxml": xml})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert any(e["code"] == "MUSICXML_AUDIT_CHORD" and e["index"] == 1
               and e["measure"] == "2" for e in body["errors"])


def test_api_audit_invalid_json_400():
    resp = client.post("/api/audit-musicxml", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_JSON"


def test_api_audit_missing_field_400():
    resp = client.post("/api/audit-musicxml", json={"score": "x"})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["code"] == "INVALID_REQUEST"


def test_api_audit_does_not_require_tempo():
    # 结构导入要求首节速度标记；时值审计不需要
    xml = build([measure("1", note(4)), measure("2", note(4), "")])
    resp = client.post("/api/import-musicxml", json={"musicxml": xml})
    assert resp.status_code == 422  # 导入要求速度
    resp = client.post("/api/audit-musicxml", json={"musicxml": xml})
    assert resp.status_code == 200
    assert resp.json()["result"]["passed"] is True
