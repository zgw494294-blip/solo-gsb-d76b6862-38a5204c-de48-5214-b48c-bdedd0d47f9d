"""右反复终点纠错：只切换各行的右反复终点 ``:|``，在预算内找回合法谱面。

约束
====

针对当前录入谱面，保持小节顺序、编号、拍数、BPM、左反复及次数、替代结尾
不变，**只允许切换各行的右反复终点**。

流程
====

1. 先复用现有行级校验（:func:`normalize_rows`）：行级不通过直接报错，
   不进入搜索；
2. 原谱走现有**完整校验与展开**（含 512 次访问上限）：能正常展开时
   返回 ``already_valid = True``，由前端提示“无需纠错”；
3. 否则在**最多 4 处切换**内寻找通过现有完整校验、且演奏访问数不超过
   512 的谱面。按切换数从小到大逐层枚举（k = 1…4），同一切换数内按
   改动行号序列字典序排列，每层只取最先找到的前 5 个方案。

剪枝
====

不遍历 64 行的全部 2^64 种右标记组合，DFS 逐行扫描时增量维护与
``_match_brackets`` 完全一致的配对状态，按两类依据剪枝：

- **改动预算**：剩余行数不足以放下剩余切换数时剪枝；切换分支在
  “保留”分支之前尝试，使方案天然按改动行号序列字典序产出；
- **反复配对状态**：
  - 已经发生的配对错误（多出的右终点 ``REPEAT_UNMATCHED_CLOSE``、
    主终点落在 {2+ 房子 ``REPEAT_MAIN_CLOSE_VOLTA``、房子未闭合
    ``VOLTA_HOUSE_UNCLOSED``、volta 无承载反复 / 遍次越界）不可能被
    后续行的切换修复，立即剪枝；
  - 尾部房子序列被提前打断且尚有未出现的房子时，缺房子错误已成定局，
    立即剪枝；
  - 栈中未闭合起点数加后缀左起点数超过剩余行数时，没有足够的行容纳
    必需的右终点，立即剪枝；
  - DFS 同时**精确累计**已确定的访问数（非 volta 小节按嵌套栈各层
    times 连乘，volta 房子小节在所属反复上只奏一次），一旦超过
    512 立即剪枝——深嵌套结构在进入右终点块几步内即被剪光，
    只有计数不超过 512 的候选才在叶子上跑完整 ``expand()`` 做最终
    校验（配对 / 归属 / 展开 / 512 全套现有规则）。
"""

from __future__ import annotations

from typing import Any

from .expander import MAX_VISITS, expand, normalize_rows

MAX_TOGGLES = 4
MAX_SOLUTIONS = 5


class _Fatal(Exception):
    """该分支已产生不可能被后续切换修复的错误（配对 / volta / 访问上限）。"""


def _err(code: str, message: str, index: int | None = None,
         measure: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "index": index,
        "measure": measure,
    }


def _houses_complete(fr: dict[str, Any]) -> bool:
    """该反复主终点位于 {1 房子时，{2…{times} 房子是否均已出现。"""
    return all(h in fr["houses"] for h in range(2, fr["times"] + 1))


def _search_level(rows: list[dict[str, Any]], k: int,
                  ) -> tuple[list[dict[str, Any]], int]:
    """枚举恰好切换 ``k`` 处右终点的方案（字典序），最多取前 5 个。

    返回 ``(solutions, expanded_count)``；``expanded_count`` 为真正跑过
    完整 :func:`expand` 的候选数（配对 / 归属 / 计数剪枝不计入）。
    """
    n = len(rows)

    # 后缀左起点数：用于“剩余行是否容得下必需右终点”的可行性剪枝。
    opens_suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        opens_suffix[i] = (
            opens_suffix[i + 1] + (1 if rows[i]["left_repeat"] else 0))

    # 增量状态（配对部分镜像 app.expander._match_brackets）：
    # - frames：栈中帧 {"times", "open"}；
    # - done  ：已闭合帧 {"times", "open", "close", "houses", "tail_indices"}；
    # - tail  ：尾部房子状态 (frame, expect_house, house_open)；
    # - prod  ：当前栈中各帧 times 的乘积；lower 为已处理行的精确访问数。
    #
    # 本搜索只切换右终点、不能编辑同线双终点的归属（right_closes），故
    # 统一按“每行至多一个右终点”的单终点表示建模与复核：导入产生的
    # right_closes 在副本上清空，只保留 right_repeat。
    state: dict[str, Any] = {
        "frames": [], "done": [], "tail": None, "prod": 1, "lower": 0}
    chosen: list[int] = []
    found: list[dict[str, Any]] = []
    expanded = 0
    trial = [dict(r) for r in rows]
    for tr in trial:
        tr["right_closes"] = None

    def add_visit(factor: int) -> None:
        state["lower"] += factor
        if state["lower"] > MAX_VISITS:
            raise _Fatal

    def step(i: int, right_on: bool,
             added: list[tuple[dict[str, Any], int, int]]) -> None:
        """按右终点 ``right_on`` 处理第 i 行；对已存在帧的房子修改登记到
        ``added`` 以便回滚。

        配对、volta 归属或访问数出现不可挽回的问题时抛 :class:`_Fatal`
        （抛出前的状态修改同样登记完整，调用方可直接回滚）。

        除配对状态外，还精确累计本行演奏访问数（与展开引擎一致）：非 volta
        小节按当前反复栈各层 times 连乘；volta 小节在所属最内层反复上只奏
        一次，该层因子取 1（体内 volta：除去栈顶；尾部房子 volta：所属帧已
        出栈，当前栈乘积即正确因子）。所有状态变更由调用方凭快照回滚。
        """
        row = rows[i]
        volta = row["volta"]

        # 1) 左起点：入栈（乘积在处理本行前生效，起点行也属于该反复），
        #    并打断一切进行中的尾部房子序列。
        if row["left_repeat"]:
            tail = state["tail"]
            if tail is not None and not _houses_complete(tail[0]):
                raise _Fatal  # 缺房子错误已成定局
            fr = {"times": row["times"], "open": i}
            state["frames"].append(fr)
            state["prod"] *= fr["times"]
            state["tail"] = None

        # 2) 尾部房子状态机（与 _match_brackets 同构）。
        in_tail_house = False
        tail = state["tail"]
        if tail is not None:
            fr, expect, house_open = tail
            if volta == expect:
                fr["houses"].add(volta)
                fr["tail_indices"].append(i)
                added.append((fr, volta, i))
                in_tail_house = True
                # 尾部 volta 小节：所属反复已出栈，访问因子即外层栈乘积
                add_visit(state["prod"])
                if right_on:
                    state["tail"] = (
                        (fr, expect + 1, False)
                        if expect < fr["times"] else None)
                else:
                    state["tail"] = (fr, expect, True)  # 房子可跨小节延伸
            elif volta is not None and house_open and volta == expect + 1:
                raise _Fatal  # VOLTA_HOUSE_UNCLOSED
            elif volta != expect:
                # 期待 {k} 时遇到普通小节（house_open 或 v=None）→ 尾部结束；
                # 编号不符且房子未开（v 为其他 volta）同样结束。
                state["tail"] = None
                if not _houses_complete(fr):
                    raise _Fatal  # 尾部被提前打断，缺房子不可挽回

        # 3) 体内 volta 必须由当前栈顶反复承载，遍次不得超出其 times：
        #    栈为空即 VOLTA_OUTSIDE_REPEAT，越界即 VOLTA_PASS_INVALID。
        if not in_tail_house and volta is not None:
            if not state["frames"] or volta > state["frames"][-1]["times"]:
                raise _Fatal

        # 4) 本行访问贡献（尾部房子行已在第 2 步累计）。
        if not in_tail_house:
            if volta is not None:
                add_visit(state["prod"] // state["frames"][-1]["times"])
            else:
                add_visit(state["prod"])

        # 5) 右终点：尾部房子行上的是次级终点（不配对、不弹栈，状态机已在
        #    第 2 步推进期待房子编号）；否则按栈匹配弹栈。
        if right_on and not in_tail_house:
            frames = state["frames"]
            if not frames:
                raise _Fatal  # REPEAT_UNMATCHED_CLOSE
            top = frames.pop()
            state["prod"] //= top["times"]
            fr = {
                "times": top["times"],
                "open": top["open"],
                "close": i,
                "houses": {1} if volta == 1 else set(),
                "tail_indices": [],
            }
            state["done"].append(fr)
            if volta is None:
                pass  # 普通主终点
            elif volta == 1:
                state["tail"] = (
                    (fr, 2, False) if fr["times"] > 1 else None)
            else:
                raise _Fatal  # REPEAT_MAIN_CLOSE_VOLTA
        return

    def snapshot_state() -> tuple[list[dict[str, Any]], int, Any, int, int]:
        """保存配对 / 计数状态快照。

        - 栈中帧对象创建后不会被原地修改（只入栈 / 出栈），浅拷贝帧列表
          即可在回滚时恢复被弹走的帧，其 times 乘积（prod）单独保存；
        - done 中新闭合的帧都是本子树新入栈的对象，回滚按长度截断丢弃；
        - tail 是不可变元组，直接保存引用；其引用的已存在帧上的
          houses / tail_indices 修改登记在 ``added`` 中逐项撤销。
        """
        return (list(state["frames"]), len(state["done"]), state["tail"],
                state["prod"], state["lower"])

    def rollback(snapshot: tuple[list[dict[str, Any]], int, Any, int, int],
                 added: list[tuple[dict[str, Any], int, int]]) -> None:
        saved_frames, nd, old_tail, prod, lower = snapshot
        for fr, house, idx in added:
            fr["houses"].discard(house)
            fr["tail_indices"].remove(idx)
        del state["done"][nd:]
        state["frames"] = saved_frames
        state["tail"] = old_tail
        state["prod"] = prod
        state["lower"] = lower

    def leaf_expand() -> dict[str, Any] | None:
        nonlocal expanded
        toggle_set = set(chosen)
        for i, r in enumerate(rows):
            trial[i]["right_repeat"] = (
                not r["right_repeat"] if i in toggle_set else r["right_repeat"])
        expanded += 1
        # 配对、volta 归属与访问上限已在 DFS 中增量保证；expand 仍执行
        # 同一套完整规则作为最终权威（展开步数、512 中止等全部复用）。
        result, errors = expand(trial)
        return None if errors else result

    def dfs(i: int, budget: int) -> None:
        if len(found) >= MAX_SOLUTIONS:
            return
        remaining = n - i
        if budget > remaining:
            return  # 剩余行放不下剩余切换数
        # 配对可行性剪枝：最终必须闭合的左起点 = 当前栈中的 d 个 + 后缀还会
        # 出现的 opens_suffix[i] 个。闭合它们的右终点只能落在剩余这些行里；
        # 每个剩余行至多提供一个右终点（左起点行也可自带右终点，形成单小节
        # 反复），必需终点数超过剩余行数即无解。
        if len(state["frames"]) + opens_suffix[i] > remaining:
            return

        if i == n:
            if budget or state["frames"]:
                return
            # 曲末仍处于尾部房子状态：只有 {2…{times} 房子均已出现才合法
            #（{times} 房子允许一直延伸到曲末，不必再放次级终点）；
            # 否则对应 _match_brackets 收尾阶段的 VOLTA_HOUSE_MISSING。
            if state["tail"] is not None and not _houses_complete(
                    state["tail"][0]):
                return
            result = leaf_expand()
            if result is None:
                return
            changes = [
                {
                    "index": idx,
                    "measure": rows[idx]["number"],
                    # 原无右终点 → 新增；原有 → 删除
                    "action": "remove" if rows[idx]["right_repeat"] else "add",
                }
                for idx in chosen
            ]
            found.append({
                "toggles": k,
                "changes": changes,
                "visit_count": result["visit_count"],
            })
            return

        # 字典序：先尝试“在本行切换”，再尝试“保留本行”。
        if budget > 0:
            snapshot = snapshot_state()
            added_t: list[tuple[dict[str, Any], int, int]] = []
            try:
                step(i, not rows[i]["right_repeat"], added_t)
            except _Fatal:
                pass
            else:
                chosen.append(i)
                dfs(i + 1, budget - 1)
                chosen.pop()
            rollback(snapshot, added_t)
        snapshot = snapshot_state()
        added_k: list[tuple[dict[str, Any], int, int]] = []
        try:
            step(i, bool(rows[i]["right_repeat"]), added_k)
        except _Fatal:
            pass
        else:
            dfs(i + 1, budget)
        rollback(snapshot, added_k)

    dfs(0, k)
    return found, expanded


def fix_right_repeats(raw: dict[str, Any],
                      ) -> tuple[dict[str, Any] | None,
                                 dict[str, Any] | None]:
    """纠错入口。成功返回 ``(result, None)``；行级校验失败返回
    ``(None, {"score_errors": [...]})``。

    成功结果：
    - 原谱可正常展开：``already_valid = True``（无需纠错）；
    - 否则：``already_valid = False``，``solutions`` 为切换数最少的前 5
      个方案（同数按改动行号序列字典序），为空表示 4 处切换内无方案。
    """
    measures = raw["measures"]

    # 1) 先复用现有行级校验
    if not isinstance(measures, list):
        return None, {"score_errors": [_err(
            "INVALID_REQUEST", "measures 必须是小节对象组成的数组。")]}
    rows, score_errors = normalize_rows(measures)
    if score_errors:
        return None, {"score_errors": score_errors}
    assert rows is not None

    # 2) 原谱完整校验与展开（含 512 上限）：合法即无需纠错
    original, original_errors = expand(rows)
    if not original_errors:
        assert original is not None
        return {
            "already_valid": True,
            "measure_count": len(rows),
            "visit_count": original["visit_count"],
            "max_visits": MAX_VISITS,
            "max_toggles": MAX_TOGGLES,
            "solutions": [],
            "solution_count": 0,
            "expanded_candidates": 0,
        }, None

    # 3) 按切换数 1…4 逐层枚举；找到方案的第一层即最少切换数层
    solutions: list[dict[str, Any]] = []
    minimal_toggles = 0
    expanded_total = 0
    for level in range(1, MAX_TOGGLES + 1):
        found, expanded = _search_level(rows, level)
        expanded_total += expanded
        if found:
            solutions = found
            minimal_toggles = level
            break

    return {
        "already_valid": False,
        "measure_count": len(rows),
        "max_visits": MAX_VISITS,
        "max_toggles": MAX_TOGGLES,
        "minimal_toggles": minimal_toggles,
        "solutions": solutions,
        "solution_count": len(solutions),
        "expanded_candidates": expanded_total,
    }, None
