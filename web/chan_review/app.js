const refs = {};

const state = {
  defaults: null,
  params: null,
  session: null,
  snapshot: null,
  snapshotTimer: null,
};

const COLORS = {
  grid: "rgba(64, 51, 39, 0.12)",
  text: "#2a2119",
  muted: "#6c6154",
  green: "#287d52",
  red: "#b53d2d",
  gold: "#b78a28",
  blue: "#305c8a",
  ink: "#21324a",
  overviewLine: "#6c6154",
  overviewFill: "rgba(179, 83, 45, 0.12)",
  overviewStroke: "rgba(179, 83, 45, 0.52)",
  zhongshuFill: "rgba(183, 138, 40, 0.18)",
  zhongshuStroke: "rgba(183, 138, 40, 0.82)",
  mergedFill: "rgba(183, 138, 40, 0.12)",
};

const FRACTAL_OFFSET = 10;

document.addEventListener("DOMContentLoaded", () => {
  collectRefs();
  bindEvents();
  boot();
});

function collectRefs() {
  refs.form = document.getElementById("control-form");
  refs.exchange = document.getElementById("exchange");
  refs.symbol = document.getElementById("symbol");
  refs.timeframeMain = document.getElementById("timeframe-main");
  refs.timeframeSub = document.getElementById("timeframe-sub");
  refs.start = document.getElementById("start");
  refs.end = document.getElementById("end");
  refs.chanMode = document.getElementById("chan-mode");
  refs.windowMain = document.getElementById("window-main");
  refs.windowSub = document.getElementById("window-sub");
  refs.viewMain = document.getElementById("view-main");
  refs.viewSub = document.getElementById("view-sub");

  refs.slider = document.getElementById("asof-slider");
  refs.asofLabel = document.getElementById("asof-label");
  refs.sessionSummary = document.getElementById("session-summary");
  refs.sessionInfo = document.getElementById("session-info");
  refs.statusLine = document.getElementById("status-line");

  refs.mainDetail = document.getElementById("main-detail");
  refs.mainOverview = document.getElementById("main-overview");
  refs.mainCounts = document.getElementById("main-counts");
  refs.mainTfLabel = document.getElementById("main-tf-label");
  refs.subDetail = document.getElementById("sub-detail");
  refs.subOverview = document.getElementById("sub-overview");
  refs.subCounts = document.getElementById("sub-counts");
  refs.subTfLabel = document.getElementById("sub-tf-label");

  refs.decisionCard = document.getElementById("decision-card");
  refs.signalsList = document.getElementById("signals-list");
  refs.signalCount = document.getElementById("signal-count");
  refs.mergeMainTable = document.getElementById("merge-main-table");
  refs.mergeSubTable = document.getElementById("merge-sub-table");

  refs.stepPrev = document.getElementById("step-prev");
  refs.stepNext = document.getElementById("step-next");
  refs.layerBtns = document.querySelectorAll(".layer-btn");

  refs.settingsBtn = document.getElementById("settings-btn");
  refs.modalOverlay = document.getElementById("modal-overlay");
  refs.modalClose = document.getElementById("modal-close");
  refs.modalCancel = document.getElementById("modal-cancel");
}

function bindEvents() {
  refs.settingsBtn.addEventListener("click", showModal);
  refs.modalClose.addEventListener("click", hideModal);
  refs.modalCancel.addEventListener("click", hideModal);
  refs.modalOverlay.addEventListener("click", (event) => {
    if (event.target === refs.modalOverlay) hideModal();
  });

  refs.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideModal();
    await loadSession(true);
  });

  refs.slider.addEventListener("input", () => {
    updateAsofLabel();
    queueSnapshot(40);
  });

  refs.stepPrev.addEventListener("click", () => step(-1));
  refs.stepNext.addEventListener("click", () => step(1));

  document.addEventListener("keydown", (event) => {
    const tagName = event.target?.tagName;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(tagName)) return;
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });

  [refs.windowMain, refs.windowSub].forEach((input) => {
    input.addEventListener("change", () => queueSnapshot(120));
  });

  [refs.viewMain, refs.viewSub].forEach((input) => {
    input.addEventListener("change", () => {
      if (state.snapshot) renderCharts(state.snapshot);
    });
  });

  refs.layerBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.classList.toggle("active");
      if (state.snapshot) renderSnapshot(state.snapshot);
    });
  });

  window.addEventListener("resize", () => {
    if (state.snapshot) renderCharts(state.snapshot);
  });
}

function showModal() {
  refs.modalOverlay.classList.remove("hidden");
}

function hideModal() {
  refs.modalOverlay.classList.add("hidden");
}

function step(delta) {
  const current = Number(refs.slider.value);
  const min = Number(refs.slider.min);
  const max = Number(refs.slider.max);
  const next = Math.max(min, Math.min(max, current + delta));
  if (next === current) return;
  refs.slider.value = String(next);
  updateAsofLabel();
  queueSnapshot(0);
}

async function boot() {
  try {
    setStatus("加载默认配置...", "ok");
    const payload = await fetchJSON("/api/defaults");
    state.defaults = payload.defaults;
    applyDefaults(payload.defaults);
    await loadSession(true);
  } catch (error) {
    setStatus(error.message, "error");
    renderEmptyState(error.message);
  }
}

function applyDefaults(defaults) {
  refs.exchange.value = defaults.exchange;
  refs.symbol.value = defaults.symbol;
  refs.timeframeMain.value = defaults.timeframe_main;
  refs.timeframeSub.value = defaults.timeframe_sub;
  refs.start.value = defaults.start;
  refs.end.value = defaults.end;
  refs.chanMode.value = defaults.chan_mode;
  refs.windowMain.value = defaults.window_main;
  refs.windowSub.value = defaults.window_sub;
}

function collectFormParams() {
  return {
    exchange: refs.exchange.value.trim(),
    symbol: refs.symbol.value.trim(),
    timeframe_main: refs.timeframeMain.value.trim(),
    timeframe_sub: refs.timeframeSub.value.trim(),
    start: refs.start.value.trim(),
    end: refs.end.value.trim(),
    chan_mode: refs.chanMode.value,
    window_main: normalizeWindow(refs.windowMain.value, 120),
    window_sub: normalizeWindow(refs.windowSub.value, 180),
  };
}

function normalizeWindow(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return parsed;
}

function snapshotParams() {
  return {
    ...state.params,
    window_main: normalizeWindow(refs.windowMain.value, state.params?.window_main || 120),
    window_sub: normalizeWindow(refs.windowSub.value, state.params?.window_sub || 180),
  };
}

function queueSnapshot(delay = 40) {
  if (state.snapshotTimer) clearTimeout(state.snapshotTimer);
  state.snapshotTimer = window.setTimeout(() => {
    loadSnapshot().catch((error) => setStatus(error.message, "error"));
  }, delay);
}

async function loadSession(resetSlider) {
  const params = collectFormParams();
  setStatus("读取 K 线数据...", "ok");
  renderEmptyState("加载中...");

  const payload = await fetchJSON("/api/session", params);
  state.params = params;
  state.session = payload.session;
  state.snapshot = null;

  const times = state.session.main_times || [];
  refs.slider.min = "0";
  refs.slider.max = String(Math.max(times.length - 1, 0));
  if (resetSlider || Number(refs.slider.value) > times.length - 1) {
    refs.slider.value = String(Math.max(times.length - 1, 0));
  }

  updateAsofLabel();
  updateSessionInfo(params, state.session);
  await loadSnapshot();
}

function updateSessionInfo(params, session) {
  refs.sessionInfo.textContent =
    `${params.symbol} · ${params.timeframe_main}/${params.timeframe_sub} · ${params.start.slice(0, 10)} → ${params.end.slice(0, 10)}`;
  refs.sessionSummary.textContent = `${session.main_bar_count}主 / ${session.sub_bar_count}次`;
}

async function loadSnapshot() {
  if (!state.session || !state.params) return;
  const times = state.session.main_times || [];
  if (!times.length) throw new Error("当前参数下没有主级别 K 线。");

  const index = Math.max(0, Math.min(Number(refs.slider.value), times.length - 1));
  const asof = times[index];
  const payload = await fetchJSON("/api/snapshot", { ...snapshotParams(), asof });
  state.snapshot = payload.snapshot;
  renderSnapshot(payload.snapshot);
  setStatus(
    `已渲染 ${payload.snapshot.meta.asof}，主 ${payload.snapshot.meta.window_main} 根，次 ${payload.snapshot.meta.window_sub} 根。`,
    "ok",
  );
}

function updateAsofLabel() {
  if (!state.session || !(state.session.main_times || []).length) {
    refs.asofLabel.textContent = "-";
    return;
  }

  const index = Math.max(0, Math.min(Number(refs.slider.value), state.session.main_times.length - 1));
  refs.asofLabel.textContent = formatTimestamp(state.session.main_times[index]);
}

function renderSnapshot(snapshot) {
  renderDecision(snapshot);
  renderSignals(snapshot.signals_full || []);
  renderMergeTable(refs.mergeMainTable, snapshot.main?.merged_bars || []);
  renderMergeTable(refs.mergeSubTable, snapshot.sub?.merged_bars || []);
  renderCharts(snapshot);
}

function renderCharts(snapshot) {
  const meta = snapshot.meta;
  const viewMain = normalizeWindow(refs.viewMain.value, 40);
  const viewSub = normalizeWindow(refs.viewSub.value, 50);

  refs.mainTfLabel.textContent = `${meta.timeframe_main} 主级别`;
  refs.subTfLabel.textContent = `${meta.timeframe_sub} 次级别`;

  renderDetailChart(refs.mainDetail, snapshot.main, `${meta.timeframe_main} 主级别`, viewMain);
  renderDetailChart(refs.subDetail, snapshot.sub, `${meta.timeframe_sub} 次级别`, viewSub);

  const mainRange = calcViewRange(snapshot.main, viewMain);
  const subRange = calcViewRange(snapshot.sub, viewSub);
  renderOverviewChart(refs.mainOverview, snapshot.main, mainRange.start, mainRange.end);
  renderOverviewChart(refs.subOverview, snapshot.sub, subRange.start, subRange.end);

  refs.mainCounts.textContent = renderCountLine(snapshot.main);
  refs.subCounts.textContent = renderCountLine(snapshot.sub);
}

function calcViewRange(levelData, viewBars) {
  const total = levelData?.raw_bars?.length ?? 0;
  if (!total) return { start: 0, end: 0 };
  return { start: Math.max(0, total - viewBars), end: total - 1 };
}

function renderCountLine(level) {
  if (!level) return "-";
  return [
    `raw ${level.raw_bars.length}`,
    `merged ${level.merged_bars.length}`,
    `frac ${level.fractals.length}`,
    `bi ${level.bis.length}`,
    `seg ${level.segments.length}`,
    `zs ${level.zhongshus.length}`,
  ].join(" / ");
}

function renderDecision(snapshot) {
  const decision = snapshot.decision || {};
  const market = decision.market_state || {};
  const risk = decision.risk || {};
  const action = decision.action?.decision || "WAIT";
  refs.decisionCard.innerHTML = `
    <span class="decision-pill ${escapeHtml(action)}">${escapeHtml(action)}</span>
    <div class="stack-item">
      <h3>${escapeHtml(decision.cn_summary || "等待加载")}</h3>
      <p>${escapeHtml(decision.action?.reason || "暂无动作原因。")}</p>
    </div>
    <div class="decision-grid">
      <div class="cardlet">
        <strong>市场状态</strong>
        <div>${escapeHtml(market.trend_type || "-")} / ${escapeHtml(market.walk_type || "-")}</div>
        <div class="mini-note">${escapeHtml(market.phase || "-")}</div>
      </div>
      <div class="cardlet">
        <strong>中枢数量</strong>
        <div>${escapeHtml(String(market.zhongshu_count ?? "-"))}</div>
        <div class="mini-note">fresh: ${escapeHtml(snapshot.meta?.previous_main_bar_time || "-")}</div>
      </div>
      <div class="cardlet">
        <strong>当前方向</strong>
        <div>笔 ${escapeHtml(market.current_stroke_dir || "-")}</div>
        <div class="mini-note">线段 ${escapeHtml(market.current_segment_dir || "-")}</div>
      </div>
      <div class="cardlet">
        <strong>风险等级</strong>
        <div>${escapeHtml(risk.conflict_level || "-")}</div>
        <div class="mini-note">${escapeHtml(risk.notes || "-")}</div>
      </div>
    </div>
  `;
}

function renderSignals(signals) {
  refs.signalCount.textContent = String(signals.length);
  if (!signals.length) {
    refs.signalsList.innerHTML = `<div class="stack-item"><p>当前时点没有 fresh 信号。</p></div>`;
    return;
  }

  refs.signalsList.innerHTML = signals.map((item) => `
    <article class="stack-item">
      <div class="signal-topline">
        <h3>${escapeHtml(item.type)} / ${escapeHtml(item.level)}</h3>
        <span class="badge">${Number(item.confidence || 0).toFixed(2)}</span>
      </div>
      <p>${escapeHtml(item.trigger || "-")}</p>
      <p>失效: ${escapeHtml(item.invalid_if || "-")}</p>
      <p class="mini-note">event: ${escapeHtml(formatTimestamp(item.event_time))}</p>
      <p class="mini-note">avail: ${escapeHtml(formatTimestamp(item.available_time))}</p>
    </article>
  `).join("");
}

function renderMergeTable(container, mergedBars) {
  const rows = mergedBars.filter((item) => item.merge_size > 1).slice(-12).reverse();
  if (!rows.length) {
    container.innerHTML = `<div class="stack-item"><p>当前窗口没有包含合并。</p></div>`;
    return;
  }

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>合并索引</th>
          <th>原始范围</th>
          <th>根数</th>
          <th>方向</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((item) => `
          <tr>
            <td>#${item.index}</td>
            <td>${item.raw_start_index}→${item.raw_end_index}</td>
            <td>${item.merge_size}</td>
            <td>${escapeHtml(item.direction)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderDetailChart(container, levelData, title, viewBars) {
  const allRawBars = levelData?.raw_bars || [];
  if (!allRawBars.length) {
    container.innerHTML = `<div class="chart-empty">${escapeHtml(title)} 暂无数据</div>`;
    return;
  }

  const mergedBars = levelData.merged_bars || [];
  const layers = currentLayers();
  const width = Math.max(container.clientWidth - 2, 400);
  const height = Math.max(container.clientHeight || 300, 200);
  const margin = { top: 16, right: 22, bottom: 26, left: 66 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;

  const rawBars = (viewBars > 0 && viewBars < allRawBars.length)
    ? allRawBars.slice(-viewBars)
    : allRawBars;
  const rawStartAbs = rawBars[0].index;
  const rawEndAbs = rawBars[rawBars.length - 1].index;
  const step = rawBars.length > 1 ? plotWidth / (rawBars.length - 1) : plotWidth;
  const bodyWidth = Math.max(4, Math.min(step * 0.6, 16));

  const rawX = (absIndex) => rawBars.length === 1
    ? margin.left + plotWidth / 2
    : margin.left + (absIndex - rawStartAbs) * step;

  const mergedMap = new Map();
  mergedBars.forEach((item) => {
    if (item.raw_end_index < rawStartAbs || item.raw_start_index > rawEndAbs) return;
    const clampedStart = Math.max(item.raw_start_index, rawStartAbs);
    const clampedEnd = Math.min(item.raw_end_index, rawEndAbs);
    const startX = rawX(clampedStart);
    const endX = rawX(clampedEnd);
    mergedMap.set(item.index, {
      startX,
      endX,
      centerX: (startX + endX) / 2,
      width: Math.max(endX - startX, bodyWidth),
    });
  });

  const extraPrices = [
    ...(levelData.zhongshus || [])
      .filter((item) => mergedMap.has(item.start_index) || mergedMap.has(item.end_index))
      .flatMap((item) => [item.zd, item.zg]),
    ...(levelData.bis || [])
      .filter((item) => mergedMap.has(item.start_index) || mergedMap.has(item.end_index))
      .flatMap((item) => [item.start_price, item.end_price]),
  ];
  const lows = rawBars.map((item) => item.low).concat(extraPrices);
  const highs = rawBars.map((item) => item.high).concat(extraPrices);
  const priceMinBase = Math.min(...lows);
  const priceMaxBase = Math.max(...highs);
  const span = Math.max(priceMaxBase - priceMinBase, priceMaxBase * 0.01, 1);
  const priceMin = priceMinBase - span * 0.08;
  const priceMax = priceMaxBase + span * 0.08;
  const y = (price) => margin.top + ((priceMax - price) / (priceMax - priceMin)) * plotHeight;

  const priceTicks = buildTicks(priceMin, priceMax, 5);
  const xTicks = buildTimeTicks(rawBars);
  const svgContent = [
    renderGrid(priceTicks, xTicks, margin, width, height, y, rawX),
    layers.zhongshus ? renderZhongshus(levelData.zhongshus || [], mergedMap, y) : "",
    layers.segments ? renderSegments(levelData.segments || [], mergedMap, y) : "",
    layers.bis ? renderBis(levelData.bis || [], mergedMap, y) : "",
    layers.raw ? renderRawCandles(rawBars, rawX, y, bodyWidth) : "",
    layers.merged ? renderMergedCandles(mergedBars, mergedMap, y) : "",
    layers.fractals ? renderFractals(levelData.fractals || [], mergedMap, y) : "",
    renderAxes(priceTicks, xTicks, margin, width, height, y, rawX),
  ].join("");

  container.innerHTML = `
    <svg class="svg-chart" viewBox="0 0 ${width} ${height}" width="100%" height="100%" role="img" aria-label="${escapeHtml(title)}">
      <title>${escapeHtml(title)}</title>
      ${svgContent}
    </svg>
  `;
}

function renderOverviewChart(container, levelData, viewStartIndex, viewEndIndex) {
  const bars = levelData?.raw_bars || [];
  if (!bars.length) {
    container.innerHTML = `<div class="chart-empty">暂无缩略图</div>`;
    return;
  }

  const width = Math.max(container.clientWidth - 2, 400);
  const height = 50;
  const margin = { top: 4, right: 8, bottom: 4, left: 8 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const minPrice = Math.min(...bars.map((item) => item.low));
  const maxPrice = Math.max(...bars.map((item) => item.high));
  const span = Math.max(maxPrice - minPrice, 1);

  const xFn = (index) => {
    if (bars.length === 1) return margin.left + plotWidth / 2;
    return margin.left + (index / (bars.length - 1)) * plotWidth;
  };
  const yFn = (price) => margin.top + ((maxPrice - price) / span) * plotHeight;

  const pathD = bars
    .map((bar, index) => `${index === 0 ? "M" : "L"}${xFn(index)},${yFn(bar.close)}`)
    .join(" ");

  const safeStart = Math.max(0, Math.min(viewStartIndex, bars.length - 1));
  const safeEnd = Math.max(safeStart, Math.min(viewEndIndex, bars.length - 1));
  const startX = xFn(safeStart);
  const endX = xFn(safeEnd);
  const boxWidth = bars.length === 1 ? 8 : Math.max(endX - startX, 2);

  container.innerHTML = `
    <svg class="svg-chart" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="价格缩略图">
      <rect
        x="${startX}"
        y="${margin.top}"
        width="${boxWidth}"
        height="${plotHeight}"
        fill="${COLORS.overviewFill}"
        stroke="${COLORS.overviewStroke}"
        stroke-width="1"
        rx="2"
      />
      <path d="${pathD}" fill="none" stroke="${COLORS.overviewLine}" stroke-width="1.1" opacity="0.74" />
    </svg>
  `;
}

function renderGrid(priceTicks, xTicks, margin, width, height, y, rawX) {
  const horizontal = priceTicks.map((tick) => (
    `<line x1="${margin.left}" y1="${y(tick)}" x2="${width - margin.right}" y2="${y(tick)}" stroke="${COLORS.grid}" stroke-width="1" />`
  )).join("");

  const vertical = xTicks.map((tick) => {
    const x = rawX(tick.index);
    return `<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${height - margin.bottom}" stroke="${COLORS.grid}" stroke-width="1" stroke-dasharray="3 6" />`;
  }).join("");

  return horizontal + vertical;
}

function renderAxes(priceTicks, xTicks, margin, width, height, y, rawX) {
  const priceLabels = priceTicks.map((tick) => `
    <text x="${margin.left - 8}" y="${y(tick) + 4}" text-anchor="end" fill="${COLORS.muted}" font-size="11">
      ${tick.toFixed(2)}
    </text>
  `).join("");

  const timeLabels = xTicks.map((tick) => {
    const x = rawX(tick.index);
    return `
      <text x="${x}" y="${height - 7}" text-anchor="middle" fill="${COLORS.muted}" font-size="11">
        ${escapeHtml(shortTimestamp(tick.time))}
      </text>
    `;
  }).join("");

  return `
    <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="${COLORS.grid}" stroke-width="1.2" />
    ${priceLabels}
    ${timeLabels}
  `;
}

function renderRawCandles(rawBars, rawX, y, bodyWidth) {
  return rawBars.map((bar) => {
    const x = rawX(bar.index);
    const rise = bar.close >= bar.open;
    const color = rise ? COLORS.green : COLORS.red;
    const bodyTop = y(Math.max(bar.open, bar.close));
    const bodyBottom = y(Math.min(bar.open, bar.close));
    const bodyHeight = Math.max(bodyBottom - bodyTop, 1.6);
    const title = `raw #${bar.index}\n${formatTimestamp(bar.time)}\nO:${bar.open} H:${bar.high} L:${bar.low} C:${bar.close}`;
    return `
      <g>
        <title>${escapeHtml(title)}</title>
        <line x1="${x}" y1="${y(bar.high)}" x2="${x}" y2="${y(bar.low)}" stroke="${color}" stroke-width="1.2" />
        <rect x="${x - bodyWidth / 2}" y="${bodyTop}" width="${bodyWidth}" height="${bodyHeight}" fill="${color}" opacity="0.82" rx="1.5" />
      </g>
    `;
  }).join("");
}

function renderMergedCandles(mergedBars, mergedMap, y) {
  return mergedBars.map((bar) => {
    const pos = mergedMap.get(bar.index);
    if (!pos) return "";
    const rise = bar.close >= bar.open;
    const stroke = rise ? COLORS.gold : COLORS.ink;
    const bodyTop = y(Math.max(bar.open, bar.close));
    const bodyBottom = y(Math.min(bar.open, bar.close));
    const bodyHeight = Math.max(bodyBottom - bodyTop, 1.8);
    const width = Math.max(pos.width + 6, 10);
    const title = `merged #${bar.index}\n${formatTimestamp(bar.time)}\nraw: ${bar.raw_start_index} -> ${bar.raw_end_index}\nsize: ${bar.merge_size} (${bar.direction})`;
    return `
      <g>
        <title>${escapeHtml(title)}</title>
        <line x1="${pos.centerX}" y1="${y(bar.high)}" x2="${pos.centerX}" y2="${y(bar.low)}" stroke="${stroke}" stroke-width="2.1" opacity="0.88" />
        <rect x="${pos.centerX - width / 2}" y="${bodyTop}" width="${width}" height="${bodyHeight}" fill="${COLORS.mergedFill}" stroke="${stroke}" stroke-width="1.8" rx="2" />
      </g>
    `;
  }).join("");
}

function renderFractals(fractals, mergedMap, y) {
  return fractals.map((item) => {
    const pos = mergedMap.get(item.index);
    if (!pos) return "";
    const x = pos.centerX;
    const size = 7;
    const isTop = item.kind === "top";
    const fill = isTop ? COLORS.red : COLORS.green;
    const anchorY = y(item.price);
    const tipY = isTop ? anchorY - FRACTAL_OFFSET : anchorY + FRACTAL_OFFSET;
    const points = isTop
      ? `${x},${tipY} ${x - size},${tipY + size + 3} ${x + size},${tipY + size + 3}`
      : `${x},${tipY} ${x - size},${tipY - size - 3} ${x + size},${tipY - size - 3}`;
    const title = `${item.kind} fractal #${item.index}\nevent: ${formatTimestamp(item.event_time)}\navail: ${formatTimestamp(item.available_time)}`;
    return `
      <polygon points="${points}" fill="${fill}" opacity="0.88">
        <title>${escapeHtml(title)}</title>
      </polygon>
    `;
  }).join("");
}

function renderBis(bis, mergedMap, y) {
  return bis.map((item) => {
    const start = mergedMap.get(item.start_index);
    const end = mergedMap.get(item.end_index);
    if (!start || !end) return "";
    const stroke = item.direction === "up" ? COLORS.blue : COLORS.ink;
    const title = `${item.direction} bi ${item.start_index} -> ${item.end_index}\nevent: ${formatTimestamp(item.event_time)}\navailable: ${formatTimestamp(item.available_time)}`;
    return `
      <line x1="${start.centerX}" y1="${y(item.start_price)}" x2="${end.centerX}" y2="${y(item.end_price)}" stroke="${stroke}" stroke-width="2.2" opacity="0.92">
        <title>${escapeHtml(title)}</title>
      </line>
    `;
  }).join("");
}

function renderSegments(segments, mergedMap, y) {
  return segments.map((item) => {
    const start = mergedMap.get(item.start_index);
    const end = mergedMap.get(item.end_index);
    if (!start || !end) return "";
    const left = Math.min(start.centerX, end.centerX);
    const width = Math.max(Math.abs(end.centerX - start.centerX), 2);
    const top = y(item.high);
    const height = Math.max(y(item.low) - top, 2);
    const stroke = item.direction === "up" ? COLORS.blue : COLORS.red;
    const title = `${item.direction} segment ${item.start_index} -> ${item.end_index}\nevent: ${formatTimestamp(item.event_time)}\navailable: ${formatTimestamp(item.available_time)}\nrange: ${item.low} - ${item.high}`;
    return `
      <rect x="${left}" y="${top}" width="${width}" height="${height}" fill="none" stroke="${stroke}" stroke-width="1.4" stroke-dasharray="6 5" opacity="0.7">
        <title>${escapeHtml(title)}</title>
      </rect>
    `;
  }).join("");
}

function renderZhongshus(zhongshus, mergedMap, y) {
  return zhongshus.map((item) => {
    const start = mergedMap.get(item.start_index);
    const end = mergedMap.get(item.end_index);
    if (!start || !end) return "";
    const left = Math.min(start.centerX, end.centerX);
    const width = Math.max(Math.abs(end.centerX - start.centerX), 2);
    const top = y(item.zg);
    const height = Math.max(y(item.zd) - top, 2);
    const title = `zhongshu ${item.start_index} -> ${item.end_index}\nZD:${item.zd} ZG:${item.zg}\nevent: ${formatTimestamp(item.event_time)}\navailable: ${formatTimestamp(item.available_time)}\nevolution: ${item.evolution}`;
    return `
      <rect x="${left}" y="${top}" width="${width}" height="${height}" fill="${COLORS.zhongshuFill}" stroke="${COLORS.zhongshuStroke}" stroke-width="1.6" rx="4">
        <title>${escapeHtml(title)}</title>
      </rect>
    `;
  }).join("");
}

function buildTicks(min, max, count) {
  if (count <= 1) return [min, max];
  const step = (max - min) / (count - 1);
  return Array.from({ length: count }, (_, index) => min + step * index);
}

function buildTimeTicks(rawBars) {
  if (!rawBars.length) return [];
  if (rawBars.length === 1) {
    return [{ index: rawBars[0].index, time: rawBars[0].time }];
  }

  const marks = [
    rawBars[0],
    rawBars[Math.floor((rawBars.length - 1) / 2)],
    rawBars[rawBars.length - 1],
  ];
  const dedup = new Map();
  marks.forEach((item) => dedup.set(item.index, { index: item.index, time: item.time }));
  return Array.from(dedup.values());
}

function currentLayers() {
  const result = {};
  refs.layerBtns.forEach((btn) => {
    result[btn.dataset.layer] = btn.classList.contains("active");
  });
  return result;
}

function renderEmptyState(message) {
  const detailHtml = `<div class="chart-empty">${escapeHtml(message)}</div>`;
  const overviewHtml = `<div class="chart-empty">等待缩略图</div>`;
  refs.mainDetail.innerHTML = detailHtml;
  refs.subDetail.innerHTML = detailHtml;
  refs.mainOverview.innerHTML = overviewHtml;
  refs.subOverview.innerHTML = overviewHtml;
  refs.mainCounts.textContent = "-";
  refs.subCounts.textContent = "-";
  refs.decisionCard.innerHTML = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
  refs.signalsList.innerHTML = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
  refs.mergeMainTable.innerHTML = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
  refs.mergeSubTable.innerHTML = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
  refs.signalCount.textContent = "-";
}

async function fetchJSON(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    url.searchParams.set(key, String(value));
  });

  const response = await fetch(url);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) {
    const detail = payload.error?.hint ? ` ${payload.error.hint}` : "";
    throw new Error((payload.error?.message || "请求失败。") + detail);
  }
  return payload;
}

function setStatus(message, tone = "") {
  refs.statusLine.textContent = message;
  refs.statusLine.classList.remove("status-error", "status-ok");
  if (tone === "error") refs.statusLine.classList.add("status-error");
  if (tone === "ok") refs.statusLine.classList.add("status-ok");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTimestamp(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").replace(".000", "");
}

function shortTimestamp(value) {
  return formatTimestamp(value).slice(5, 16);
}
