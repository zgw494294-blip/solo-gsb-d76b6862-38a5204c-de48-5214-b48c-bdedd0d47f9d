"""乐谱反复展开引擎。

输入是顺序排列的 2~64 个小节，每个小节包含：

- number           唯一编号（字符串，最多 16 个非空白字符）
- beats            拍数（1~128 的整数）
- tempo            速度标记 BPM（40~240 的整数）；首小节必填，其余留空
                    表示沿用上一个速度标记（反复展开后的每次访问按来源小节
                    取得生效速度）
- left_repeat      该小节左边界是否带反复起点 ``|:``
- right_repeat     该小节右边界是否带反复终点 ``:|``
- right_closes     可选：该右边界闭合的**各反复起点行**（0 基下标）列表，
                    按内层→外层排列（至多 2 个）。仅“同线双终点”使用——
                    同一道物理小节线两侧各有一个 backward（一侧是内层第 2+
                    房子的次级终点，另一侧是更外层反复的主终点）时，转换后
                    两个终点落在同一录入行；省略或留空表示该行至多一个终点，
                    由 ``right_repeat`` 按栈匹配。手工录入只需勾选
                    ``right_repeat``，双终点仅由 MusicXML 导入产生。
- times            左侧反复起点的循环次数（2~4），仅当 left_repeat 为真时必填
- volta            替代结尾适用的遍次（1~4，可空）

记谱模型
========

反复起点与终点成对出现，按栈正确嵌套（同一个小节上的左、右标记构成单小节反复）。
一组反复体在第 1 次进入后，到达终点时跳回起点，直到完成 ``times`` 遍。
``volta = k`` 表示该小节仅在所属的**最内层反复**的第 ``k`` 遍被演奏，其余各遍跳过。

同一道物理小节线两侧可以各有一个 backward：一侧（线前小节的右 barline）是
内层第 2+ 房子的次级终点，另一侧（线后小节的左 barline）是更外层反复的主
终点。手工录入把两侧都落在线前小节这一行，用 ``right_closes`` 列出两个终点
各自的反复起点行（内层在前）。展开到这一边界时**先按房子状态机处理内层
次级终点（完成内层房子），再弹栈闭合外层反复**——与记号写在线的哪一侧
无关，只按反复归属决定先后。

替代结尾常见写法（``{1`` / ``{2`` 房子）是：

::

    m1  m2(m3 {1) m4(:| {2) m5 ── 第 1 遍 m1 m2 m3 m4；第 2 遍 m1 m2 m4 m5

即：

- 非最后一遍的房子在终点**之前**，跳过房子里的小节时，终点控制标记仍然生效；
- 最后一遍的房子接在终点**之后**（称为该反复的“尾部 volta”），出栈后仍按
  最终遍路径演奏。

因此静态归属分两种：

1. 小节位置位于某对反复括号内 → 归属于包含它的最深（最内层）反复；
2. 紧随某对反复终点之后、一串“只含 volta、不带任何反复边界标记”的连续小节 →
   归属给刚闭合的那对反复（外层计数上下文仍在）。

运行时维护反复栈，每帧记录循环遍数；跳到起点重新执行左边界时复用已有栈帧，
外层进入下一遍会重新打开内层反复，因此内层遍数自动从 1 重新开始。
"""

from __future__ import annotations

from typing import Any

MIN_MEASURES = 2
MAX_MEASURES = 64
MAX_VISITS = 512
MIN_TEMPO = 40
MAX_TEMPO = 240
# 解释器步数保护（合法结构远到不了；理论上每遍至少执行 1 个小节）。
MAX_STEPS = 200_000


def _err(code: str, message: str, index: int | None = None,
         measure: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "index": index,
        "measure": measure,
    }


# ---------------------------------------------------------------------------
# 1. 行校验与归一化
# ---------------------------------------------------------------------------

def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _as_int(value: Any) -> int | None:
    # bool 是 int 的子类，这里拒绝，避免 true 被当成 1。
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def normalize_rows(raw: Any) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """把原始 JSON 数据转成规整的小节列表；返回 ``(rows, errors)``。"""
    errors: list[dict[str, Any]] = []

    if not isinstance(raw, list):
        return None, [_err("INVALID_REQUEST", "请求体必须是小节对象组成的数组。")]
    if not (MIN_MEASURES <= len(raw) <= MAX_MEASURES):
        errors.append(_err(
            "MEASURE_COUNT",
            f"小节数量必须在 {MIN_MEASURES}~{MAX_MEASURES} 之间，当前为 {len(raw)}。",
        ))
        return None, errors

    rows: list[dict[str, Any]] = []
    seen_numbers: dict[str, int] = {}
    raw_count = len(raw) if isinstance(raw, list) else 0
    # 首小节速度标记是否缺失（格式错误已单独上报，不再叠加“必填”错误）。
    first_tempo_missing = False
    first_tempo_bad = False

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(_err("INVALID_ROW", "每一行必须是对象。", i))
            continue

        label = item.get("number")
        number = label if isinstance(label, str) else (
            str(label) if isinstance(label, int) and not isinstance(label, bool) else None
        )
        if number is None:
            errors.append(_err("NUMBER_REQUIRED", "小节编号缺失。", i))
            number = f"#{i + 1}"
        elif not number.strip():
            errors.append(_err("NUMBER_REQUIRED", "小节编号不能为空。", i))
            number = f"#{i + 1}"
        elif len(number.strip()) > 16:
            errors.append(_err("NUMBER_FORMAT", "小节编号最多 16 个字符。", i, number.strip()))
        else:
            number = number.strip()
            if number in seen_numbers:
                errors.append(_err(
                    "NUMBER_DUPLICATE",
                    f"小节编号 {number!r} 与第 {seen_numbers[number] + 1} 行重复。",
                    i, number,
                ))
            else:
                seen_numbers[number] = i

        beats = _as_int(item.get("beats"))
        if beats is None:
            errors.append(_err("BEATS_REQUIRED", "拍数必须是整数。", i, number))
            beats = 0
        elif not (1 <= beats <= 128):
            errors.append(_err("BEATS_RANGE", "拍数必须在 1~128 之间。", i, number))

        # tempo：速度标记 BPM。None 表示留空（沿用前一标记）。
        tempo_raw = item.get("tempo")
        tempo: int | None = None
        if tempo_raw is None or tempo_raw == "":
            if i == 0:
                first_tempo_missing = True
        else:
            tempo = _as_int(tempo_raw)
            if tempo is None or not (MIN_TEMPO <= tempo <= MAX_TEMPO):
                errors.append(_err(
                    "TEMPO_RANGE",
                    f"速度标记必须是 {MIN_TEMPO}~{MAX_TEMPO} 之间的整数（BPM）。",
                    i, number,
                ))
                tempo = None
                if i == 0:
                    first_tempo_bad = True

        left = _as_bool(item.get("left_repeat"))
        right = _as_bool(item.get("right_repeat"))
        if left is None:
            errors.append(_err("LEFT_REPEAT_FORMAT", "left_repeat 必须是布尔值。", i, number))
            left = False
        if right is None:
            errors.append(_err("RIGHT_REPEAT_FORMAT", "right_repeat 必须是布尔值。", i, number))
            right = False

        # right_closes：可选的“同线双终点”归属列表（各反复起点行，内层
        # → 外层，至多 2 个）。仅与 right_repeat 同时出现；不重复、不越界。
        # 与栈配对是否吻合（归属、嵌套顺序）由括号配对阶段校验。
        closes_raw = item.get("right_closes")
        right_closes: list[int] | None = None
        if closes_raw is not None:
            if not isinstance(closes_raw, list) or not closes_raw \
                    or len(closes_raw) > 2:
                errors.append(_err(
                    "RIGHT_CLOSES_FORMAT",
                    "right_closes 必须是 1~2 个反复起点行号组成的数组（同线双终点）。",
                    i, number))
                closes_raw = []
            else:
                parsed: list[int] = []
                bad = False
                for v in closes_raw:
                    iv = _as_int(v)
                    if iv is None or not (0 <= iv < raw_count):
                        bad = True
                        break
                    parsed.append(iv)
                if bad or len(set(parsed)) != len(parsed):
                    errors.append(_err(
                        "RIGHT_CLOSES_FORMAT",
                        "right_closes 中的行号必须是不重复的有效小节行号。",
                        i, number))
                else:
                    right_closes = parsed
            if not right:
                errors.append(_err(
                    "RIGHT_CLOSES_WITHOUT_RIGHT",
                    "只有勾选右反复终点的小节才能给出 right_closes。",
                    i, number))
                right_closes = None

        times_raw = item.get("times")
        times: int | None = None
        if times_raw is not None:
            times = _as_int(times_raw)
            if times is None:
                errors.append(_err("TIMES_FORMAT", "循环次数必须是 2~4 的整数。", i, number))
            elif not (2 <= times <= 4):
                errors.append(_err("TIMES_RANGE", "循环次数必须在 2~4 之间。", i, number))
                times = None
        if times is not None and not left:
            errors.append(_err(
                "TIMES_WITHOUT_LEFT",
                "只有带左侧反复起点的小节才能填写循环次数。",
                i, number,
            ))
        if left and times is None:
            errors.append(_err(
                "TIMES_REQUIRED",
                "带左侧反复起点的小节必须填写 2~4 的循环次数。",
                i, number,
            ))

        volta_raw = item.get("volta")
        volta: int | None = None
        if volta_raw is not None and volta_raw != "":
            volta = _as_int(volta_raw)
            if volta is None or not (1 <= volta <= 4):
                errors.append(_err("VOLTA_FORMAT",
                                   "替代结尾遍次必须是 1~4 的整数或留空。", i, number))
                volta = None

        rows.append({
            "number": number,
            "beats": beats if beats else 4,
            "tempo": tempo,
            "left_repeat": left,
            "right_repeat": right,
            "right_closes": right_closes,
            "times": times if left else (times if times is None else None),
            "volta": volta,
        })

    if first_tempo_missing and not first_tempo_bad:
        errors.append(_err(
            "TEMPO_FIRST_REQUIRED",
            f"第 1 小节必须填写速度标记（{MIN_TEMPO}~{MAX_TEMPO} BPM）。",
            0, rows[0]["number"],
        ))

    return rows, errors


# ---------------------------------------------------------------------------
# 2. 括号配对
# ---------------------------------------------------------------------------

def _match_brackets(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """扫描左/右边界标记，返回 ``(pairs, errors)``。

    pairs 每项： ``{id, open, close, closes, tail, times, depth}``。

    - ``close`` 为主终点（栈匹配），``closes`` 含主终点与多房子的次级终点；
    - ``tail`` 为主终点之后归属本反复的尾部小节（替代结尾房子）索引。

    尾部界定靠“期待房子编号”：主终点位于 ``{1`` 房子时，之后的连续小节必须
    依次是同一反复的 ``{2``、``{3``……房子（编号严格递增，可带次级终点），
    直到达到 ``times`` 号房子。遇到普通小节、左侧起点或不符合期待编号的小节，
    尾部立即结束——这样内层尾巴之后的外层 ``{1`` 房子不会被误吞，
    因为外层房子编号 1 与内层期待的 2 不一致。

    同一录入行可以同时是“内层第 2+ 房子的次级终点”与“更外层反复的主
    终点”（同线双终点，``right_closes`` 给出两个归属）：本行先由尾部
    状态机消费一个次级终点（不弹栈），剩余的终点再按栈弹栈闭合外层；
    该行 volta 属于内层房子，外层主终点不按本行 volta 判定。
    """
    pairs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    stack: list[int] = []  # pair id-1

    # 尾部房子状态机。状态：(pair_id, expect_house, house_open)
    # - house_open=False：期待编号为 expect_house 的小节开始新房子；
    # - house_open=True ：当前房子可由同编号小节延续，直到遇到右侧终点闭合。
    tail: tuple[int, int, bool] | None = None

    def tail_advance(tp: int, expect: int) -> tuple[int, int, bool] | None:
        return (tp, expect + 1, False) if expect < pairs[tp]["times"] else None

    for i, row in enumerate(rows):
        if row["left_repeat"]:
            pid = len(pairs)
            pairs.append({
                "id": pid + 1,
                "open": i,
                "close": -1,
                "closes": [],
                "tail": [],
                "times": row["times"] or 2,
                "depth": len(stack),
            })
            stack.append(pid)
            tail = None  # 新起点打断一切尾部序列

        v = row["volta"]
        # 本行右边界需要闭合的终点数；其中第一个可能是尾部房子的次级终点。
        close_count = len(row["right_closes"]) if row["right_closes"] else (
            1 if row["right_repeat"] else 0)
        main_pops = close_count  # 尾部次级消费一个后剩余的主终点数

        # 尾部房子状态机（有无右终点都要推进）：
        if tail is not None:
            tp, expect, house_open = tail
            if v == expect:
                pairs[tp]["tail"].append(i)
                if close_count:
                    pairs[tp]["closes"].append(i)
                    tail = tail_advance(tp, expect)
                    main_pops -= 1  # 内层房子次级终点：不弹栈
                else:
                    tail = (tp, expect, True)  # 房子可继续延伸
            elif v is not None and house_open and v == expect + 1:
                errors.append(_err(
                    "VOLTA_HOUSE_UNCLOSED",
                    f"第 {expect} 遍替代结尾房子缺少结束它的右侧反复终点 :|。",
                    i, row["number"],
                ))
                tail = None
            elif v != expect:
                # 普通小节或编号不符 → 尾部结束（本行终点全部按主终点处理）
                tail = None

        # 主终点：尾部次级消费之后剩余的闭合按栈弹栈。同线双终点时外层
        # 主终点是本行最后一个闭合；该行 volta 属于已消费的内层房子，
        # 外层主终点不按本行 volta 判定。
        for k in range(main_pops):
            is_outer_of_double = close_count == 2 and main_pops == 1 and k == 0
            if stack:
                pid = stack.pop()
                # 显式归属（同线双终点）必须与栈配对一致
                declared = row["right_closes"]
                if declared is not None:
                    want = declared[close_count - main_pops + k]
                    if pairs[pid]["open"] != want:
                        errors.append(_err(
                            "REPEAT_CLOSE_OWNER",
                            "右反复终点的归属（right_closes）与实际嵌套的反复"
                            "起点不一致（同线双终点必须内层在前、外层在后）。",
                            i, row["number"],
                        ))
                pairs[pid]["close"] = i
                pairs[pid]["closes"].append(i)
                if is_outer_of_double:
                    # 外层主终点与内层次级终点同线：本行 volta 属于内层
                    # 房子，外层尾部（若有）从下一行才开始，不在此行挂尾巴。
                    pass
                elif row["volta"] is None:
                    pass  # 普通主终点，无尾部房子
                elif row["volta"] == 1:
                    tail = tail_advance(pid, 1)
                else:
                    errors.append(_err(
                        "REPEAT_MAIN_CLOSE_VOLTA",
                        f"反复的主终点必须位于普通小节或第 1 遍房子（{{1）中，"
                        f"当前却在第 {row['volta']} 遍房子里；请把 :| 移到 {{1 房子末尾。",
                        i, row["number"],
                    ))
            else:
                errors.append(_err(
                    "REPEAT_UNMATCHED_CLOSE",
                    "右侧反复终点没有对应的左侧反复起点。",
                    i, row["number"],
                ))

        # 孤立 volta 小节（无左起点、不在尾部）交给归属校验。
        if v is not None and not row["left_repeat"]:
            pass

    for pid in stack:
        p = pairs[pid]
        errors.append(_err(
            "REPEAT_UNMATCHED_OPEN",
            "左侧反复起点没有对应的右侧反复终点（反复段必须成对且正确嵌套）。",
            p["open"], rows[p["open"]]["number"],
        ))

    # 尾部被提前打断或曲子结束：检查房子序列是否完整。
    for p in pairs:
        if p["close"] < 0:
            continue
        tail_houses = {rows[t]["volta"] for t in p["tail"]
                       if rows[t]["volta"] is not None}
        if rows[p["close"]]["volta"] == 1:
            missing = [h for h in range(2, p["times"] + 1) if h not in tail_houses]
            if missing:
                shown = "、".join(f"{{{h}}}" for h in missing)
                errors.append(_err(
                    "VOLTA_HOUSE_MISSING",
                    f"该反复共 {p['times']} 遍，主终点位于 {{1 房子，但缺少第 "
                    f"{shown} 遍的替代结尾房子。",
                    p["close"], rows[p["close"]]["number"],
                ))

    return pairs, errors


# ---------------------------------------------------------------------------
# 3. volta 归属
# ---------------------------------------------------------------------------

def _assign_volta_owners(rows: list[dict[str, Any]],
                         pairs: list[dict[str, Any]]) -> list[int | None]:
    """返回每个小节的最内层所属反复 id（1 基），无法归属为 None。

    归属来源两处：括号区间内的小节（内层覆盖外层），以及配对阶段记录的
    尾部房子小节（``pairs[i]["tail"]``）。
    """
    n = len(rows)
    owner: list[int | None] = [None] * n
    for p in pairs:
        for pos in range(p["open"], p["close"] + 1):
            owner[pos] = p["id"]  # pairs 按起点顺序创建，内层后写入即覆盖外层
    for p in pairs:
        for pos in p["tail"]:
            owner[pos] = p["id"]
    return owner


def _validate_volta(rows: list[dict[str, Any]], pairs: list[dict[str, Any]],
                    owner: list[int | None]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    times_by_id = {p["id"]: p["times"] for p in pairs}
    for i, row in enumerate(rows):
        v = row["volta"]
        if v is None:
            continue
        oid = owner[i]
        if oid is None:
            errors.append(_err(
                "VOLTA_OUTSIDE_REPEAT",
                "替代结尾只能出现在某对反复段内部，或紧随其终点的连续 volta 小节中。",
                i, row["number"],
            ))
            continue
        if not (1 <= v <= times_by_id[oid]):
            errors.append(_err(
                "VOLTA_PASS_INVALID",
                f"替代结尾遍次 {v} 超出最内层反复的有效遍次范围"
                f"（1~{times_by_id[oid]}）。",
                i, row["number"],
            ))
    return errors


# ---------------------------------------------------------------------------
# 4. 展开（栈式解释器）
# ---------------------------------------------------------------------------

def _pair_summary(pairs: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": p["id"],
            "depth": p["depth"],
            "times": p["times"],
            "open_index": p["open"],
            "close_index": p["close"],
            "secondary_close_indices": [c for c in p["closes"] if c != p["close"]],
            "open_measure": rows[p["open"]]["number"],
            "close_measure": rows[p["close"]]["number"],
        }
        for p in pairs
    ]


def expand(raw: Any, expect: list[str] | None = None,
           ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """完整流程：校验 → 配对 → volta 归属 → 展开。

    成功返回 ``(result, [])``；失败返回 ``(None, errors)``。

    ``expect`` 可选：目标演奏编号序列。给出后，展开一旦确定不可能得到该
    序列（本次访问编号与目标对应项不符、访问数超出目标长度，或最终访问
    数不足）即提前中止，返回 ``EXPECT_MISMATCH`` 错误。展开是确定性的，
    提前中止与“完整展开后再比对”结论完全一致，仅供反求等批量枚举场景
    剪枝；不改变任何校验与展开语义。
    """
    rows, errors = normalize_rows(raw)
    if errors:
        return None, errors
    assert rows is not None

    pairs, errors = _match_brackets(rows)
    if errors:
        return None, errors

    owner = _assign_volta_owners(rows, pairs)
    errors = _validate_volta(rows, pairs, owner)
    if errors:
        return None, errors

    n = len(rows)

    # 生效速度：首小节必填已在校验阶段保证；留空小节沿用最近一次显式标记。
    effective_tempos: list[int] = []
    current_tempo: int | None = None
    for row in rows:
        if row["tempo"] is not None:
            current_tempo = row["tempo"]
        # 首小节强制要求速度标记，校验失败会提前返回，故此处必非 None。
        assert current_tempo is not None
        effective_tempos.append(current_tempo)

    pair_at_open = {p["open"]: p for p in pairs}
    closes_at: dict[int, list[dict[str, Any]]] = {}  # 右终点小节 -> 闭合的
    # 反复（同线双终点时内层在前、外层在后），含次级终点
    for p in pairs:
        for c in p["closes"]:
            closes_at.setdefault(c, []).append(p)
    # pairs 按起点（外层先开）排列，这里统一改为按嵌套深度降序，保证
    # 同线双终点“先内层、后外层”。
    for cs in closes_at.values():
        cs.sort(key=lambda p: -p["depth"])
    times_by_id = {p["id"]: p["times"] for p in pairs}

    # 栈帧：[pair_id, pass_no]
    frames: list[list[int]] = []
    last_finished: list[int] | None = None  # 刚闭合的最内层反复 [id, final_pass]

    visits: list[dict[str, Any]] = []
    position = 0
    steps = 0
    total_beats = 0

    while position < n:
        steps += 1
        if steps > MAX_STEPS:
            return None, [_err(
                "EXPANSION_LIMIT",
                f"展开步数超过安全上限（{MAX_STEPS}），请检查反复结构。",
            )]

        row = rows[position]

        # --- 左边界：进入反复（跳回时该帧已在栈顶，不重复入栈） -------------
        if row["left_repeat"]:
            p = pair_at_open[position]
            if not frames or frames[-1][0] != p["id"]:
                frames.append([p["id"], 1])

        # --- 是否演奏本小节（volta 过滤） ---------------------------------
        oid = owner[position]
        play = True
        if oid is not None and row["volta"] is not None:
            if frames and frames[-1][0] == oid:
                play = frames[-1][1] == row["volta"]
            elif last_finished is not None and last_finished[0] == oid:
                # 尾部 volta：该反复已闭合，只在最终遍演奏。
                play = last_finished[1] == row["volta"]
            else:
                play = False

        if play:
            if expect is not None:
                gi = len(visits)
                if gi >= len(expect) or row["number"] != expect[gi]:
                    return None, [_err(
                        "EXPECT_MISMATCH",
                        "展开序列与目标演奏编号序列不符。",
                        position, row["number"],
                    )]
            if len(visits) >= MAX_VISITS:
                return None, [_err(
                    "TOO_MANY_VISITS",
                    f"展开后的访问次数超过上限 {MAX_VISITS}，请减少循环次数或嵌套层数。",
                    position, row["number"],
                )]

            path = [
                {"repeat_id": fid, "pass": pno,
                 "times": times_by_id[fid]}
                for fid, pno in frames
            ]
            if oid is not None and row["volta"] is not None and (
                    not frames or frames[-1][0] != oid):
                # 尾部 volta：所属反复已出栈，把最终遍路径补在末尾。
                path.append({"repeat_id": oid,
                             "pass": last_finished[1] if last_finished else times_by_id[oid],
                             "times": times_by_id[oid],
                             "tail": True})

            total_beats += row["beats"]
            visits.append({
                "global_index": len(visits) + 1,
                "source_index": position,
                "source_measure": row["number"],
                "beats": row["beats"],
                # 展开后的每次访问按来源小节取得生效速度
                "tempo": effective_tempos[position],
                "tempo_marked": row["tempo"] is not None,
                "volta": row["volta"],
                "cumulative_beats": total_beats,
                "path": path,
            })

        # --- 右边界 -------------------------------------------------------
        # 普通小节上的终点：每遍都生效；
        # volta 房子 k 上的终点：仅在第 k 遍生效（非末遍跳回），
        #   最终遍若走到“被跳过的房子终点”则直接退出该反复（流向后续房子）；
        # 其余遍惰性放行。
        # 同线双终点：该边界先处理内层第 2+ 房子（次级终点），再处理外层
        # 反复主终点。本行 volta 属于内层房子，只对内层终点生效；外层主
        # 终点按普通终点处理。内层非最终遍在该边界跳回后，栈顶仍是内层
        # 帧，外层主终点本次**延后**不动作；内层最终遍再次到达本边界时
        # 内层已出栈、外层帧成为栈顶，外层主终点才在此闭合。是否延后只
        # 看当前栈帧即可区分这两种到达，无需额外状态。
        jump_to: int | None = None
        if row["right_repeat"]:
            close_pairs = closes_at.get(position, [])
            house = row["volta"]

            if len(close_pairs) == 2:
                inner_pair, outer_pair = close_pairs

                # 1) 内层房子终点（本行 volta 属于它）
                if frames and frames[-1][0] == inner_pair["id"]:
                    current_pass = frames[-1][1]
                    if house is None:
                        acts = True
                    elif play:
                        acts = True
                    else:
                        acts = current_pass == inner_pair["times"]
                    if acts:
                        if current_pass < inner_pair["times"]:
                            frames[-1][1] += 1
                            jump_to = inner_pair["open"]  # 外层主终点随之延后
                        else:
                            last_finished = [inner_pair["id"], current_pass]
                            frames.pop()

                # 2) 外层主终点（本行 volta 不属于它，按普通终点处理）：
                #    仅当内层此刻不在栈顶（已最终遍出栈）、外层帧在栈顶
                #    时闭合；内层刚跳回（栈顶仍是内层）时延后不动作。
                if jump_to is None and frames \
                        and frames[-1][0] == outer_pair["id"]:
                    current_pass = frames[-1][1]
                    if current_pass < outer_pair["times"]:
                        frames[-1][1] += 1
                        jump_to = outer_pair["open"]
                    else:
                        last_finished = [outer_pair["id"], current_pass]
                        frames.pop()
            else:
                # 单终点：原有语义（普通 / 房子主终点，含最终遍惰性退出）。
                for p in close_pairs:
                    if not frames or frames[-1][0] != p["id"]:
                        continue
                    current_pass = frames[-1][1]
                    if house is None:
                        acts = True
                    elif play:
                        acts = True
                    else:
                        acts = current_pass == p["times"]
                    if acts:
                        if current_pass < p["times"]:
                            frames[-1][1] += 1
                            jump_to = p["open"]
                        else:
                            last_finished = [p["id"], current_pass]
                            frames.pop()

        if jump_to is not None:
            position = jump_to
        else:
            position += 1

    if expect is not None and len(visits) != len(expect):
        return None, [_err(
            "EXPECT_MISMATCH",
            "展开序列比目标演奏编号序列短。",
        )]

    result = {
        "measure_count": n,
        "visit_count": len(visits),
        "total_beats": total_beats,
        "max_visits": MAX_VISITS,
        "tempo_range": [MIN_TEMPO, MAX_TEMPO],
        # 每个来源小节的生效速度（含留空沿用后的解析值）
        "tempos": [
            {"source_index": i, "marked": rows[i]["tempo"] is not None,
             "tempo": t}
            for i, t in enumerate(effective_tempos)
        ],
        "repeats": _pair_summary(pairs, rows),
        "visits": visits,
    }
    return result, []
