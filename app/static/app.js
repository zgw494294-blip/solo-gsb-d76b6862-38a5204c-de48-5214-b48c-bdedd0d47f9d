/* 乐谱反复展开台 —— 原生 JavaScript 前端 */
"use strict";

const MIN_ROWS = 2;
const MAX_ROWS = 64;
const MIN_BPM = 40;
const MAX_BPM = 240;
const MAX_DOTS = 12; // 卡片上最多渲染的拍点数量，超过则用“第 x/N 拍”文字表示

let rows = [];
let lastResult = null;
let selectedSource = null; // 当前双向高亮的来源小节索引
let planStale = false;     // 小节/拍数/BPM 改动后，旧播放计划失效

const tbody = document.getElementById("measure-tbody");
const rowCountEl = document.getElementById("row-count");
const errorBanner = document.getElementById("error-banner");
const visitStrip = document.getElementById("visit-strip");
const resultSummary = document.getElementById("result-summary");
const btnDownload = document.getElementById("btn-download");
const legend = document.getElementById("visit-legend");
const staleBanner = document.getElementById("stale-banner");
const btnReexpand = document.getElementById("btn-reexpand");
const btnPlay = document.getElementById("btn-play");
const btnPause = document.getElementById("btn-pause");
const btnStop = document.getElementById("btn-stop");
const transportStatus = document.getElementById("transport-status");

/* --------------------------------------------------------------- 工具 ---- */

function uid() {
  return Math.random().toString(36).slice(2, 9);
}

function blankRow(number) {
  return {
    key: uid(),
    number: String(number),
    beats: 4,
    tempo: "", // 首小节由初始化逻辑强制填入默认 BPM
    left_repeat: false,
    times: 2,
    right_repeat: false,
    right_closes: null, // 同线双终点（仅 MusicXML 导入产生）：[内层起点行, 外层起点行]
    volta: "",
    unknown: false, // 反求：是否勾选为待求位置（仅带左反复的小节可勾选）
  };
}

/* --------------------------------------------------------------- 渲染 ---- */

function noteHTML(row) {
  const parts = [];
  if (row.left_repeat) {
    parts.push('<span class="sym">|:</span>');
    parts.push(`×${row.times || "?"}`);
  }
  if (row.right_repeat) {
    parts.push('<span class="sym">:|</span>');
    if (Array.isArray(row.right_closes) && row.right_closes.length === 2) {
      parts.push('<span class="volta-tag" title="同一道小节线两侧各有一个终点：先闭合内层房子、再闭合外层反复">同线双终点</span>');
    }
  }
  if (row.volta !== "" && row.volta !== null) {
    parts.push(`<span class="volta-tag">{${row.volta}} 房</span>`);
  }
  return parts.join(" ") || '<span style="color:#c9c0ae">—</span>';
}

function tempoCellClass(row, i) {
  return i === 0 && row.tempo === "" ? "cell-error" : "";
}

function renderTable() {
  tbody.innerHTML = "";
  rows.forEach((row, i) => {
    const tr = document.createElement("tr");
    tr.dataset.index = String(i);
    tr.dataset.key = row.key;
    tr.innerHTML = `
      <td class="cell-num"><input type="text" data-field="number"
                 maxlength="16" value="${escapeHTML(row.number || "")}"
                 class="${row.number ? "" : "cell-error"}"></td>
      <td><input type="number" min="1" max="128" data-field="beats"
                 value="${Number(row.beats) || ""}"></td>
      <td><input type="number" min="${MIN_BPM}" max="${MAX_BPM}"
                 step="1" data-field="tempo"
                 placeholder="${i === 0 ? "必填" : "沿用"}"
                 value="${row.tempo === "" ? "" : row.tempo}"
                 class="${tempoCellClass(row, i)}"></td>
      <td><input type="checkbox" data-field="left_repeat"
                 ${row.left_repeat ? "checked" : ""}></td>
      <td><input type="number" min="2" max="4" data-field="times"
                 value="${row.times ?? ""}"
                 ${row.left_repeat ? "" : "disabled"}></td>
      <td><input type="checkbox" data-field="unknown"
                 ${row.unknown && row.left_repeat ? "checked" : ""}
                 ${row.left_repeat ? "" : "disabled"}
                 title="勾选为反求待求位置（仅带左反复的小节，1~8 个）"></td>
      <td><input type="checkbox" data-field="right_repeat"
                 ${row.right_repeat ? "checked" : ""}></td>
      <td><input type="number" min="1" max="4" data-field="volta"
                 placeholder="—" value="${row.volta === "" ? "" : row.volta}"></td>
      <td class="note">${noteHTML(row)}</td>
      <td><button type="button" class="btn-del" title="删除该小节"
                  ${rows.length <= MIN_ROWS ? "disabled" : ""}>✕</button></td>
    `;
    tbody.appendChild(tr);
  });
  rowCountEl.textContent = `${rows.length} / ${MAX_ROWS} 小节`;
  updateSolveSelection(); // 行增删、左反复变化都会刷新待求位置摘要
  renderCueEditor();      // 反复起点候选与来源小节候选随谱面刷新
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* --------------------------------------------------------------- 编辑 ---- */

tbody.addEventListener("input", (ev) => {
  const input = ev.target.closest("input[data-field]");
  if (!input) return;
  const tr = input.closest("tr");
  const i = Number(tr.dataset.index);
  const field = input.dataset.field;

  if (input.type === "checkbox") {
    rows[i][field] = input.checked;
    if (field === "left_repeat") {
      if (!input.checked) rows[i].unknown = false; // 无左反复的小节不能作待求位置
      renderTable(); // 刷新次数输入、待求勾选与记号预览
    }
    if (field === "right_repeat" && !input.checked) {
      rows[i].right_closes = null; // 手动取消右终点：同线双终点归属一并清除
    }
  } else if (field === "volta") {
    rows[i][field] = input.value === "" ? "" : Number(input.value);
    refreshNoteOnly(i);
  } else if (field === "number") {
    rows[i][field] = input.value;
  } else {
    // beats / tempo / times
    rows[i][field] = input.value === "" ? "" : Number(input.value);
    if (field === "beats") refreshNoteOnly(i);
  }
  clearRowError(i);
  if (field === "unknown") {
    // 待求勾选不属于谱面内容：不影响播放计划，但反求结果随之过期
    updateSolveSelection();
    markSolveStale();
  } else {
    invalidatePlan(); // 小节、拍数或 BPM 改动 → 旧播放计划立即失效
    markSolveStale(); // 谱面改动 → 反求结果同样过期
    markFixStale();   // 谱面改动 → 右终点纠错结果同样过期
  }
});

tbody.addEventListener("change", () => { /* checkbox 状态已在 input 事件处理 */ });

tbody.addEventListener("click", (ev) => {
  const del = ev.target.closest(".btn-del");
  if (del) {
    const i = Number(del.closest("tr").dataset.index);
    rows.splice(i, 1);
    renderTable();
    invalidatePlan();
    markSolveStale();
    markFixStale();
    return;
  }
  // 点击输入控件时只编辑，不触发高亮
  if (ev.target.closest("input, button")) return;
  const tr = ev.target.closest("tr");
  if (tr) highlightSource(Number(tr.dataset.index));
});

function refreshNoteOnly(i) {
  const tr = tbody.querySelector(`tr[data-index="${i}"] .note`);
  if (tr) tr.innerHTML = noteHTML(rows[i]);
}

document.getElementById("btn-add").addEventListener("click", () => {
  if (rows.length >= MAX_ROWS) return;
  rows.push(blankRow(rows.length + 1));
  renderTable();
  invalidatePlan();
  markSolveStale();
  markFixStale();
  tbody.lastElementChild?.scrollIntoView({ block: "nearest" });
});

/* --------------------------------------------------------------- 高亮 ---- */

function highlightSource(i) {
  if (!lastResult) return;
  selectedSource = (selectedSource === i) ? null : i;

  tbody.querySelectorAll("tr").forEach((tr) => {
    tr.classList.toggle("row-selected",
      selectedSource !== null && Number(tr.dataset.index) === selectedSource);
  });
  visitStrip.querySelectorAll(".visit-card").forEach((card) => {
    card.classList.toggle("v-selected",
      selectedSource !== null && Number(card.dataset.source) === selectedSource);
  });

  if (selectedSource !== null) {
    const first = visitStrip.querySelector(".v-selected");
    first?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }
}

function clearSelectionHighlight() {
  selectedSource = null;
  tbody.querySelectorAll("tr.row-selected")
    .forEach((tr) => tr.classList.remove("row-selected"));
  visitStrip.querySelectorAll(".v-selected")
    .forEach((card) => card.classList.remove("v-selected"));
}

/* --------------------------------------------------------------- 展开 ---- */

function collectPayload() {
  return rows.map((r) => ({
    number: r.number,
    beats: r.beats,
    tempo: r.tempo === "" ? null : r.tempo,
    left_repeat: r.left_repeat,
    right_repeat: r.right_repeat,
    // 同线双终点归属仅在勾选右终点时有意义；取消勾选时一并清空
    right_closes: r.right_repeat && Array.isArray(r.right_closes)
      && r.right_closes.length === 2
      ? r.right_closes : null,
    times: r.left_repeat ? (r.times || null) : null,
    volta: r.volta === "" ? null : r.volta,
  }));
}

async function doExpand() {
  clearErrors();
  if (rows.length < MIN_ROWS) {
    showGlobalError(`至少需要 ${MIN_ROWS} 个小节。`);
    return;
  }

  let resp;
  try {
    resp = await fetch("/api/expand", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
  } catch (e) {
    showGlobalError("无法连接服务器，请确认后端正在运行。");
    return;
  }

  const body = await resp.json();
  if (!body.ok) {
    renderErrors(body.errors || []);
    clearResult();
    return;
  }
  lastResult = body.result;
  planStale = false;
  // 先记下本次谱面签名，再渲染新演奏带
  const expandedSig = JSON.stringify(collectPayload());
  renderResult(lastResult);
  // 定位结果仅在谱面签名一致时保留（同谱重展可恢复已选区间高亮）；
  // 签名不一致说明谱面已在定位后被改动，旧结果作废。
  if (lastLocate && locateSig !== expandedSig) {
    markLocateStale();
  } else if (lastLocate && locateSelected !== null) {
    highlightLocateMatch(locateSelected);
  }
  // 提示单同理：谱面签名不一致时旧单作废（编辑谱面时已即时失效，
  // 这里兜底覆盖未走 invalidatePlan 的替换路径）
  if (lastCues && cuesSig !== expandedSig) {
    markCuesStale();
  }
  // 摘谱预览同理：谱面签名不一致时旧预览作废（选段本身保留）
  if (lastExcerpt && excerptSig !== expandedSig) {
    markExcerptStale();
  }
}

document.getElementById("btn-expand").addEventListener("click", doExpand);
btnReexpand.addEventListener("click", doExpand);

/* ------------------------------------------- 播放计划失效（编辑后） -------- */

function invalidatePlan() {
  markCompareStale(); // 谱面改动 → 对照结果同样过期
  markLocateStale();  // 谱面改动 → 定位结果同样过期
  markCuesStale();    // 谱面改动 → 提示单同样过期
  markExcerptStale(); // 谱面改动 → 摘谱预览同样过期
  if (!lastResult) return;
  planStale = true;        // 先标记，使 stop() 内的按钮同步按失效态渲染
  transport.stop();
  clearSelectionHighlight();
  staleBanner.classList.remove("hidden");
  visitStrip.classList.add("strip-stale");
  btnDownload.disabled = true;
  resultSummary.textContent = "播放计划已失效，请重新展开";
  transport.syncButtons();
  syncExcerptPickButton(); // 计划失效：退出摘谱选段模式并禁用选段按钮
}

/* --------------------------------------------------------------- 错误 ---- */

function clearErrors() {
  errorBanner.classList.add("hidden");
  errorBanner.innerHTML = "";
  tbody.querySelectorAll("tr.row-invalid").forEach((tr) => {
    tr.classList.remove("row-invalid");
  });
  tbody.querySelectorAll("input.cell-error").forEach((el) => {
    el.classList.remove("cell-error");
  });
}

function clearRowError(i) {
  tbody.querySelectorAll(`tr[data-index="${i}"] input.cell-error`)
    .forEach((el) => el.classList.remove("cell-error"));
  tbody.querySelector(`tr[data-index="${i}"]`)?.classList.remove("row-invalid");
}

function showGlobalError(message) {
  errorBanner.innerHTML = `<h3>无法展开</h3><ul><li>${escapeHTML(message)}</li></ul>`;
  errorBanner.classList.remove("hidden");
}

function jumpToRow(i) {
  const tr = tbody.querySelector(`tr[data-index="${i}"]`);
  tr?.scrollIntoView({ behavior: "smooth", block: "center" });
  tbody.querySelectorAll("tr.row-flash").forEach((t) => t.classList.remove("row-flash"));
  tr?.classList.add("row-flash");
  tr?.querySelector("input")?.focus({ preventScroll: true });
}

function markInvalidRows(errors) {
  errors.forEach((e) => {
    if (e.index === null || e.index === undefined) return;
    const tr = tbody.querySelector(`tr[data-index="${e.index}"]`);
    tr?.classList.add("row-invalid");
    // 编号错误由渲染时的 cell-error 处理；速度字段错误单独标红
    if (e.code === "TEMPO_RANGE" || e.code === "TEMPO_FIRST_REQUIRED") {
      tr?.querySelector('input[data-field="tempo"]')?.classList.add("cell-error");
    }
  });
}

function renderErrors(errors) {
  clearErrors();
  const lis = errors.map((e) => {
    const where = e.index !== null && e.index !== undefined
      ? `第 ${e.index + 1} 行${e.measure ? `（编号 ${escapeHTML(e.measure)}）` : ""}`
      : "整体";
    if (e.index !== null && e.index !== undefined) {
      return `<li><button type="button" class="link" data-jump="${e.index}">${where}</button>：${escapeHTML(e.message)}</li>`;
    }
    return `<li>${where}：${escapeHTML(e.message)}</li>`;
  }).join("");
  errorBanner.innerHTML = `<h3>校验未通过（${errors.length} 项）</h3><ul>${lis}</ul>`;
  errorBanner.classList.remove("hidden");

  errorBanner.querySelectorAll("button[data-jump]").forEach((btn) => {
    btn.addEventListener("click", () => jumpToRow(Number(btn.dataset.jump)));
  });

  markInvalidRows(errors);
}

/* --------------------------------------------------------------- 结果 ---- */

function clearResult() {
  transport.stop();
  lastResult = null;
  planStale = false;
  clearSelectionHighlight();
  staleBanner.classList.add("hidden");
  visitStrip.classList.remove("strip-stale");
  visitStrip.innerHTML =
    '<div class="placeholder">修正校验错误后重新展开。</div>';
  resultSummary.textContent = "展开失败";
  btnDownload.disabled = true;
  legend.classList.add("hidden");
  transport.syncButtons();
  markLocateStale();
  markCuesStale();
  markExcerptStale();
  syncExcerptPickButton(); // 无有效演奏带：退出选段模式并禁用选段按钮
}

function depthClass(depth) {
  if (depth <= 4) return `depth-${depth}`;
  return "depth-n";
}

function groupKey(path) {
  return path.map((p) => `${p.repeat_id}.${p.pass}`).join("/");
}

function groupLabel(path) {
  if (path.length === 0) return "反复段之外";
  return path.map((p) => `R${p.repeat_id} 第${p.pass}/${p.times}遍`).join(" · ");
}

function beatsHTML(beats) {
  if (beats <= MAX_DOTS) {
    let dots = "";
    for (let b = 0; b < beats; b++) {
      dots += `<span class="beat-dot${b === 0 ? " beat-strong" : ""}"></span>`;
    }
    return `<span class="v-beats-line">${dots}</span>`;
  }
  return `<span class="v-beats-line v-beats-text">第 0/${beats} 拍</span>`;
}

function renderResult(result) {
  clearSelectionHighlight();
  visitStrip.innerHTML = "";

  let prevTempo = null;
  let lastKey = null;
  result.visits.forEach((v) => {
    const key = groupKey(v.path);
    if (key !== lastKey) {
      const sep = document.createElement("div");
      sep.className = "group-sep";
      sep.innerHTML = `<span class="group-label">${escapeHTML(groupLabel(v.path))}</span>`;
      visitStrip.appendChild(sep);
      lastKey = key;
    }

    const tempoChanged = prevTempo !== null && v.tempo !== prevTempo;
    prevTempo = v.tempo;

    const depth = v.path.length;
    const card = document.createElement("div");
    card.className = `visit-card ${depthClass(depth)}`;
    card.dataset.source = String(v.source_index);
    card.dataset.visit = String(v.global_index - 1);
    card.dataset.beats = String(v.beats);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.title = `点击定位到本次访问的强拍（第 1 拍）\n`
      + `来源小节 ${v.source_measure} · 全局 #${v.global_index} · ${v.beats} 拍\n`
      + `生效速度 ${v.tempo} BPM${v.tempo_marked ? "（本小节标记）" : "（沿用前一标记）"}\n`
      + v.path.map((p) => `第${p.repeat_id} 层反复 第${p.pass}/${p.times}遍`
      + (p.tail ? "（尾部结尾）" : "")).join("\n");
    card.innerHTML = `
      <button type="button" class="v-pick" title="双向高亮该来源小节的全部访问">⇄</button>
      <span class="v-index">#${v.global_index}</span>
      <span class="v-measure">${escapeHTML(v.source_measure)}</span>
      <span class="v-beats">${v.beats}拍 · 累${v.cumulative_beats}</span>
      <span class="v-beats-wrap" aria-hidden="true">${beatsHTML(v.beats)}</span>
      <span class="v-path">${v.path.length
        ? v.path.map((p) => `${p.pass}/${p.times}`).join("›")
        : "—"}</span>
      <span class="v-tempo${tempoChanged ? " tempo-change" : ""}">♩=${v.tempo}</span>
      ${v.volta !== null && v.volta !== undefined
        ? `<span class="v-volta">{${v.volta}}</span>` : ""}
    `;
    // 点击卡片（非 ⇄ 按钮）= 定位到该次访问的强拍；摘谱选段模式下改为选段
    card.addEventListener("click", (ev) => {
      if (ev.target.closest(".v-pick")) return;
      if (excerptPickMode) {
        excerptPick(Number(card.dataset.visit) + 1); // 1 基访问序号
        return;
      }
      transport.seekVisit(Number(card.dataset.visit));
    });
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        if (excerptPickMode) {
          excerptPick(Number(card.dataset.visit) + 1);
          return;
        }
        transport.seekVisit(Number(card.dataset.visit));
      }
    });
    card.querySelector(".v-pick").addEventListener("click", (ev) => {
      ev.stopPropagation();
      highlightSource(v.source_index);
    });
    visitStrip.appendChild(card);
  });

  resultSummary.textContent =
    `${result.visit_count} 次访问 · ${result.measure_count} 个来源小节`
    + ` · 共 ${result.total_beats} 拍`;
  btnDownload.disabled = false;
  staleBanner.classList.add("hidden");
  visitStrip.classList.remove("strip-stale");

  renderLegend();
  transport.reset(result);
  // 重新渲染演奏带后：恢复摘谱选段高亮并同步选段按钮可用态
  paintExcerptSelection();
  syncExcerptPickButton();
}

function renderLegend() {
  legend.classList.remove("hidden");
  legend.innerHTML = `
    <span class="chip"><span class="swatch depth-0" style="background:#fdfcf9"></span>段外</span>
    <span class="chip"><span class="swatch depth-1" style="background:#eef0ff"></span>第 1 层</span>
    <span class="chip"><span class="swatch depth-2" style="background:#ecfbf7"></span>第 2 层</span>
    <span class="chip"><span class="swatch depth-3" style="background:#fff1f1"></span>第 3 层</span>
    <span class="chip"><span class="swatch depth-4" style="background:#fffaeb"></span>第 4 层</span>
    <span class="chip">右上角 {k} 角标 = 替代结尾遍次</span>
    <span class="chip">♩= 为生效速度（描边表示新速度标记）；⇄ 双向高亮同一小节的全部访问</span>
    <span class="chip">点击卡片定位到该次访问强拍；强拍音更高，当前拍点同步高亮</span>
  `;
}

/* --------------------------------------------------------------- 下载 ---- */

btnDownload.addEventListener("click", () => {
  if (!lastResult) return;
  const payload = {
    exported_at: new Date().toISOString(),
    measure_count: lastResult.measure_count,
    visit_count: lastResult.visit_count,
    total_beats: lastResult.total_beats,
    tempo_range: lastResult.tempo_range,
    tempos: lastResult.tempos,
    repeats: lastResult.repeats,
    visits: lastResult.visits.map((v) => ({
      global_index: v.global_index,          // 全局序号
      source_measure: v.source_measure,      // 来源编号
      source_index: v.source_index,
      beats: v.beats,
      tempo: v.tempo,                        // 该次访问的生效速度（按来源小节）
      tempo_marked: v.tempo_marked,
      cumulative_beats: v.cumulative_beats,
      volta: v.volta,
      pass_path: v.path,                     // 各层遍次路径
    })),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `score-expansion-${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

/* =========================================================================
   排练节拍器：Web Audio API 本地合成 + 前瞻调度
   -------------------------------------------------------------------------
   - 调度器每 25ms 轮询一次，把未来 0.12s 内的节拍用音频时钟提前排队，
     避免 setInterval 抖动导致的节拍不稳；
   - cursor 在排队时即向前推进，因此暂停时要把游标回退到队列中
     “第一拍尚未响起”的拍（暂停记住的下一未响节拍）；
   - 停止 / 定位会 stop() 全部已排队 OscillatorNode（未 start 时 stop
     同样会阻止其发声），并取消视觉定时器。
   ======================================================================== */

const transport = (() => {
  const LOOKAHEAD_MS = 25;
  const SCHEDULE_AHEAD = 0.12;

  let visits = null;   // 当前播放计划（展开结果中的 visits）
  let ctx = null;      // AudioContext，用户手势时懒创建
  let timerId = null;  // 调度器句柄
  let endTimer = null; // 全曲最后一拍余响后的收尾句柄
  let state = "idle";  // idle | running | paused
  let cursor = null;   // 下一拍 { visitIndex, beat }（排队时已被向前推进）
  let nextTime = 0;    // cursor 对应节拍的 AudioContext 时间
  let queue = [];      // 已排队条目

  function ensureCtx() {
    if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function advance(pos) {
    const measure = visits[pos.visitIndex];
    if (pos.beat + 1 < measure.beats) {
      return { visitIndex: pos.visitIndex, beat: pos.beat + 1 };
    }
    if (pos.visitIndex + 1 < visits.length) {
      return { visitIndex: pos.visitIndex + 1, beat: 0 };
    }
    return null;
  }

  function beatDuration(pos) {
    return 60 / visits[pos.visitIndex].tempo;
  }

  /* 单个节拍音，全部本地合成：强拍音高更高、带上方五度泛音、更响更长 */
  function scheduleClick(time, downbeat) {
    const ac = ctx;
    const master = ac.createGain();
    master.gain.setValueAtTime(0.0001, time);
    master.connect(ac.destination);

    const partials = downbeat
      ? [[1568, 0.9], [2352, 0.3]]
      : [[1046, 0.7]];
    const dur = downbeat ? 0.09 : 0.055;
    const peak = downbeat ? 0.9 : 0.5;
    const oscs = [];
    for (const [freq, mix] of partials) {
      const osc = ac.createOscillator();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, time);
      const g = ac.createGain();
      g.gain.setValueAtTime(0.0001, time);
      g.gain.exponentialRampToValueAtTime(peak * mix, time + 0.002);
      g.gain.exponentialRampToValueAtTime(0.0001, time + dur);
      osc.connect(g).connect(master);
      osc.start(time);
      osc.stop(time + dur + 0.02);
      oscs.push(osc);
    }
    return { master, oscs };
  }

  function tick() {
    if (state !== "running" || !cursor) return;
    while (cursor && nextTime < ctx.currentTime + SCHEDULE_AHEAD) {
      const pos = { ...cursor };
      const when = nextTime;
      const downbeat = pos.beat === 0;
      const { master, oscs } = scheduleClick(when, downbeat);

      // 视觉回调在临近音频时钟时触发，而不是排队瞬间
      const delayMs = Math.max(0, (when - ctx.currentTime) * 1000);
      const timer = setTimeout(() => {
        queue = queue.filter((q) => q.timer !== timer);
        onBeat(pos);
      }, delayMs);

      queue.push({ time: when, visitIndex: pos.visitIndex, beat: pos.beat,
                   oscs, master, timer });

      nextTime += beatDuration(pos);
      cursor = advance(cursor);
    }
    if (!cursor && timerId !== null && endTimer === null) {
      // 所有节拍已排队：停止轮询，等最后一拍响起后收尾
      clearInterval(timerId);
      timerId = null;
      const last = queue[queue.length - 1];
      const waitMs = last
        ? Math.max(0, (last.time - ctx.currentTime) + 0.12) * 1000
        : 0;
      endTimer = setTimeout(finish, waitMs);
    }
  }

  /* 取消全部已排队但尚未响起的音源与视觉定时器。
     pauseLike=true 时（暂停），游标回退到队列最早的拍，即下一未响节拍。 */
  function flush(pauseLike) {
    if (timerId !== null) {
      clearInterval(timerId);
      timerId = null;
    }
    if (endTimer !== null) {
      clearTimeout(endTimer);
      endTimer = null;
    }
    if (pauseLike && queue.length) {
      const head = queue.reduce((a, b) => (a.time < b.time ? a : b));
      cursor = { visitIndex: head.visitIndex, beat: head.beat };
    }
    for (const q of queue) {
      clearTimeout(q.timer);
      if (ctx) {
        try {
          q.master.gain.cancelScheduledValues(q.time);
          q.master.gain.setValueAtTime(0, Math.max(ctx.currentTime, q.time));
        } catch (_) { /* 忽略 */ }
        for (const osc of q.oscs) {
          try { osc.stop(); } catch (_) { /* 未 start 时 stop 同样阻止发声 */ }
        }
        try { q.master.disconnect(); } catch (_) { /* 忽略 */ }
      }
    }
    queue = [];
  }

  function onBeat(pos) {
    if (state !== "running") return;
    paintPosition(pos, false);
  }

  function finish() {
    endTimer = null;
    if (state !== "running") return;
    queue = [];
    state = "idle";
    cursor = null;
    clearPositionPaint();
    syncButtons();
  }

  function startScheduler() {
    if (timerId === null) {
      timerId = setInterval(tick, LOOKAHEAD_MS);
      tick();
    }
  }

  /* ------------------------------------------------------ 公共控制 ---- */

  function play() {
    if (!visits || planStale) return;
    const ac = ensureCtx();
    flush(false);
    if (state === "idle" || cursor === null) {
      cursor = { visitIndex: 0, beat: 0 };
    }
    nextTime = ac.currentTime + 0.08;
    state = "running";
    clearPositionPaint();
    startScheduler();
    syncButtons();
    paintPosition(cursor, true);
  }

  // 暂停：记住下一未响节拍，恢复后从该拍继续
  function pause() {
    if (state !== "running") return;
    flush(true);
    state = "paused";
    syncButtons();
    if (cursor) paintPosition(cursor, true);
  }

  function stop() {
    flush(false);
    state = "idle";
    cursor = null;
    clearPositionPaint();
    syncButtons();
  }

  // 点击演奏带定位：取消已排队音源，从该次访问强拍（暂停态）开始
  function seekVisit(visitIndex) {
    if (!visits || planStale) return;
    flush(false);
    cursor = { visitIndex, beat: 0 };
    state = "paused";
    syncButtons();
    paintPosition(cursor, true);
  }

  function syncButtons() {
    const enabled = !!visits && !planStale;
    btnPlay.disabled = !enabled || state === "running";
    btnPause.disabled = !enabled || state !== "running";
    btnStop.disabled = !enabled || (state === "idle" && cursor === null);
  }

  // 重新展开后换用新计划
  function reset(result) {
    flush(false);
    visits = result ? result.visits : null;
    state = "idle";
    cursor = null;
    clearPositionPaint();
    syncButtons();
  }

  /* ------------------------------------------------------ 高亮同步 ---- */

  function cardAt(visitIndex) {
    return visitStrip.querySelector(
      `.visit-card[data-visit="${visitIndex}"]`);
  }

  function clearPositionPaint() {
    visitStrip.querySelectorAll(".visit-card.playing,.visit-card.cued")
      .forEach((el) => el.classList.remove("playing", "cued"));
    visitStrip.querySelectorAll(".beat-dot.beat-on")
      .forEach((el) => el.classList.remove("beat-on"));
    visitStrip.querySelectorAll(".v-beats-text").forEach((txt) => {
      txt.textContent = `第 0/${txt.closest(".visit-card").dataset.beats} 拍`;
    });
    tbody.querySelectorAll("tr.row-playing")
      .forEach((tr) => tr.classList.remove("row-playing"));
    updateStatus(null);
  }

  function paintPosition(pos, cued) {
    clearPositionPaint();
    const card = cardAt(pos.visitIndex);
    if (!card) return;
    card.classList.add(cued ? "cued" : "playing");

    const beats = Number(card.dataset.beats);
    if (beats <= MAX_DOTS) {
      card.querySelectorAll(".beat-dot")[pos.beat]
        ?.classList.add("beat-on");
    } else {
      card.querySelector(".v-beats-text").textContent =
        `第 ${pos.beat + 1}/${beats} 拍`;
    }

    // 同步高亮当前访问的来源小节行
    const source = Number(card.dataset.source);
    tbody.querySelectorAll("tr.row-playing")
      .forEach((tr) => tr.classList.remove("row-playing"));
    tbody.querySelector(`tr[data-index="${source}"]`)
      ?.classList.add("row-playing");

    if (!cued) {
      // 播放中仅在卡片即将离开视口时跟随滚动，避免频繁跳动
      const r = card.getBoundingClientRect();
      if (r.left < 60 || r.right > window.innerWidth - 60) {
        card.scrollIntoView({ behavior: "smooth", block: "nearest",
                              inline: "center" });
      }
    }
    updateStatus(pos);
  }

  function updateStatus(pos) {
    if (!visits) {
      transportStatus.textContent = "展开后可在此排练";
      transportStatus.dataset.state = "";
      return;
    }
    if (planStale) {
      transportStatus.textContent = "计划已失效 · 请重新展开";
      transportStatus.dataset.state = "stale";
      return;
    }
    if (state === "idle" || pos === null) {
      transportStatus.textContent = `就绪 · 共 ${visits.length} 次访问`
        + ` · 起始 ♩=${visits[0].tempo}`;
      transportStatus.dataset.state = "";
      return;
    }
    const v = visits[pos.visitIndex];
    const label = `${v.source_measure} #${v.global_index}`
      + ` · 第 ${pos.beat + 1}/${v.beats} 拍`
      + ` · ♩=${v.tempo}`;
    transportStatus.textContent =
      (state === "running" ? "▶ 播放中 · " : "⏸ 暂停于下一未响节拍 · ")
      + label;
    transportStatus.dataset.state = state;
  }

  return { play, pause, stop, seekVisit, reset, syncButtons };
})();

btnPlay.addEventListener("click", () => transport.play());
btnPause.addEventListener("click", () => transport.pause());
btnStop.addEventListener("click", () => transport.stop());

/* =========================================================================
   改谱影响对照
   -------------------------------------------------------------------------
   - “设为基准”：先经 /api/expand 校验当前谱面，通过后保存录入行快照；
     再次设置直接覆盖旧基准（校验失败则保留旧基准）。
   - “发起对照”：POST /api/compare 上传基准快照与当前小节，两版在服务端
     分别重新校验展开；任一无效时只显示该版错误，并隐藏旧的对照差异。
   - 结果以“对齐列”可视化（上排基准、下排当前），差异条目可点击定位到
     对齐列与当前版来源行。
   ======================================================================== */

let baseline = null;    // { rows, setAt, measureCount, visitCount }
let lastCompare = null; // 最近一次成功的对照结果

const baselineStatus = document.getElementById("baseline-status");
const btnSetBaseline = document.getElementById("btn-set-baseline");
const btnCompare = document.getElementById("btn-compare");
const compareNote = document.getElementById("compare-note");
const compareErrors = document.getElementById("compare-errors");
const compareBody = document.getElementById("compare-body");
const compareSummary = document.getElementById("compare-summary");
const firstDiff = document.getElementById("first-diff");
const diffList = document.getElementById("diff-list");
const alignView = document.getElementById("align-view");

function fmtSec(x) { return `${Number(x).toFixed(2)}s`; }

function fmtOffset(x) {
  const sign = x > 0 ? "+" : x < 0 ? "-" : "±";
  return `${sign}${Math.abs(x).toFixed(2)}s`;
}

function pathLabel(path) {
  if (!path || path.length === 0) return "段外";
  return path.map((p) => `${p.pass}/${p.times}${p.tail ? "尾" : ""}`).join("›");
}

function showCompareNote(text) {
  compareNote.textContent = text;
  compareNote.classList.remove("hidden");
}

function hideComparePanels() {
  compareBody.classList.add("hidden");
  compareErrors.classList.add("hidden");
  compareNote.classList.add("hidden");
}

function updateBaselineStatus() {
  if (!baseline) {
    baselineStatus.textContent = "尚未设置基准";
    btnCompare.disabled = true;
    return;
  }
  const t = baseline.setAt;
  const hhmmss = [t.getHours(), t.getMinutes(), t.getSeconds()]
    .map((n) => String(n).padStart(2, "0")).join(":");
  baselineStatus.textContent =
    `基准：${baseline.measureCount} 小节 · ${baseline.visitCount} 次访问`
    + ` · 设于 ${hhmmss}`;
  btnCompare.disabled = false;
}

/* 谱面改动后：旧对照结果过期，不再展示 */
function markCompareStale() {
  if (compareBody.classList.contains("hidden")
      && compareErrors.classList.contains("hidden")) {
    return; // 没有展示中的对照结果或对照错误
  }
  lastCompare = null;
  hideComparePanels();
  showCompareNote("谱面已改动，对照结果已过期，请重新发起对照。");
}

btnSetBaseline.addEventListener("click", async () => {
  clearErrors();
  if (rows.length < MIN_ROWS) {
    showGlobalError(`至少需要 ${MIN_ROWS} 个小节。`);
    return;
  }
  let resp;
  try {
    resp = await fetch("/api/expand", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
  } catch (e) {
    showGlobalError("无法连接服务器，请确认后端正在运行。");
    return;
  }
  const body = await resp.json();
  if (!body.ok) {
    // 当前谱面无效：不覆盖旧基准
    renderErrors(body.errors || []);
    return;
  }
  const hadBaseline = !!baseline;
  baseline = {
    rows: collectPayload(), // 录入行快照（重设即覆盖旧基准）
    setAt: new Date(),
    measureCount: body.result.measure_count,
    visitCount: body.result.visit_count,
  };
  lastCompare = null;
  hideComparePanels();
  showCompareNote(hadBaseline
    ? "基准已重设（旧基准已覆盖），可发起对照。"
    : "基准已设置，可发起对照。");
  updateBaselineStatus();
});

btnCompare.addEventListener("click", async () => {
  if (!baseline) return;
  clearErrors();
  let resp;
  try {
    resp = await fetch("/api/compare", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        baseline: baseline.rows,
        current: collectPayload(),
      }),
    });
  } catch (e) {
    lastCompare = null;
    hideComparePanels();
    showCompareNote("无法连接服务器，请确认后端正在运行。");
    return;
  }
  const body = await resp.json();
  if (!body.ok) {
    // 任一版本无效：显示该版错误，不展示过期差异
    lastCompare = null;
    hideComparePanels();
    renderCompareErrors(body.baseline_errors || [], body.current_errors || []);
    return;
  }
  lastCompare = body.result;
  hideComparePanels();
  renderCompare(body.result);
});

function renderCompareErrors(baseErrs, currErrs) {
  compareErrors.innerHTML = "";
  const blocks = [];
  if (baseErrs.length) blocks.push(["基准版本校验未通过", baseErrs, false]);
  if (currErrs.length) blocks.push(["当前版本校验未通过", currErrs, true]);
  blocks.forEach(([title, errs, jumpable]) => {
    const h = document.createElement("h3");
    h.textContent = `${title}（${errs.length} 项）`;
    compareErrors.appendChild(h);
    const ul = document.createElement("ul");
    errs.forEach((e) => {
      const li = document.createElement("li");
      const where = e.index !== null && e.index !== undefined
        ? `第 ${e.index + 1} 行${e.measure ? `（编号 ${e.measure}）` : ""}`
        : "整体";
      if (jumpable && e.index !== null && e.index !== undefined) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "link";
        btn.textContent = where;
        btn.addEventListener("click", () => jumpToRow(e.index));
        li.appendChild(btn);
        li.appendChild(document.createTextNode(`：${e.message}`));
      } else {
        li.textContent = `${where}：${e.message}`;
      }
      ul.appendChild(li);
    });
    compareErrors.appendChild(ul);
  });
  compareErrors.classList.remove("hidden");
  if (currErrs.length) markInvalidRows(currErrs); // 当前版错误行在录入表标红
}

function describeEvent(ev) {
  if (ev.kind === "removed") {
    const b = ev.base;
    return `基准 #${b.global_index} · 来源 ${b.source_measure}`
      + `（基准第 ${b.source_index + 1} 行）· ${b.beats} 拍 ♩=${b.tempo}`
      + ` · 路径 ${pathLabel(b.path)} · 起拍 ${fmtSec(b.start_seconds)}`;
  }
  if (ev.kind === "added") {
    const c = ev.curr;
    return `当前 #${c.global_index} · 来源 ${c.source_measure}`
      + `（当前第 ${c.source_index + 1} 行）· ${c.beats} 拍 ♩=${c.tempo}`
      + ` · 路径 ${pathLabel(c.path)} · 起拍 ${fmtSec(c.start_seconds)}`;
  }
  const head = `基准 #${ev.base.global_index} ↔ 当前 #${ev.curr.global_index}`
    + ` · 来源 ${ev.curr.source_measure}`;
  const parts = [];
  if (ev.changes.includes("beats")) {
    parts.push(`拍数 ${ev.base.beats}→${ev.curr.beats}`);
  }
  if (ev.changes.includes("tempo")) {
    parts.push(`BPM ${ev.base.tempo}→${ev.curr.tempo}`);
  }
  if (ev.changes.includes("path")) {
    parts.push(`路径 ${pathLabel(ev.base.path)}→${pathLabel(ev.curr.path)}`);
  }
  const tail = `起拍偏移 ${fmtOffset(ev.time_offset)}`;
  return parts.length
    ? `${head}：${parts.join("；")} · ${tail}`
    : `${head} · ${tail}`;
}

function renderCompare(result) {
  compareBody.classList.remove("hidden");

  const s = result.stats;
  compareSummary.innerHTML = "";
  [
    `基准 ${result.baseline.visit_count} 次访问 · 全长 ${fmtSec(result.baseline.total_seconds)}`,
    `当前 ${result.current.visit_count} 次访问 · 全长 ${fmtSec(result.current.total_seconds)}`,
    `配对 ${s.paired}（未变 ${s.unchanged} · 变更 ${s.changed}）`
      + ` · 新增 ${s.added} · 删去 ${s.removed}`,
  ].forEach((text) => {
    const span = document.createElement("span");
    span.textContent = text;
    compareSummary.appendChild(span);
  });

  // 按对齐顺序的首次差异
  firstDiff.innerHTML = "";
  if (result.first_difference) {
    const fd = result.first_difference;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link";
    btn.textContent = `首次差异（对齐第 ${fd.align_index + 1} 位）`;
    btn.addEventListener("click", () => locateEvent(fd.align_index));
    firstDiff.appendChild(btn);
    firstDiff.appendChild(
      document.createTextNode(`：${describeEvent(fd)}`));
  } else {
    firstDiff.textContent = "两版演奏带完全一致，没有差异。";
  }

  // 可点击的差异列表
  diffList.innerHTML = "";
  const diffs = result.events.filter((ev) => ev.differs);
  if (diffs.length === 0) {
    const li = document.createElement("li");
    li.className = "diff-none";
    li.textContent = "没有差异。";
    diffList.appendChild(li);
  }
  diffs.forEach((ev) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "diff-item";
    const badge = document.createElement("span");
    badge.className = `diff-badge ${ev.kind === "pair" ? "changed" : ev.kind}`;
    badge.textContent =
      ev.kind === "added" ? "新增" : ev.kind === "removed" ? "删去" : "变更";
    btn.appendChild(badge);
    const text = document.createElement("span");
    text.textContent = describeEvent(ev);
    btn.appendChild(text);
    btn.addEventListener("click", () => locateEvent(ev.align_index));
    li.appendChild(btn);
    diffList.appendChild(li);
  });

  renderAlign(result.events, result.first_difference);
}

function renderAlign(events, first) {
  alignView.innerHTML = "";
  const frag = document.createDocumentFragment();
  events.forEach((ev) => {
    const col = document.createElement("div");
    col.className = "align-col";
    col.dataset.align = String(ev.align_index);
    if (first && ev.align_index === first.align_index) {
      col.classList.add("first-diff-col");
    }
    col.appendChild(buildChip(ev.base, "基准", ev));
    col.appendChild(buildChip(ev.curr, "当前", ev));
    col.addEventListener("click", () => locateEvent(ev.align_index));
    frag.appendChild(col);
  });
  alignView.appendChild(frag);
}

function buildChip(ref, sideLabel, ev) {
  const cell = document.createElement("div");
  if (!ref) {
    cell.className = "align-slot empty";
    return cell;
  }
  cell.className = `align-chip ${ev.kind}`
    + (ev.kind === "pair" && ev.changes.length ? " changed" : "");
  const measure = document.createElement("span");
  measure.className = "chip-measure";
  measure.textContent = ref.source_measure;
  const idx = document.createElement("span");
  idx.className = "chip-index";
  idx.textContent = `#${ref.global_index}`;
  cell.appendChild(measure);
  cell.appendChild(idx);
  const lines = [
    `${sideLabel} #${ref.global_index} · 来源 ${ref.source_measure}`
      + `（第 ${ref.source_index + 1} 行）`,
    `${ref.beats} 拍 · ♩=${ref.tempo} · 路径 ${pathLabel(ref.path)}`
      + (ref.volta !== null && ref.volta !== undefined
        ? ` · {${ref.volta}} 房` : ""),
    `起拍 ${fmtSec(ref.start_seconds)}`,
  ];
  if (ev.kind === "pair") lines.push(`起拍偏移 ${fmtOffset(ev.time_offset)}`);
  cell.title = lines.join("\n");
  return cell;
}

function locateEvent(alignIndex) {
  if (!lastCompare) return;
  const ev = lastCompare.events[alignIndex];
  if (!ev) return;
  const col = alignView.querySelector(`[data-align="${alignIndex}"]`);
  alignView.querySelectorAll(".col-flash")
    .forEach((c) => c.classList.remove("col-flash"));
  if (col) {
    col.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    col.classList.add("col-flash");
  }
  // 涉及当前版的差异：同时定位到录入表中的来源行
  if (ev.curr) jumpToRow(ev.curr.source_index);
}

/* =========================================================================
   反复次数反求
   -------------------------------------------------------------------------
   - 在表格“待求”列勾选 1~8 个带左反复的小节，输入目标演奏编号序列
     （逗号或换行分隔，最多 512 项）；
   - POST /api/solve：先按现有规则校验录入谱面与目标编号，再枚举待求
     位置的次数组合（各取 2~4，其余次数照录入值）；每个组合都须通过
     现有校验与展开，超过 512 次访问的组合不计入；
   - 方案按待求位置的录入行顺序组成次数元组，字典序前 10 个可点击写回
     表格；写回使旧演奏带、节拍器计划与改谱对照失效。
   ======================================================================== */

const MAX_TARGET_ITEMS = 512;

let lastSolve = null; // 最近一次成功的反求结果

const solveSelection = document.getElementById("solve-selection");
const targetInput = document.getElementById("target-input");
const btnSolve = document.getElementById("btn-solve");
const solveNote = document.getElementById("solve-note");
const solveErrors = document.getElementById("solve-errors");
const solveBody = document.getElementById("solve-body");
const solveSummary = document.getElementById("solve-summary");
const solveEmpty = document.getElementById("solve-empty");
const solutionList = document.getElementById("solution-list");
const solveMore = document.getElementById("solve-more");

function updateSolveSelection() {
  const picked = [];
  rows.forEach((r, i) => {
    if (r.unknown && r.left_repeat) {
      picked.push(`第 ${i + 1} 行 ${r.number || "?"}`);
    }
  });
  solveSelection.textContent = picked.length
    ? `待求位置 ${picked.length} 个：${picked.join(" · ")}`
    : "未勾选待求位置（在“待求”列勾选 1~8 个带左反复的小节）";
}

function showSolveNote(text) {
  solveNote.textContent = text;
  solveNote.classList.remove("hidden");
}

function hideSolvePanels() {
  solveBody.classList.add("hidden");
  solveErrors.classList.add("hidden");
  solveNote.classList.add("hidden");
}

/* 谱面、待求位置或目标序列改动后：旧反求结果过期，不再展示 */
function markSolveStale() {
  if (solveBody.classList.contains("hidden")
      && solveErrors.classList.contains("hidden")) {
    return;
  }
  lastSolve = null;
  hideSolvePanels();
  showSolveNote("谱面、待求位置或目标序列已改动，反求结果已过期，请重新反求。");
}

function parseTarget(text) {
  return text.split(/[,，、\r\n]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function collectUnknowns() {
  const indices = [];
  rows.forEach((r, i) => {
    if (r.unknown && r.left_repeat) indices.push(i);
  });
  return indices;
}

btnSolve.addEventListener("click", async () => {
  clearErrors();
  hideSolvePanels();
  lastSolve = null;

  const unknowns = collectUnknowns();
  if (unknowns.length < 1 || unknowns.length > 8) {
    showSolveNote(`请先在表格“待求”列勾选 1~8 个带左反复的小节（当前 ${unknowns.length} 个）。`);
    return;
  }
  const target = parseTarget(targetInput.value);
  if (target.length === 0) {
    showSolveNote("请填写目标演奏编号序列（逗号或换行分隔）。");
    return;
  }
  if (target.length > MAX_TARGET_ITEMS) {
    showSolveNote(`目标序列最多 ${MAX_TARGET_ITEMS} 项，当前 ${target.length} 项。`);
    return;
  }

  let resp;
  try {
    resp = await fetch("/api/solve", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        measures: collectPayload(),
        unknowns,
        target,
      }),
    });
  } catch (e) {
    showSolveNote("无法连接服务器，请确认后端正在运行。");
    return;
  }
  const body = await resp.json();
  if (!body.ok) {
    renderSolveErrors(body);
    return;
  }
  lastSolve = body.result;
  renderSolveResult(body.result);
});

targetInput.addEventListener("input", markSolveStale);

function renderSolveErrors(body) {
  solveErrors.innerHTML = "";
  const blocks = [
    ["录入谱面校验未通过", body.score_errors || [], true],
    ["待求位置选择有误", body.unknown_errors || [], false],
    ["目标序列有误", body.target_errors || [], false],
  ].filter(([, errs]) => errs.length);

  blocks.forEach(([title, errs, jumpable]) => {
    const h = document.createElement("h3");
    h.textContent = `${title}（${errs.length} 项）`;
    solveErrors.appendChild(h);
    const ul = document.createElement("ul");
    errs.forEach((e) => {
      const li = document.createElement("li");
      if (jumpable && e.index !== null && e.index !== undefined) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "link";
        btn.textContent = `第 ${e.index + 1} 行`
          + `${e.measure ? `（编号 ${e.measure}）` : ""}`;
        btn.addEventListener("click", () => jumpToRow(e.index));
        li.appendChild(btn);
        li.appendChild(document.createTextNode(`：${e.message}`));
      } else {
        li.textContent = e.message;
      }
      ul.appendChild(li);
    });
    solveErrors.appendChild(ul);
  });
  solveErrors.classList.remove("hidden");

  const scoreErrs = body.score_errors || [];
  if (scoreErrs.length) markInvalidRows(scoreErrs); // 谱面错误行在录入表标红
}

function renderSolveResult(result) {
  solveBody.classList.remove("hidden");

  solveSummary.innerHTML = "";
  [
    `目标序列 ${result.target_count} 项`,
    `待求位置：${result.unknowns
      .map((u) => `第 ${u.index + 1} 行 ${u.measure}`).join("、")}`,
    `组合空间 ${result.combination_count}`,
    `总解数 ${result.solution_count}`,
  ].forEach((text) => {
    const span = document.createElement("span");
    span.textContent = text;
    solveSummary.appendChild(span);
  });

  solutionList.innerHTML = "";
  if (result.solution_count === 0) {
    // 谱面与目标均合法但无解：明确提示
    solveEmpty.textContent = "谱面与目标序列均合法，但待求位置取 2~4 的"
      + `全部 ${result.combination_count} 种组合都无法展开成目标序列 —— 无解。`;
    solveEmpty.classList.remove("hidden");
    solveMore.classList.add("hidden");
    return;
  }
  solveEmpty.classList.add("hidden");

  result.solutions.forEach((sol, k) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "solution-item";
    btn.title = "点击把该方案的次数写回表格";
    const badge = document.createElement("span");
    badge.className = "sol-badge";
    badge.textContent = `方案 ${k + 1}`;
    btn.appendChild(badge);
    const text = document.createElement("span");
    text.textContent = result.unknowns
      .map((u, j) => `${u.measure}（第 ${u.index + 1} 行）= ${sol.times[j]} 遍`)
      .join(" · ");
    btn.appendChild(text);
    btn.addEventListener("click", () => applySolution(btn, sol));
    li.appendChild(btn);
    solutionList.appendChild(li);
  });

  if (result.solution_count > result.solutions.length) {
    solveMore.textContent = `共 ${result.solution_count} 个方案，`
      + `仅显示次数元组字典序前 ${result.solutions.length} 个。`;
    solveMore.classList.remove("hidden");
  } else {
    solveMore.classList.add("hidden");
  }
}

/* 点击方案：次数写回表格 → 旧演奏带、节拍器计划与改谱对照失效 */
function applySolution(btn, sol) {
  if (!lastSolve) return;
  lastSolve.unknowns.forEach((u, j) => {
    rows[u.index].times = sol.times[j];
  });
  renderTable();
  invalidatePlan(); // 写回次数 = 谱面改动：旧演奏带 / 节拍器计划 / 对照结果失效
  solutionList.querySelectorAll(".solution-item")
    .forEach((el) => el.classList.remove("applied"));
  btn.classList.add("applied");
  showSolveNote("方案次数已写回表格；旧演奏带、节拍器计划与改谱对照已失效，"
    + "请重新展开或对照后查看新结果。");
}

/* =========================================================================
   右反复终点纠错
   -------------------------------------------------------------------------
   - POST /api/fix-right：只切换各行右反复 :|，最多 4 处；
   - 原谱能正常展开 → 提示无需纠错；否则按切换数最少、同数字典序返回
     前 5 个方案，每个方案列出新增 / 删除的终点与展开访问数；
   - 点击方案只写回各行 right_repeat；写回后旧演奏带、节拍器计划、
     改谱对照与反求结果全部失效。
   ======================================================================== */

let lastFix = null; // 最近一次成功的纠错结果

const btnFix = document.getElementById("btn-fix");
const fixNote = document.getElementById("fix-note");
const fixErrors = document.getElementById("fix-errors");
const fixBody = document.getElementById("fix-body");
const fixStats = document.getElementById("fix-stats");
const fixEmpty = document.getElementById("fix-empty");
const fixList = document.getElementById("fix-list");

function showFixNote(text) {
  fixNote.textContent = text;
  fixNote.classList.remove("hidden");
}

function hideFixPanels() {
  fixBody.classList.add("hidden");
  fixErrors.classList.add("hidden");
  fixNote.classList.add("hidden");
}

/* 谱面改动后：旧纠错结果过期，不再展示 */
function markFixStale() {
  if (fixBody.classList.contains("hidden")
      && fixErrors.classList.contains("hidden")
      && fixNote.classList.contains("hidden")) {
    return;
  }
  lastFix = null;
  hideFixPanels();
  showFixNote("谱面已改动，纠错结果已过期，请重新纠错。");
}

btnFix.addEventListener("click", async () => {
  clearErrors();
  hideFixPanels();
  lastFix = null;

  if (rows.length < MIN_ROWS) {
    showFixNote(`至少需要 ${MIN_ROWS} 个小节。`);
    return;
  }

  let resp;
  try {
    resp = await fetch("/api/fix-right", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ measures: collectPayload() }),
    });
  } catch (e) {
    showFixNote("无法连接服务器，请确认后端正在运行。");
    return;
  }
  const body = await resp.json();
  if (!body.ok) {
    renderFixErrors(body.score_errors || []);
    return;
  }
  lastFix = body.result;
  renderFixResult(body.result);
});

function renderFixErrors(scoreErrs) {
  fixErrors.innerHTML = "";
  const h = document.createElement("h3");
  h.textContent = `录入谱面行级校验未通过（${scoreErrs.length} 项），请先修正后再纠错`;
  fixErrors.appendChild(h);
  const ul = document.createElement("ul");
  scoreErrs.forEach((e) => {
    const li = document.createElement("li");
    if (e.index !== null && e.index !== undefined) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "link";
      btn.textContent = `第 ${e.index + 1} 行`
        + `${e.measure ? `（编号 ${e.measure}）` : ""}`;
      btn.addEventListener("click", () => jumpToRow(e.index));
      li.appendChild(btn);
      li.appendChild(document.createTextNode(`：${e.message}`));
    } else {
      li.textContent = e.message;
    }
    ul.appendChild(li);
  });
  fixErrors.appendChild(ul);
  fixErrors.classList.remove("hidden");
  markInvalidRows(scoreErrs); // 谱面错误行在录入表标红
}

function renderFixResult(result) {
  fixBody.classList.remove("hidden");

  fixStats.innerHTML = "";
  const stat = document.createElement("span");
  if (result.already_valid) {
    stat.textContent = `原谱已通过现有完整校验并正常展开（${result.visit_count} 次访问），无需纠错。`;
    fixStats.appendChild(stat);
    fixEmpty.classList.add("hidden");
    fixList.innerHTML = "";
    return;
  }
  stat.textContent = `最少切换 ${result.minimal_toggles || "—"} 处 · `
    + `访问上限 ${result.max_visits} · 切换预算 ${result.max_toggles} 处`;
  fixStats.appendChild(stat);

  fixList.innerHTML = "";
  if (result.solution_count === 0) {
    fixEmpty.textContent = `在最多 ${result.max_toggles} 处右反复终点切换内，`
      + "找不到能通过现有完整校验且展开访问数不超过 "
      + `${result.max_visits} 的谱面 —— 无方案。`;
    fixEmpty.classList.remove("hidden");
    return;
  }
  fixEmpty.classList.add("hidden");

  result.solutions.forEach((sol, k) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fix-item";
    btn.title = "点击只把该方案的右反复标记写回表格（其余字段不变）";

    const badge = document.createElement("span");
    badge.className = "fix-badge";
    badge.textContent = `方案 ${k + 1}`;
    btn.appendChild(badge);

    const body = document.createElement("span");
    body.className = "fix-item-body";
    const head = document.createElement("span");
    head.className = "fix-item-head";
    head.textContent = `切换 ${sol.toggles} 处 · 展开 ${sol.visit_count} 次访问`;
    body.appendChild(head);
    const detail = document.createElement("span");
    detail.className = "fix-changes";
    detail.textContent = sol.changes.map((c) => {
      const where = `第 ${c.index + 1} 行${c.measure ? `（${c.measure}）` : ""}`;
      return `${c.action === "add" ? "新增" : "删除"} ${where} 的 :|`;
    }).join("；");
    body.appendChild(detail);
    btn.appendChild(body);

    btn.addEventListener("click", () => applyFixSolution(btn, sol));
    li.appendChild(btn);
    fixList.appendChild(li);
  });
}

/* 点击方案：只写回各行右反复标记 → 旧演奏带 / 节拍器 / 对照 / 反求全部失效 */
function applyFixSolution(btn, sol) {
  if (!lastFix) return;
  sol.changes.forEach((c) => {
    rows[c.index].right_repeat = (c.action === "add");
  });
  renderTable();
  invalidatePlan(); // 写回右标记 = 谱面改动：演奏带 / 节拍器 / 对照失效
  markSolveStale(); // 反求结果同样过期
  fixList.querySelectorAll(".fix-item")
    .forEach((el) => el.classList.remove("applied"));
  btn.classList.add("applied");
  showFixNote("方案的右反复标记已写回表格（其余字段未改动）；旧演奏带、节拍器计划、"
    + "改谱对照与反求结果已失效，请重新展开 / 对照 / 反求。");
}

/* =========================================================================
   排练记录定位
   -------------------------------------------------------------------------
   - POST /api/locate：谱面先复用现有校验与展开；排练记录（逗号或换行
     分隔，1~64 项，可含谱面外误记编号）与演奏带的任意非空连续区间做
     编辑距离匹配（相同 0 / 替换、删除、补入各 1，区间外不计）；
   - 返回距离 ≤ 容错数的全部区间，按 (距离, 起点, 终点) 升序前 10 项
     及总数；点击结果项在演奏带上高亮对应访问区间（不改谱面）；
   - 谱面、记录或容错数改变后旧结果立即清除；用自增请求序号保证
     迟到响应不会恢复已过期的结果。
   ======================================================================== */

const MAX_LOCATE_ITEMS = 64;

let lastLocate = null;  // 最近一次成功的定位结果（须对应当前演奏带）
let locateSeq = 0;      // 在途请求序号：只接受最新一次的响应
let locateSelected = null; // 当前高亮的命中项序号（结果列表下标）
let locateSig = "";     // 定位时使用的谱面快照签名（JSON 字符串），用于同谱重展保留高亮

const locateInput = document.getElementById("locate-input");
const locateTolerance = document.getElementById("locate-tolerance");
const btnLocate = document.getElementById("btn-locate");
const locateNote = document.getElementById("locate-note");
const locateErrors = document.getElementById("locate-errors");
const locateBody = document.getElementById("locate-body");
const locateSummary = document.getElementById("locate-summary");
const locateEmpty = document.getElementById("locate-empty");
const locateList = document.getElementById("locate-list");

const LOCATE_OP_TEXT = {
  match: "相同",
  substitute: "替换",
  delete: "删除",
  insert: "补入",
};

function showLocateNote(text) {
  locateNote.textContent = text;
  locateNote.classList.remove("hidden");
}

function hideLocatePanels() {
  locateBody.classList.add("hidden");
  locateErrors.classList.add("hidden");
  locateNote.classList.add("hidden");
}

/* 清除演奏带上的定位高亮（不影响双向高亮 / 节拍器高亮） */
function clearLocateHighlight() {
  visitStrip.querySelectorAll(".visit-card.loc-hit")
    .forEach((card) => card.classList.remove("loc-hit"));
  locateSelected = null;
}

/* 谱面、记录或容错数改变后：旧定位结果立即清除，迟到响应也不得恢复 */
function markLocateStale() {
  locateSeq += 1; // 在途响应一律作废
  lastLocate = null;
  locateSelected = null;
  clearLocateHighlight();
  locateList.querySelectorAll(".locate-item")
    .forEach((el) => el.classList.remove("applied"));
  if (locateBody.classList.contains("hidden")
      && locateErrors.classList.contains("hidden")
      && locateNote.classList.contains("hidden")) {
    return;
  }
  hideLocatePanels();
  showLocateNote("谱面、记录或容错数已改动，定位结果已过期，请重新定位。");
}

locateInput.addEventListener("input", markLocateStale);
locateTolerance.addEventListener("input", markLocateStale);

btnLocate.addEventListener("click", async () => {
  clearErrors();
  hideLocatePanels();
  clearLocateHighlight();
  locateList.innerHTML = "";
  lastLocate = null;

  if (!lastResult || planStale) {
    showLocateNote("请先成功展开演奏带，再进行排练记录定位。");
    return;
  }

  const record = parseTarget(locateInput.value); // 逗号（中英文、顿号）或换行分隔
  if (record.length === 0) {
    showLocateNote("请填写排练记录小节编号（逗号或换行分隔）。");
    return;
  }
  if (record.length > MAX_LOCATE_ITEMS) {
    showLocateNote(`排练记录最多 ${MAX_LOCATE_ITEMS} 项，当前 ${record.length} 项。`);
    return;
  }
  const toleranceRaw = locateTolerance.value === "" ? null : Number(locateTolerance.value);
  if (!Number.isInteger(toleranceRaw) || toleranceRaw < 0 || toleranceRaw > 4) {
    showLocateNote("容错数必须是 0~4 的整数。");
    return;
  }

  const seq = ++locateSeq;
  btnLocate.disabled = true;
  showLocateNote("正在定位…");
  let resp;
  try {
    resp = await fetch("/api/locate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        measures: collectPayload(),
        record: locateInput.value,
        tolerance: toleranceRaw,
      }),
    });
  } catch (e) {
    if (seq !== locateSeq) return;
    btnLocate.disabled = false;
    showLocateNote("无法连接服务器，请确认后端正在运行。");
    return;
  }
  if (seq !== locateSeq) return; // 迟到响应：谱面 / 记录 / 容错已变更，直接丢弃
  btnLocate.disabled = false;

  const body = await resp.json();
  if (seq !== locateSeq) return;
  if (!body.ok) {
    renderLocateErrors(body);
    return;
  }
  lastLocate = body.result;
  locateSig = JSON.stringify(collectPayload());
  renderLocateResult(body.result);
});

function renderLocateErrors(body) {
  locateErrors.innerHTML = "";
  const blocks = [
    ["录入谱面校验未通过", body.score_errors || [], true],
    ["排练记录有误", body.record_errors || [], false],
    ["容错数有误", body.tolerance_errors || [], false],
  ].filter(([, errs]) => errs.length);

  blocks.forEach(([title, errs, jumpable]) => {
    const h = document.createElement("h3");
    h.textContent = `${title}（${errs.length} 项）`;
    locateErrors.appendChild(h);
    const ul = document.createElement("ul");
    errs.forEach((e) => {
      const li = document.createElement("li");
      if (jumpable && e.index !== null && e.index !== undefined) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "link";
        btn.textContent = `第 ${e.index + 1} 行`
          + `${e.measure ? `（编号 ${e.measure}）` : ""}`;
        btn.addEventListener("click", () => jumpToRow(e.index));
        li.appendChild(btn);
        li.appendChild(document.createTextNode(`：${e.message}`));
      } else {
        li.textContent = e.message;
      }
      ul.appendChild(li);
    });
    locateErrors.appendChild(ul);
  });
  locateErrors.classList.remove("hidden");

  const scoreErrs = body.score_errors || [];
  if (scoreErrs.length) markInvalidRows(scoreErrs);
}

function locatePathLabel(path) {
  if (!path || path.length === 0) return "段外";
  return path.map((p) => `${p.pass}/${p.times}${p.tail ? "尾" : ""}`).join("›");
}

function alignmentHTML(alignment) {
  return alignment.map((s) => {
    const opClass = `loc-op loc-op-${s.op}`;
    const rec = s.record_item === null || s.record_item === undefined
      ? '<span class="loc-gap">·</span>' : escapeHTML(s.record_item);
    const vis = s.visit
      ? `<span class="loc-vis">#${s.visit.global_index} ${escapeHTML(s.visit.source_measure)}</span>`
      : '<span class="loc-gap">·</span>';
    return `<li class="${opClass}">`
      + `<span class="loc-op-name">${LOCATE_OP_TEXT[s.op]}</span>`
      + `<span class="loc-pair"><span class="loc-rec">${rec}</span>`
      + `<span class="loc-arrow">→</span>${vis}</span></li>`;
  }).join("");
}

function renderLocateResult(result) {
  locateNote.classList.add("hidden");
  locateBody.classList.remove("hidden");

  locateSummary.innerHTML = "";
  [
    `记录 ${result.record_count} 项 · 容错 ${result.tolerance}`,
    `演奏带 ${result.visit_count} 次访问`,
    `命中区间 ${result.match_count} 个`
      + (result.match_count > result.shown_count
        ? `（按距离、起点、终点升序显示前 ${result.shown_count} 个）` : ""),
  ].forEach((text) => {
    const span = document.createElement("span");
    span.textContent = text;
    locateSummary.appendChild(span);
  });

  locateList.innerHTML = "";
  if (result.match_count === 0) {
    // 无结果：必须明确提示
    locateEmpty.textContent = `在 ${result.visit_count} 次访问的演奏带中，`
      + `找不到与排练记录编辑距离不超过 ${result.tolerance} 的非空连续区间 —— 无匹配区间。`
      + "可尝试增大容错数（0~4）或核对记录。";
    locateEmpty.classList.remove("hidden");
    return;
  }
  locateEmpty.classList.add("hidden");

  result.matches.forEach((mt, k) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "locate-item";
    btn.title = "点击在演奏带上高亮该访问区间（不改动谱面）";

    const head = document.createElement("span");
    head.className = "locate-item-head";
    head.innerHTML =
      `<span class="locate-rank">区间 ${k + 1}</span>`
      + `<span class="locate-dist">距离 ${mt.distance}</span>`
      + `<span class="locate-range">访问 #${mt.start}–#${mt.end}`
      + `（${mt.interval_length} 次）</span>`
      + `<span class="locate-paths">起 ${escapeHTML(locatePathLabel(mt.start_path))}`
      + ` · 止 ${escapeHTML(locatePathLabel(mt.end_path))}</span>`;
    btn.appendChild(head);

    const align = document.createElement("ul");
    align.className = "locate-align";
    align.innerHTML = alignmentHTML(mt.alignment);
    btn.appendChild(align);

    btn.addEventListener("click", () => {
      if (locateSelected === k) {
        clearLocateHighlight();
        btn.classList.remove("applied");
        return;
      }
      highlightLocateMatch(k);
      locateList.querySelectorAll(".locate-item")
        .forEach((el) => el.classList.remove("applied"));
      btn.classList.add("applied");
    });
    li.appendChild(btn);
    locateList.appendChild(li);
  });
}

/* 点击结果：高亮演奏带上 [start, end] 的访问卡片（序号为 1 基全局序号） */
function highlightLocateMatch(k) {
  if (!lastLocate) return;
  const mt = lastLocate.matches[k];
  if (!mt) return;
  clearLocateHighlight();
  locateSelected = k;
  let firstCard = null;
  visitStrip.querySelectorAll(".visit-card").forEach((card) => {
    const gi = Number(card.dataset.visit) + 1; // data-visit 为 0 基
    if (gi >= mt.start && gi <= mt.end) {
      card.classList.add("loc-hit");
      if (!firstCard) firstCard = card;
    }
  });
  firstCard?.scrollIntoView(
    { behavior: "smooth", block: "nearest", inline: "center" });
}

/* =========================================================================
   遍次排练提示单
   -------------------------------------------------------------------------
   - 录入 1~32 条提示：来源小节 + 节内拍序（1 起）+ 1~80 字提示语，
     可按反复起点限定一层或多层遍次（未限定的层级匹配任意遍）；
   - POST /api/cues：服务端复用现有校验与展开，逐条校验后按每次访问的
     完整遍次路径（含终点后房子的尾部路径）匹配，命中提示落到对应访问
     的指定拍，按此前访问拍数与生效 BPM 累计秒数，依时间与录入顺序返回；
   - 点击条目即在演奏带中定位该次访问（与点击卡片一致的暂停定位）；
   - 谱面或提示改变后旧单立即清除；自增请求序号保证迟到响应不会恢复。
   ======================================================================== */

const MAX_CUES = 32;
const MAX_CUE_TEXT = 80;

let cueRows = [];       // 提示录入行：{key, measure, beat, text, passes:[{key, open, pass}]}
let lastCues = null;    // 最近一次成功的提示单结果
let cueSeq = 0;         // 在途请求序号：只接受最新一次的响应
let cuesSig = "";       // 生成提示单时的谱面签名（同谱重展不误判过期）
let cueSelected = null; // 当前定位的条目下标

const cueEditor = document.getElementById("cue-editor");
const cueMeasureList = document.getElementById("cue-measure-list");
const cuesCount = document.getElementById("cues-count");
const btnCueAdd = document.getElementById("btn-cue-add");
const btnCues = document.getElementById("btn-cues");
const cuesNote = document.getElementById("cues-note");
const cuesErrors = document.getElementById("cues-errors");
const cuesBody = document.getElementById("cues-body");
const cuesSummary = document.getElementById("cues-summary");
const cuesList = document.getElementById("cues-list");

function blankCue() {
  return { key: uid(), measure: "", beat: 1, text: "", passes: [] };
}

/* 当前表格中带左反复的小节（遍次限定的反复起点候选） */
function repeatStarts() {
  const starts = [];
  rows.forEach((r, i) => {
    if (r.left_repeat) {
      starts.push({ index: i, number: r.number || "?", times: r.times || "?" });
    }
  });
  return starts;
}

function renderCueEditor() {
  // 来源小节候选（当前表格编号）
  cueMeasureList.innerHTML = "";
  rows.forEach((r) => {
    if (!r.number) return;
    const opt = document.createElement("option");
    opt.value = r.number;
    cueMeasureList.appendChild(opt);
  });

  const starts = repeatStarts();
  cueEditor.innerHTML = "";
  cueRows.forEach((cue, ci) => {
    const card = document.createElement("div");
    card.className = "cue-card";
    card.dataset.index = String(ci);

    const head = document.createElement("div");
    head.className = "cue-head";
    const no = document.createElement("span");
    no.className = "cue-no";
    no.textContent = `提示 ${ci + 1}`;
    head.appendChild(no);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-del cue-del";
    del.title = "删除该提示";
    del.textContent = "✕";
    head.appendChild(del);
    card.appendChild(head);

    const fields = document.createElement("div");
    fields.className = "cue-fields";
    fields.innerHTML = `
      <label>来源小节
        <input type="text" data-cue-field="measure" list="cue-measure-list"
               maxlength="16" placeholder="编号"
               value="${escapeHTML(cue.measure || "")}"></label>
      <label>拍序
        <input type="number" data-cue-field="beat" min="1" max="128" step="1"
               value="${cue.beat === "" ? "" : cue.beat}"></label>
      <label class="cue-text-label">提示语（1~${MAX_CUE_TEXT} 字）
        <input type="text" data-cue-field="text" maxlength="${MAX_CUE_TEXT}"
               placeholder="例如：注意换气"
               value="${escapeHTML(cue.text || "")}"></label>`;
    card.appendChild(fields);

    const passes = document.createElement("div");
    passes.className = "cue-passes";
    const plabel = document.createElement("span");
    plabel.className = "cue-passes-label";
    plabel.textContent = cue.passes.length
      ? "遍次限定（未限定的层级匹配任意遍）"
      : "不限定遍次（匹配该小节的全部访问）";
    passes.appendChild(plabel);

    cue.passes.forEach((p, pi) => {
      const prow = document.createElement("div");
      prow.className = "cue-pass";
      prow.dataset.index = String(pi);

      const sel = document.createElement("select");
      sel.dataset.passField = "open";
      sel.title = "按反复起点限定该层遍次";
      const hasOpen = p.open !== "" && p.open !== null && p.open !== undefined;
      const ph = document.createElement("option");
      ph.value = "";
      ph.textContent = starts.length ? "选择反复起点" : "（谱面暂无反复起点）";
      sel.appendChild(ph);
      let matched = false;
      starts.forEach((s) => {
        const opt = document.createElement("option");
        opt.value = String(s.index);
        opt.textContent = `第 ${s.index + 1} 行 ${s.number} 起（×${s.times}）`;
        if (hasOpen && Number(p.open) === s.index) matched = true;
        sel.appendChild(opt);
      });
      if (hasOpen && !matched) {
        // 谱面改动后起点行已失效：保留原值展示，交给服务端校验报错
        const opt = document.createElement("option");
        opt.value = String(p.open);
        opt.textContent = `第 ${Number(p.open) + 1} 行（起点已失效）`;
        sel.appendChild(opt);
      }
      sel.value = hasOpen ? String(p.open) : "";
      prow.appendChild(sel);

      const passInput = document.createElement("input");
      passInput.type = "number";
      passInput.min = "1";
      passInput.max = "4";
      passInput.step = "1";
      passInput.dataset.passField = "pass";
      passInput.title = "限定遍次（1~该反复次数）";
      passInput.value = p.pass === "" ? "" : String(p.pass);
      prow.appendChild(passInput);

      const pdel = document.createElement("button");
      pdel.type = "button";
      pdel.className = "btn-del cue-pass-del";
      pdel.title = "删除该遍次限定";
      pdel.textContent = "✕";
      prow.appendChild(pdel);
      passes.appendChild(prow);
    });

    const addPass = document.createElement("button");
    addPass.type = "button";
    addPass.className = "btn btn-small cue-pass-add";
    addPass.textContent = "＋ 限定遍次";
    passes.appendChild(addPass);
    card.appendChild(passes);

    cueEditor.appendChild(card);
  });

  cuesCount.textContent = `${cueRows.length} / ${MAX_CUES} 条提示`;
  btnCueAdd.disabled = cueRows.length >= MAX_CUES;
}

cueEditor.addEventListener("input", (ev) => {
  const el = ev.target;
  const card = el.closest(".cue-card");
  if (!card) return;
  const cue = cueRows[Number(card.dataset.index)];
  if (!cue) return;
  const cueField = el.dataset.cueField;
  if (cueField) {
    if (cueField === "beat") {
      cue.beat = el.value === "" ? "" : Number(el.value);
    } else {
      cue[cueField] = el.value;
    }
    markCuesStale();
    return;
  }
  const passField = el.dataset.passField;
  if (passField) {
    const prow = el.closest(".cue-pass");
    const p = cue.passes[Number(prow.dataset.index)];
    if (!p) return;
    p[passField] = el.value === "" ? "" : Number(el.value);
    markCuesStale();
  }
});

cueEditor.addEventListener("change", (ev) => {
  // select 的 input 事件在部分浏览器不触发，这里兜底同步
  const el = ev.target;
  if (el.dataset.passField !== "open") return;
  const card = el.closest(".cue-card");
  const prow = el.closest(".cue-pass");
  if (!card || !prow) return;
  const cue = cueRows[Number(card.dataset.index)];
  const p = cue && cue.passes[Number(prow.dataset.index)];
  if (!p) return;
  const value = el.value === "" ? "" : Number(el.value);
  if (p.open !== value) {
    p.open = value;
    markCuesStale();
  }
});

cueEditor.addEventListener("click", (ev) => {
  const delCue = ev.target.closest(".cue-del");
  if (delCue) {
    cueRows.splice(Number(delCue.closest(".cue-card").dataset.index), 1);
    renderCueEditor();
    markCuesStale();
    return;
  }
  const addPass = ev.target.closest(".cue-pass-add");
  if (addPass) {
    const cue = cueRows[Number(addPass.closest(".cue-card").dataset.index)];
    const starts = repeatStarts();
    cue.passes.push({
      key: uid(),
      open: starts.length ? starts[0].index : "",
      pass: 1,
    });
    renderCueEditor();
    markCuesStale();
    return;
  }
  const delPass = ev.target.closest(".cue-pass-del");
  if (delPass) {
    const cue = cueRows[Number(delPass.closest(".cue-card").dataset.index)];
    cue.passes.splice(Number(delPass.closest(".cue-pass").dataset.index), 1);
    renderCueEditor();
    markCuesStale();
  }
});

btnCueAdd.addEventListener("click", () => {
  if (cueRows.length >= MAX_CUES) return;
  cueRows.push(blankCue());
  renderCueEditor();
  markCuesStale();
  cueEditor.lastElementChild?.scrollIntoView({ block: "nearest" });
});

function showCuesNote(text) {
  cuesNote.textContent = text;
  cuesNote.classList.remove("hidden");
}

function hideCuesPanels() {
  cuesBody.classList.add("hidden");
  cuesErrors.classList.add("hidden");
  cuesNote.classList.add("hidden");
}

/* 谱面或提示改变后：旧提示单立即清除，迟到响应不得恢复 */
function markCuesStale() {
  cueSeq += 1; // 在途响应一律作废
  lastCues = null;
  cueSelected = null;
  btnCues.disabled = false; // 在途响应被丢弃后允许立即重新生成
  if (cuesBody.classList.contains("hidden")
      && cuesErrors.classList.contains("hidden")
      && cuesNote.classList.contains("hidden")) {
    return;
  }
  hideCuesPanels();
  showCuesNote("谱面或提示已改动，提示单已过期，请重新生成。");
}

function jumpToCue(i) {
  const card = cueEditor.querySelector(`.cue-card[data-index="${i}"]`);
  cueEditor.querySelectorAll(".cue-flash")
    .forEach((el) => el.classList.remove("cue-flash"));
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("cue-flash");
    card.querySelector("input")?.focus({ preventScroll: true });
  }
}

function collectCues() {
  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
  return cueRows.map((c) => ({
    measure: String(c.measure ?? "").trim(),
    beat: num(c.beat),
    text: String(c.text ?? "").trim(),
    passes: c.passes.map((p) => ({ open: num(p.open), pass: num(p.pass) })),
  }));
}

btnCues.addEventListener("click", async () => {
  clearErrors();
  hideCuesPanels();
  lastCues = null;
  cueSelected = null;

  if (!lastResult || planStale) {
    showCuesNote("请先成功展开演奏带，再生成遍次排练提示单。");
    return;
  }
  if (cueRows.length < 1 || cueRows.length > MAX_CUES) {
    showCuesNote(`请录入 1~${MAX_CUES} 条提示（当前 ${cueRows.length} 条）。`);
    return;
  }
  for (let i = 0; i < cueRows.length; i += 1) {
    const c = cueRows[i];
    if (!String(c.measure ?? "").trim()) {
      showCuesNote(`第 ${i + 1} 条提示缺少来源小节编号。`);
      jumpToCue(i);
      return;
    }
    if (c.beat === "" || c.beat === null || c.beat === undefined) {
      showCuesNote(`第 ${i + 1} 条提示缺少拍序。`);
      jumpToCue(i);
      return;
    }
    if (!String(c.text ?? "").trim()) {
      showCuesNote(`第 ${i + 1} 条提示缺少提示语。`);
      jumpToCue(i);
      return;
    }
  }

  const seq = ++cueSeq;
  btnCues.disabled = true;
  showCuesNote("正在生成提示单…");
  let resp;
  try {
    resp = await fetch("/api/cues", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ measures: collectPayload(), cues: collectCues() }),
    });
  } catch (e) {
    if (seq !== cueSeq) return;
    btnCues.disabled = false;
    showCuesNote("无法连接服务器，请确认后端正在运行。");
    return;
  }
  if (seq !== cueSeq) return; // 迟到响应：谱面 / 提示已变更，直接丢弃
  btnCues.disabled = false;

  const body = await resp.json();
  if (seq !== cueSeq) return;
  if (!body.ok) {
    renderCuesErrors(body);
    return;
  }
  lastCues = body.result;
  cuesSig = JSON.stringify(collectPayload());
  renderCuesResult(body.result);
});

function renderCuesErrors(body) {
  cuesErrors.innerHTML = "";
  const blocks = [
    ["录入谱面校验未通过", body.score_errors || [], "score"],
    ["提示录入有误", body.cue_errors || [], "cue"],
  ].filter(([, errs]) => errs.length);

  blocks.forEach(([title, errs, kind]) => {
    const h = document.createElement("h3");
    h.textContent = `${title}（${errs.length} 项）`;
    cuesErrors.appendChild(h);
    const ul = document.createElement("ul");
    errs.forEach((e) => {
      const li = document.createElement("li");
      if (e.index !== null && e.index !== undefined) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "link";
        btn.textContent = kind === "score"
          ? `第 ${e.index + 1} 行${e.measure ? `（编号 ${e.measure}）` : ""}`
          : `第 ${e.index + 1} 条提示${e.measure ? `（${e.measure}）` : ""}`;
        btn.addEventListener("click", () => {
          if (kind === "score") jumpToRow(e.index);
          else jumpToCue(e.index);
        });
        li.appendChild(btn);
        li.appendChild(document.createTextNode(`：${e.message}`));
      } else {
        li.textContent = e.message;
      }
      ul.appendChild(li);
    });
    cuesErrors.appendChild(ul);
  });
  cuesErrors.classList.remove("hidden");

  const scoreErrs = body.score_errors || [];
  if (scoreErrs.length) markInvalidRows(scoreErrs); // 谱面错误行在录入表标红
}

function renderCuesResult(result) {
  cuesNote.classList.add("hidden");
  cuesBody.classList.remove("hidden");

  cuesSummary.innerHTML = "";
  [
    `提示 ${result.cue_count} 条 · 命中 ${result.hit_count} 项`,
    `演奏带 ${result.visit_count} 次访问 · 全长 ${fmtSec(result.total_seconds)}`,
    "按时间与录入顺序排列",
  ].forEach((text) => {
    const span = document.createElement("span");
    span.textContent = text;
    cuesSummary.appendChild(span);
  });

  cuesList.innerHTML = "";
  result.entries.forEach((e, k) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cue-entry";
    btn.title = "点击在演奏带中定位该次访问（暂停于该访问强拍）";

    const head = document.createElement("span");
    head.className = "cue-entry-head";
    head.innerHTML =
      `<span class="cue-rank">${k + 1}</span>`
      + `<span class="cue-time">${fmtSec(e.seconds)}</span>`
      + `<span class="cue-visit">访问 #${e.visit.global_index}`
      + ` · ${escapeHTML(e.visit.source_measure)} · 第 ${e.beat} 拍</span>`
      + `<span class="cue-path">${escapeHTML(pathLabel(e.visit.path))}</span>`
      + `<span class="cue-from">第 ${e.cue_index + 1} 条</span>`;
    btn.appendChild(head);

    const text = document.createElement("span");
    text.className = "cue-entry-text";
    text.textContent = e.text;
    btn.appendChild(text);

    btn.addEventListener("click", () => {
      cueSelected = k;
      cuesList.querySelectorAll(".cue-entry")
        .forEach((el) => el.classList.remove("applied"));
      btn.classList.add("applied");
      locateCueEntry(e);
    });
    li.appendChild(btn);
    cuesList.appendChild(li);
  });
}

/* 点击条目：定位演奏带中的那次访问（与点击卡片一致的暂停定位） */
function locateCueEntry(entry) {
  if (!lastResult || planStale) return;
  const idx = entry.visit.global_index - 1;
  transport.seekVisit(idx);
  visitStrip.querySelector(`.visit-card[data-visit="${idx}"]`)
    ?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
}

/* =========================================================================
   演奏片段摘谱
   -------------------------------------------------------------------------
   - 在已展开的演奏带上按访问序号选 1~4 段互不重叠、按演奏先后排列的闭
     区间（合计 2~64 次访问；同一来源小节不同遍次可重复选入）；
   - POST /api/excerpt：服务端先复用现有校验与展开，把每次选中访问依序
     变成独立录入行（编号重排为 1 起连续整数、保留拍数、清除反复与房子
     记号；每段首行写生效 BPM、段内仅速度变化时写），再用现有结构导出
     生成单 part MusicXML，并回读逐行核对拍数与生效 BPM；
   - 页面预览新行与原访问序号 / 来源编号 / 遍次路径映射并提供下载；
   - 原谱与演奏带不变；谱面或选段变化后旧预览立即清除，自增请求序号
     保证迟到响应不得恢复。
   ======================================================================== */

const MAX_EXCERPT_SEGMENTS = 4;
const MIN_EXCERPT_VISITS = 2;
const MAX_EXCERPT_VISITS = 64;

let excerptSegments = [];  // 选段：{key, start, end}（1 基访问序号）
let lastExcerpt = null;    // 最近一次成功的摘谱结果
let excerptSeq = 0;        // 在途请求序号：只接受最新一次的响应
let excerptSig = "";       // 生成摘谱时的谱面签名（同谱重展不误判过期）
let excerptPickMode = false;   // 是否处于演奏带选段模式
let excerptPickStart = null;  // 选段模式下已点的起点（1 基访问序号，null=待选起点）

const excerptEditor = document.getElementById("excerpt-editor");
const excerptCount = document.getElementById("excerpt-count");
const btnExcerptPick = document.getElementById("btn-excerpt-pick");
const btnExcerptAdd = document.getElementById("btn-excerpt-add");
const btnExcerpt = document.getElementById("btn-excerpt");
const excerptNote = document.getElementById("excerpt-note");
const excerptErrors = document.getElementById("excerpt-errors");
const excerptBody = document.getElementById("excerpt-body");
const excerptSummary = document.getElementById("excerpt-summary");
const excerptPreview = document.getElementById("excerpt-preview");
const btnExcerptDownload = document.getElementById("btn-excerpt-download");

function excerptSegLenText(seg) {
  if (seg.start === "" || seg.end === "") return "—";
  const s = Number(seg.start);
  const e = Number(seg.end);
  if (!Number.isInteger(s) || !Number.isInteger(e) || s > e) return "—";
  return `${e - s + 1} 次访问`;
}

/* 摘要：已选段数与合计访问数（非法段不计入） */
function updateExcerptCount() {
  let total = 0;
  excerptSegments.forEach((seg) => {
    if (seg.start === "" || seg.end === "") return;
    const s = Number(seg.start);
    const e = Number(seg.end);
    if (Number.isInteger(s) && Number.isInteger(e) && e >= s) total += e - s + 1;
  });
  excerptCount.textContent = excerptSegments.length
    ? `已选 ${excerptSegments.length} / ${MAX_EXCERPT_SEGMENTS} 段`
      + ` · 合计 ${total} 次访问`
    : "未选段";
  btnExcerptAdd.disabled = excerptSegments.length >= MAX_EXCERPT_SEGMENTS;
}

/* 在演奏带上高亮当前选段覆盖的访问卡片（不改动谱面，仅视觉） */
function paintExcerptSelection() {
  const inSegment = new Set();
  excerptSegments.forEach((seg) => {
    if (seg.start === "" || seg.end === "") return;
    const s = Number(seg.start);
    const e = Number(seg.end);
    if (!Number.isInteger(s) || !Number.isInteger(e) || s > e) return;
    for (let gi = s; gi <= e; gi += 1) inSegment.add(gi);
  });
  visitStrip.querySelectorAll(".visit-card").forEach((card) => {
    const gi = Number(card.dataset.visit) + 1; // data-visit 为 0 基
    card.classList.toggle("excerpt-hit", inSegment.has(gi));
    card.classList.toggle("excerpt-pick-start",
      excerptPickMode && excerptPickStart === gi);
  });
  visitStrip.classList.toggle("excerpt-picking", excerptPickMode);
}

function renderExcerptEditor() {
  excerptEditor.innerHTML = "";
  excerptSegments.forEach((seg, i) => {
    const row = document.createElement("div");
    row.className = "excerpt-seg";
    row.dataset.index = String(i);
    row.innerHTML = `
      <span class="excerpt-seg-no">第 ${i + 1} 段</span>
      <label>起点 <input type="number" min="1" step="1"
                 data-seg-field="start"
                 value="${seg.start === "" ? "" : seg.start}"></label>
      <span class="excerpt-seg-dash">～</span>
      <label>终点 <input type="number" min="1" step="1"
                 data-seg-field="end"
                 value="${seg.end === "" ? "" : seg.end}"></label>
      <span class="excerpt-seg-len">${excerptSegLenText(seg)}</span>
      <button type="button" class="btn-del excerpt-seg-del"
              title="删除该选段">✕</button>`;
    excerptEditor.appendChild(row);
  });
  updateExcerptCount();
  paintExcerptSelection();
}

/* 选段模式：点击演奏带卡片 → 依次记录起点、终点（1 基访问序号） */
function excerptPick(gi) {
  if (!lastResult || planStale) return;
  if (excerptPickStart === null) {
    excerptPickStart = gi;
    paintExcerptSelection(); // 标出已选起点
    return;
  }
  const s = Math.min(excerptPickStart, gi);
  const e = Math.max(excerptPickStart, gi); // 同卡两次 = 单访问段
  excerptPickStart = null;
  if (excerptSegments.length >= MAX_EXCERPT_SEGMENTS) {
    renderExcerptEditor(); // 清除起点标记
    showExcerptNote(`最多 ${MAX_EXCERPT_SEGMENTS} 段；请先删除一段再继续选段。`);
    return;
  }
  excerptSegments.push({ key: uid(), start: s, end: e });
  renderExcerptEditor();
  markExcerptStale();
}

/* 同步选段按钮可用态；计划失效时选段模式一并退出 */
function syncExcerptPickButton() {
  const ok = !!lastResult && !planStale;
  if (!ok) {
    // 计划失效：选段模式一并退出
    excerptPickMode = false;
    excerptPickStart = null;
  }
  btnExcerptPick.disabled = !ok;
  btnExcerptPick.textContent = excerptPickMode ? "退出选段" : "在演奏带上选段";
  btnExcerptPick.classList.toggle("btn-active", excerptPickMode);
  paintExcerptSelection();
}

function showExcerptNote(text) {
  excerptNote.textContent = text;
  excerptNote.classList.remove("hidden");
}

function hideExcerptPanels() {
  excerptBody.classList.add("hidden");
  excerptErrors.classList.add("hidden");
  excerptNote.classList.add("hidden");
}

/* 谱面或选段改变后：旧摘谱预览立即清除，迟到响应不得恢复 */
function markExcerptStale() {
  excerptSeq += 1; // 在途响应一律作废
  lastExcerpt = null;
  btnExcerpt.disabled = false;
  paintExcerptSelection();
  if (excerptBody.classList.contains("hidden")
      && excerptErrors.classList.contains("hidden")
      && excerptNote.classList.contains("hidden")) {
    return;
  }
  hideExcerptPanels();
  showExcerptNote("谱面或选段已改动，摘谱预览已过期，请重新生成。");
}

excerptEditor.addEventListener("input", (ev) => {
  const el = ev.target;
  const field = el.dataset.segField;
  if (!field) return;
  const row = el.closest(".excerpt-seg");
  const seg = excerptSegments[Number(row.dataset.index)];
  if (!seg) return;
  seg[field] = el.value === "" ? "" : Number(el.value);
  // 只刷新长度文字与摘要（不整体重绘，避免输入框失焦）
  row.querySelector(".excerpt-seg-len").textContent = excerptSegLenText(seg);
  updateExcerptCount();
  markExcerptStale();
});

excerptEditor.addEventListener("click", (ev) => {
  const del = ev.target.closest(".excerpt-seg-del");
  if (!del) return;
  excerptSegments.splice(Number(del.closest(".excerpt-seg").dataset.index), 1);
  renderExcerptEditor();
  markExcerptStale();
});

btnExcerptPick.addEventListener("click", () => {
  if (!lastResult || planStale) return;
  excerptPickMode = !excerptPickMode;
  excerptPickStart = null;
  syncExcerptPickButton();
  if (excerptPickMode) {
    showExcerptNote("选段模式：依次点击该段的起点与终点卡片"
      + "（再次点击同一张卡片为单访问段）；可选多段，完成后点“退出选段”。");
  }
});

btnExcerptAdd.addEventListener("click", () => {
  if (excerptSegments.length >= MAX_EXCERPT_SEGMENTS) return;
  const vc = lastResult && !planStale ? lastResult.visit_count : 0;
  if (vc >= MIN_EXCERPT_VISITS) {
    excerptSegments.push({ key: uid(), start: 1, end: 2 });
  } else {
    excerptSegments.push({ key: uid(), start: "", end: "" });
  }
  renderExcerptEditor();
  markExcerptStale();
  excerptEditor.lastElementChild?.scrollIntoView({ block: "nearest" });
});

function jumpToExcerptSeg(i) {
  const row = excerptEditor.querySelector(`.excerpt-seg[data-index="${i}"]`);
  excerptEditor.querySelectorAll(".excerpt-flash")
    .forEach((el) => el.classList.remove("excerpt-flash"));
  if (row) {
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("excerpt-flash");
    row.querySelector("input")?.focus({ preventScroll: true });
  }
}

btnExcerpt.addEventListener("click", async () => {
  clearErrors();
  hideExcerptPanels();
  lastExcerpt = null;

  if (!lastResult || planStale) {
    showExcerptNote("请先成功展开演奏带，再进行演奏片段摘谱。");
    return;
  }
  if (excerptSegments.length < 1
      || excerptSegments.length > MAX_EXCERPT_SEGMENTS) {
    showExcerptNote(
      `请选择 1~${MAX_EXCERPT_SEGMENTS} 段（当前 ${excerptSegments.length} 段）。`);
    return;
  }
  for (let i = 0; i < excerptSegments.length; i += 1) {
    const seg = excerptSegments[i];
    const s = Number(seg.start);
    const e = Number(seg.end);
    if (seg.start === "" || seg.end === ""
        || !Number.isInteger(s) || !Number.isInteger(e) || s > e) {
      showExcerptNote(`第 ${i + 1} 段的起点与终点必须是整数，且起点不大于终点。`);
      jumpToExcerptSeg(i);
      return;
    }
  }
  // 服务端要求按演奏先后排列；在此把选段按起点排序并同步重排编辑器，
  // 保证编辑器“第 N 段”与预览中的段编号一致
  excerptSegments.sort((a, b) => Number(a.start) - Number(b.start));
  renderExcerptEditor();
  const sorted = excerptSegments
    .map((seg) => ({ start: Number(seg.start), end: Number(seg.end) }));
  const vc = lastResult.visit_count;
  for (let i = 0; i < sorted.length; i += 1) {
    if (sorted[i].start < 1 || sorted[i].end > vc) {
      showExcerptNote(`第 ${i + 1} 段 [${sorted[i].start}, ${sorted[i].end}]`
        + ` 超出访问序号范围（1~${vc}）。`);
      jumpToExcerptSeg(i);
      return;
    }
    if (i > 0 && sorted[i].start <= sorted[i - 1].end) {
      showExcerptNote(`第 ${i} 段与第 ${i + 1} 段重叠或未按演奏先后排列。`);
      jumpToExcerptSeg(i);
      return;
    }
  }
  const total = sorted.reduce((acc, seg) => acc + seg.end - seg.start + 1, 0);
  if (total < MIN_EXCERPT_VISITS || total > MAX_EXCERPT_VISITS) {
    showExcerptNote(`各段合计须为 ${MIN_EXCERPT_VISITS}~${MAX_EXCERPT_VISITS}`
      + ` 次访问，当前为 ${total} 次。`);
    return;
  }

  const seq = ++excerptSeq;
  btnExcerpt.disabled = true;
  showExcerptNote("正在生成摘谱…");
  let resp;
  try {
    resp = await fetch("/api/excerpt", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        measures: collectPayload(),
        segments: sorted,
      }),
    });
  } catch (e) {
    if (seq !== excerptSeq) return;
    btnExcerpt.disabled = false;
    showExcerptNote("无法连接服务器，请确认后端正在运行。");
    return;
  }
  if (seq !== excerptSeq) return; // 迟到响应：谱面 / 选段已变更，直接丢弃
  btnExcerpt.disabled = false;

  const body = await resp.json();
  if (seq !== excerptSeq) return;
  if (!body.ok) {
    renderExcerptErrors(body);
    return;
  }
  lastExcerpt = body.result;
  excerptSig = JSON.stringify(collectPayload());
  renderExcerptResult(body.result);
});

function renderExcerptErrors(body) {
  excerptErrors.innerHTML = "";
  const blocks = [
    ["录入谱面校验未通过", body.score_errors || [], "score"],
    ["选段有误", body.segment_errors || [], "segment"],
    ["摘谱生成失败", body.excerpt_errors || [], "excerpt"],
  ].filter(([, errs]) => errs.length);

  blocks.forEach(([title, errs, kind]) => {
    const h = document.createElement("h3");
    h.textContent = `${title}（${errs.length} 项）`;
    excerptErrors.appendChild(h);
    const ul = document.createElement("ul");
    errs.forEach((e) => {
      const li = document.createElement("li");
      if (kind === "score" && e.index !== null && e.index !== undefined) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "link";
        btn.textContent = `第 ${e.index + 1} 行`
          + `${e.measure ? `（编号 ${e.measure}）` : ""}`;
        btn.addEventListener("click", () => jumpToRow(e.index));
        li.appendChild(btn);
        li.appendChild(document.createTextNode(`：${e.message}`));
      } else if (kind === "segment"
                 && e.index !== null && e.index !== undefined) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "link";
        btn.textContent = `第 ${e.index + 1} 段`;
        btn.addEventListener("click", () => jumpToExcerptSeg(e.index));
        li.appendChild(btn);
        li.appendChild(document.createTextNode(`：${e.message}`));
      } else {
        li.textContent = e.message;
      }
      ul.appendChild(li);
    });
    excerptErrors.appendChild(ul);
  });
  excerptErrors.classList.remove("hidden");

  const scoreErrs = body.score_errors || [];
  if (scoreErrs.length) markInvalidRows(scoreErrs); // 谱面错误行在录入表标红
}

function renderExcerptResult(result) {
  excerptNote.classList.add("hidden");
  excerptBody.classList.remove("hidden");

  excerptSummary.innerHTML = "";
  [
    `${result.segment_count} 段 · 共 ${result.visit_count} 次访问`
      + ` · ${result.total_beats} 拍`,
    `新编号 1~${result.visit_count} 连续整数 · 反复与房子记号已清除`,
    "回读校验通过：拍数与生效 BPM 逐行对应所选访问",
  ].forEach((text) => {
    const span = document.createElement("span");
    span.textContent = text;
    excerptSummary.appendChild(span);
  });

  excerptPreview.innerHTML = "";
  let prevSeg = null;
  result.mapping.forEach((mp) => {
    const row = result.rows[mp.row - 1];
    const tr = document.createElement("tr");
    if (mp.segment !== prevSeg) {
      tr.classList.add("excerpt-boundary"); // 段首行加分隔（段首速度自带）
      prevSeg = mp.segment;
    }
    const marked = row.tempo !== null && row.tempo !== undefined;
    tr.innerHTML =
      `<td class="ex-num">${mp.row}</td>`
      + `<td>第 ${mp.segment} 段</td>`
      + `<td>${row.beats}</td>`
      + `<td>♩=${mp.visit.tempo}`
      + (marked
        ? '<span class="excerpt-mark">写入</span>'
        : '<span class="excerpt-carry">沿用</span>')
      + "</td>"
      + `<td>#${mp.visit.global_index}</td>`
      + `<td>${escapeHTML(mp.visit.source_measure)}</td>`
      + `<td>${escapeHTML(pathLabel(mp.visit.path))}</td>`;
    excerptPreview.appendChild(tr);
  });
}

btnExcerptDownload.addEventListener("click", () => {
  if (!lastExcerpt) return;
  const blob = new Blob([lastExcerpt.musicxml], {
    type: "application/vnd.recordare.musicxml+xml",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `score-excerpt-${Date.now()}.musicxml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

/* =========================================================================
   MusicXML 结构导入
   -------------------------------------------------------------------------
   - 选择本地 .musicxml 文件 → POST /api/import-musicxml（文本随 JSON 发送）；
   - 服务端转换后走现有完整校验与展开：成功时在页面预览转换出的录入行，
     由用户“确认替换表格”；失败时按源小节列出错误，不改动当前表格；
   - 确认后整体替换 rows，并使旧演奏带、节拍器计划、对照、反求与纠错
     结果全部失效，随后自动展开新谱面。
   ======================================================================== */

const btnImport = document.getElementById("btn-import-musicxml");
const fileInput = document.getElementById("musicxml-file");
const importPanel = document.getElementById("import-panel");
const importStatus = document.getElementById("import-status");
const importPreview = document.getElementById("import-preview");
const btnImportConfirm = document.getElementById("btn-import-confirm");
const btnImportCancel = document.getElementById("btn-import-cancel");

let pendingImport = null; // 待确认的导入：{ rows, expansion, fileName }

function showImportStatus(text, kind) {
  importStatus.textContent = text;
  importStatus.className = `import-status${kind ? ` import-${kind}` : ""}`;
}

function hideImportPanel() {
  importPanel.classList.add("hidden");
  importPreview.classList.add("hidden");
  btnImportConfirm.disabled = true;
  pendingImport = null;
  fileInput.value = "";
}

btnImport.addEventListener("click", () => fileInput.click());
btnImportCancel.addEventListener("click", hideImportPanel);

fileInput.addEventListener("change", async () => {
  const file = fileInput.files && fileInput.files[0];
  if (!file) return;
  clearErrors();
  importPanel.classList.remove("hidden");
  importPreview.classList.add("hidden");
  btnImportConfirm.disabled = true;
  pendingImport = null;
  showImportStatus(`正在读取并转换 ${file.name} …`);

  let text;
  try {
    text = await file.text();
  } catch (e) {
    showImportStatus("读取本地文件失败，请重试。", "error");
    return;
  }

  let resp;
  try {
    resp = await fetch("/api/import-musicxml", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ musicxml: text }),
    });
  } catch (e) {
    showImportStatus("无法连接服务器，请确认后端正在运行。", "error");
    return;
  }

  const body = await resp.json();
  if (!body.ok) {
    // 不支持的结构 / 未配对记号 / 校验展开失败：按源小节报错，不动表格
    renderImportErrors(body.errors || []);
    return;
  }
  pendingImport = {
    rows: body.result.measures,
    expansion: body.result.expansion,
    fileName: file.name,
  };
  renderImportPreview(pendingImport);
});

function renderImportErrors(errors) {
  importPreview.classList.add("hidden");
  if (!errors.length) {
    showImportStatus("转换失败：服务端未返回错误详情。", "error");
    return;
  }
  importStatus.className = "import-status import-error";
  importStatus.innerHTML = "";
  const h = document.createElement("h3");
  h.textContent = `MusicXML 无法导入（${errors.length} 项），当前表格未改动：`;
  importStatus.appendChild(h);
  const ul = document.createElement("ul");
  errors.forEach((e) => {
    const li = document.createElement("li");
    const where = e.index !== null && e.index !== undefined
      ? `源第 ${e.index + 1} 小节${e.measure ? `（编号 ${e.measure}）` : ""}`
      : "整体";
    li.textContent = `${where}：${e.message}`;
    ul.appendChild(li);
  });
  importStatus.appendChild(ul);
}

function rowPreviewText(r) {
  const marks = [];
  if (r.left_repeat) marks.push(`|:×${r.times}`);
  if (r.right_repeat) {
    marks.push(Array.isArray(r.right_closes) && r.right_closes.length === 2
      ? ":|（同线双终点：内层+外层）" : ":|");
  }
  if (r.volta !== null && r.volta !== undefined) marks.push(`{${r.volta}`);
  return `${r.beats}拍 · ♩=${r.tempo === null || r.tempo === undefined ? "沿用" : r.tempo}`
    + (marks.length ? ` · ${marks.join(" ")}` : "");
}

function renderImportPreview(pending) {
  const { rows: imported, expansion, fileName } = pending;
  importStatus.className = "import-status import-ok";
  importStatus.innerHTML = "";
  const head = document.createElement("div");
  head.className = "import-head";
  head.textContent = `${fileName} 转换成功：${imported.length} 个小节，`
    + `展开 ${expansion.visit_count} 次访问、共 ${expansion.total_beats} 拍。`
    + "请核对下面的录入行，确认后替换当前表格；取消则保留现有内容。";
  importStatus.appendChild(head);

  importPreview.innerHTML = "";
  const table = document.createElement("table");
  table.className = "import-table";
  table.innerHTML = "<thead><tr><th>#</th><th>编号</th><th>转换结果</th></tr></thead>";
  const tb = document.createElement("tbody");
  imported.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${i + 1}</td>`
      + `<td>${escapeHTML(r.number)}</td>`
      + `<td>${escapeHTML(rowPreviewText(r))}</td>`;
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  importPreview.appendChild(table);
  importPreview.classList.remove("hidden");
  btnImportConfirm.disabled = false;
}

btnImportConfirm.addEventListener("click", () => {
  if (!pendingImport) return;
  rows = pendingImport.rows.map((r) => ({
    key: uid(),
    number: r.number,
    beats: r.beats,
    tempo: r.tempo === null || r.tempo === undefined ? "" : r.tempo,
    left_repeat: !!r.left_repeat,
    times: r.times ?? 2,
    right_repeat: !!r.right_repeat,
    // 同线双终点归属（仅导入产生；确认后保留，随表格一起提交 / 展开）
    right_closes: Array.isArray(r.right_closes) ? r.right_closes.slice() : null,
    volta: r.volta === null || r.volta === undefined ? "" : r.volta,
    unknown: false,
  }));
  clearErrors();
  renderTable();
  hideImportPanel();
  // 整体替换：旧演奏带、节拍器计划、对照、反求、纠错结果全部失效
  lastResult = null;
  planStale = false;
  staleBanner.classList.add("hidden");
  visitStrip.classList.remove("strip-stale");
  btnDownload.disabled = true;
  legend.classList.add("hidden");
  resultSummary.textContent = "尚未展开";
  transport.reset(null);
  markCompareStale();
  markSolveStale();
  markFixStale();
  markLocateStale(); // 整体替换：定位结果同样失效
  markCuesStale();   // 整体替换：提示单同样失效
  markExcerptStale(); // 整体替换：摘谱预览同样失效
  doExpand(); // 与“载入示例”一致：替换后立即展开新谱面
});

/* =========================================================================
   MusicXML 结构导出
   -------------------------------------------------------------------------
   - POST /api/export-musicxml：服务端先复用完整校验与展开（非法谱面按
     原始行报错、不产生文件），生成单 part score-partwise 后再用现有
     导入入口往返自检，行与展开结果必须与原谱一致；
   - 成功时把返回的文本作为 .musicxml 下载到本地；整个过程不改动表格、
     演奏带、节拍器计划与任何已有结果。
   ======================================================================== */

const btnExportMusicXml = document.getElementById("btn-export-musicxml");

btnExportMusicXml.addEventListener("click", async () => {
  clearErrors();
  if (rows.length < MIN_ROWS) {
    showGlobalError(`至少需要 ${MIN_ROWS} 个小节。`);
    return;
  }
  btnExportMusicXml.disabled = true;
  const oldLabel = btnExportMusicXml.textContent;
  btnExportMusicXml.textContent = "正在导出…";

  let resp;
  try {
    resp = await fetch("/api/export-musicxml", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
  } catch (e) {
    showGlobalError("无法连接服务器，请确认后端正在运行。");
    btnExportMusicXml.disabled = false;
    btnExportMusicXml.textContent = oldLabel;
    return;
  }

  const body = await resp.json();
  btnExportMusicXml.disabled = false;
  btnExportMusicXml.textContent = oldLabel;

  if (!body.ok) {
    // 非法谱面 / 结构不可编码：按原始行显示错误，不产生下载
    renderErrors(body.errors || []);
    return;
  }

  const xml = body.result.musicxml;
  const blob = new Blob([xml], { type: "application/vnd.recordare.musicxml+xml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `score-structure-${Date.now()}.musicxml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

/* --------------------------------------------------------------- 示例 ---- */

const DEMO = [
  { number: "前奏", beats: 4, tempo: 90 },
  { number: "A", beats: 4, left_repeat: true, times: 2 },
  { number: "B", beats: 4, left_repeat: true, times: 2 },
  { number: "C1", beats: 2, right_repeat: true, volta: 1 },
  { number: "C2", beats: 2, volta: 2 },
  { number: "D", beats: 4, right_repeat: true, tempo: 60 },
  { number: "尾奏", beats: 4 },
];

document.getElementById("btn-demo").addEventListener("click", () => {
  rows = DEMO.map((r) => ({
    key: uid(),
    number: r.number,
    beats: r.beats,
    tempo: r.tempo === undefined || r.tempo === null ? "" : r.tempo,
    left_repeat: !!r.left_repeat,
    times: r.times ?? 2,
    right_repeat: !!r.right_repeat,
    volta: r.volta === undefined || r.volta === null ? "" : r.volta,
    unknown: false,
  }));
  clearErrors();
  renderTable();
  // 载入示例属于结构替换，旧结果整体作废
  lastResult = null;
  planStale = false;
  staleBanner.classList.add("hidden");
  visitStrip.classList.remove("strip-stale");
  btnDownload.disabled = true;
  legend.classList.add("hidden");
  visitStrip.innerHTML =
    '<div class="placeholder">点击右上角“展开演奏顺序”，结果将显示在这里。</div>';
  resultSummary.textContent = "尚未展开";
  transport.reset(null);
  markCompareStale(); // 结构整体替换 → 对照结果过期
  markSolveStale();   // 反求结果同样过期
  markFixStale();     // 右终点纠错结果同样过期
  markLocateStale();  // 定位结果同样过期
  markCuesStale();    // 提示单同样过期
  markExcerptStale(); // 摘谱预览同样过期
  doExpand();
});

document.getElementById("btn-clear").addEventListener("click", () => {
  rows = [blankRow(1), blankRow(2)];
  rows[0].tempo = 90; // 第 1 小节速度必填，给一个常用默认值
  clearErrors();
  clearResult();
  markCompareStale();
  markSolveStale();
  markFixStale();
  markLocateStale();
  markCuesStale();
  markExcerptStale();
  visitStrip.innerHTML =
    '<div class="placeholder">点击右上角“展开演奏顺序”，结果将显示在这里。</div>';
  resultSummary.textContent = "尚未展开";
  renderTable();
});

/* --------------------------------------------------------------- 启动 ---- */

rows = [blankRow(1), blankRow(2)];
rows[0].tempo = 90; // 第 1 小节速度必填，给一个常用默认值
renderTable();
transport.syncButtons();
updateBaselineStatus();
