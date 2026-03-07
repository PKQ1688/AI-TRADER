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
  zhongshuFill: "rgba(183, 138, 40, 0.18)",
  zhongshuStroke: "rgba(183, 138, 40, 0.82)",
  mergedFill: "rgba(183, 138, 40, 0.12)",
};

document.addEventListener("DOMContentLoaded", () => {
  collectRefs();
  bindEvents();
  boot();
});

function collectRefs() {
  refs.form           = document.getElementById("control-form");
  refs.exchange       = document.getElementById("exchange");
  refs.symbol         = document.getElementById("symbol");
  refs.timeframeMain  = document.getElementById("timeframe-main");
  refs.timeframeSub   = document.getElementById("timeframe-sub");
  refs.start          = document.getElementById("start");
  refs.end            = document.getElementById("end");
  refs.chanMode       = document.getElementById("chan-mode");
  refs.windowMain     = document.getElementById("window-main");
  refs.windowSub      = document.getElementById("window-sub");

  refs.slider         = document.getElementById("asof-slider");
  refs.asofLabel      = document.getElementById("asof-label");
  refs.sessionSummary = document.getElementById("session-summary");
  refs.sessionInfo    = document.getElementById("session-info");
  refs.statusLine     = document.getElementById("status-line");

  refs.mainChart      = document.getElementById("main-chart");
  refs.subChart       = document.getElementById("sub-chart");
  refs.mainCounts     = document.getElementById("main-counts");
  refs.subCounts      = document.getElementById("sub-counts");
  refs.mainTfLabel    = document.getElementById("main-tf-label");
  refs.subTfLabel     = document.getElementById("sub-tf-label");

  refs.decisionCard   = document.getElementById("decision-card");
  refs.signalsList    = document.getElementById("signals-list");
  refs.signalCount    = document.getElementById("signal-count");
  refs.mergeMainTable = document.getElementById("merge-main-table");
  refs.mergeSubTable  = document.getElementById("merge-sub-table");

  refs.viewMain       = document.getElementById("view-main");
  refs.viewSub        = document.getElementById("view-sub");

  refs.stepPrev       = document.getElementById("step-prev");
  refs.stepNext       = document.getElementById("step-next");
  refs.layerBtns      = document.querySelectorAll(".layer-btn");

  refs.settingsBtn    = document.getElementById("settings-btn");
  refs.modalOverlay   = document.getElementById("modal-overlay");
  refs.modalClose     = document.getElementById("modal-close");
  refs.modalCancel    = document.getElementById("modal-cancel");
}

function bindEvents() {
  // Form submit (inside modal)
  refs.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideModal();
    await loadSession(true);
  });

  // Slider drag
  refs.slider.addEventListener("input", () => {
    updateAsofLabel();
    queueSnapshot();
  });

  // Step buttons
  refs.stepPrev.addEventListener("click", () => {
    const v = Number(refs.slider.value);
    if (v > Number(refs.slider.min)) {
      refs.slider.value = String(v - 1);
      updateAsofLabel();
      queueSnapshot(0);
    }
  });

  refs.stepNext.addEventListener("click", () => {
    const v = Number(refs.slider.value);
    if (v < Number(refs.slider.max)) {
      refs.slider.value = String(v + 1);
      updateAsofLabel();
      queueSnapshot(0);
    }
  });

  // Keyboard ← → for step (when not typing in an input)
  document.addEventListener("keydown", (event) => {
    const tag = event.target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (event.key === "ArrowLeft")  refs.stepPrev.click();
    if (event.key === "ArrowRight") refs.stepNext.click();
  });

  // Window size inputs (re-fetch snapshot without reloading bars)
  [refs.windowMain, refs.windowSub].forEach((input) => {
    input.addEventListener("change", () => queueSnapshot(120));
  });

  // View size inputs (client-side only — no network request needed)
  [refs.viewMain, refs.viewSub].forEach((input) => {
    input.addEventListener("change", () => {
      if (state.snapshot) renderCharts(state.snapshot);
    });
  });

  // Layer toggle buttons
  refs.layerBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.classList.toggle("active");
      if (state.snapshot) renderSnapshot(state.snapshot);
    });
  });

  // Window resize → redraw charts
  window.addEventListener("resize", () => {
    if (state.snapshot) renderCharts(state.snapshot);
  });

  // Modal open / close
  refs.settingsBtn.addEventListener("click", showModal);
  refs.modalClose.addEventListener("click", hideModal);
  refs.modalCancel.addEventListener("click", hideModal);
  refs.modalOverlay.addEventListener("click", (event) => {
    if (event.target === refs.modalOverlay) hideModal();
  });
}

function showModal() {
  refs.modalOverlay.classList.remove("hidden");
}

function hideModal() {
  refs.modalOverlay.classList.add("hidden");
}

// ── Boot ─────────────────────────────────────────────────────────────────────

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
  refs.exchange.value      = defaults.exchange;
  refs.symbol.value        = defaults.symbol;
  refs.timeframeMain.value = defaults.timeframe_main;
  refs.timeframeSub.value  = defaults.timeframe_sub;
  refs.start.value         = defaults.start;
  refs.end.value           = defaults.end;
  refs.chanMode.value      = defaults.chan_mode;
  refs.windowMain.value    = defaults.window_main;
  refs.windowSub.value     = defaults.window_sub;
}

function collectFormParams() {
  return {
    exchange:       refs.exchange.value.trim(),
    symbol:         refs.symbol.value.trim(),
    timeframe_main: refs.timeframeMain.value.trim(),
    timeframe_sub:  refs.timeframeSub.value.trim(),
    start:          refs.start.value.trim(),
    end:            refs.end.value.trim(),
    chan_mode:       refs.chanMode.value,
    window_main:    normalizeWindow(refs.windowMain.value, 120),
    window_sub:     normalizeWindow(refs.windowSub.value, 180),
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
    window_sub:  normalizeWindow(refs.windowSub.value,  state.params?.window_sub  || 180),
  };
}

function queueSnapshot(delay = 40) {
  if (state.snapshotTimer) clearTimeout(state.snapshotTimer);
  state.snapshotTimer = window.setTimeout(() => {
    loadSnapshot().catch((error) => setStatus(error.message, "error"));
  }, delay);
}

// ── Session & Snapshot loading ────────────────────────────────────────────────

async function loadSession(resetSlider) {
  const params = collectFormParams();
  setStatus("读取 K 线数据...", "ok");
  renderEmptyState("加载中...");

  const payload = await fetchJSON("/api/session", params);
  state.params   = params;
  state.session  = payload.session;
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
  refs.sessionSummary.textContent =
    `${session.main_bar_count}主 / ${session.sub_bar_count}次`;
}

async function loadSnapshot() {
  if (!state.session || !state.params) return;
  const times = state.session.main_times || [];
  if (!times.length) throw new Error("当前参数下没有主级别 K 线。");

  const index = Math.max(0, Math.min(Number(refs.slider.value), times.length - 1));
  const asof  = times[index];
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
  const index = Math.max(
    0,
    Math.min(Number(refs.slider.value), state.session.main_times.length - 1),
  );
  refs.asofLabel.textContent = formatTimestamp(state.session.main_times[index]);
}

// ── Render orchestration ──────────────────────────────────────────────────────

function renderSnapshot(snapshot) {
  renderDecision(snapshot);
  renderSignals(snapshot.signals_full || []);
  renderMergeTable(refs.mergeMainTable, snapshot.main?.merged_bars || []);
  renderMergeTable(refs.mergeSubTable,  snapshot.sub?.merged_bars  || []);
  renderCharts(snapshot);
}

function renderCharts(snapshot) {
  const meta = snapshot.meta;
  refs.mainTfLabel.textContent = `${meta.timeframe_main} 主级别`;
  refs.subTfLabel.textContent  = `${meta.timeframe_sub} 次级别`;
  const viewMain = normalizeWindow(refs.viewMain?.value, 60);
  const viewSub  = normalizeWindow(refs.viewSub?.value, 80);
  renderChart(refs.mainChart, snapshot.main, `${meta.timeframe_main} 主级别`, viewMain);
  renderChart(refs.subChart,  snapshot.sub,  `${meta.timeframe_sub} 次级别`, viewSub);
  refs.mainCounts.textContent = renderCountLine(snapshot.main);
  refs.subCounts.textContent  = renderCountLine(snapshot.sub);
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

// ── Decision ──────────────────────────────────────────────────────────────────

function renderDecision(snapshot) {
  const decision = snapshot.decision;
  const market   = decision.market_state;
  const action   = decision.action.decision;
  refs.decisionCard.innerHTML = `
    <span class="decision-pill ${escapeHtml(action)}">${escapeHtml(action)}</span>
    <div class="stack-item" style="margin:5px 0 0">
      <h3>${escapeHtml(decision.cn_summary)}</h3>
      <p>${escapeHtml(decision.action.reason)}</p>
    </div>
    <div class="decision-grid">
      <div class="cardlet">
        <strong>市场状态</strong>
        <div>${escapeHtml(market.trend_type)} / ${escapeHtml(market.walk_type)}</div>
        <div class="mini-note">${escapeHtml(market.phase)}</div>
      </div>
      <div class="cardlet">
        <strong>中枢数量</strong>
        <div>${escapeHtml(String(market.zhongshu_count))}</div>
        <div class="mini-note">fresh: ${escapeHtml(snapshot.meta.previous_main_bar_time || "-")}</div>
      </div>
      <div class="cardlet">
        <strong>当前方向</strong>
        <div>笔 ${escapeHtml(market.current_stroke_dir)}</div>
        <div class="mini-note">线段 ${escapeHtml(market.current_segment_dir)}</div>
      </div>
      <div class="cardlet">
        <strong>冲突等级</strong>
        <div>${escapeHtml(decision.risk.conflict_level)}</div>
        <div class="mini-note">${escapeHtml(decision.risk.notes)}</div>
      </div>
    </div>
  `;
}

// ── Signals ───────────────────────────────────────────────────────────────────

function renderSignals(signals) {
  refs.signalCount.textContent = String(signals.length);
  if (!signals.length) {
    refs.signalsList.innerHTML = `<div class="stack-item"><p>当前时点没有 fresh 信号。</p></div>`;
    return;
  }
  refs.signalsList.innerHTML = signals
    .map(
      (item) => `
        <article class="stack-item">
          <div class="signal-topline">
            <h3>${escapeHtml(item.type)} / ${escapeHtml(item.level)}</h3>
            <span class="badge">${Number(item.confidence).toFixed(2)}</span>
          </div>
          <p>${escapeHtml(item.trigger)}</p>
          <p>失效: ${escapeHtml(item.invalid_if)}</p>
          <p class="mini-note">event: ${escapeHtml(formatTimestamp(item.event_time))}</p>
          <p class="mini-note">avail: ${escapeHtml(formatTimestamp(item.available_time))}</p>
        </article>
      `,
    )
    .join("");
}

// ── Merge table ───────────────────────────────────────────────────────────────

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
        ${rows
          .map(
            (item) => `
              <tr>
                <td>#${item.index}</td>
                <td>${item.raw_start_index}→${item.raw_end_index}</td>
                <td>${item.merge_size}</td>
                <td>${escapeHtml(item.direction)}</td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

// ── Chart rendering ───────────────────────────────────────────────────────────

// viewBars: how many raw bars to render (client-side slice of the last N bars).
// This is independent of window_main/sub which controls how many bars the
// algorithm sees. Reducing viewBars makes each candle wider and more readable.
function renderChart(container, levelData, title, viewBars) {
  const allRawBars = levelData?.raw_bars || [];
  if (!allRawBars.length) {
    container.innerHTML = `<div class="chart-empty">${escapeHtml(title)} 暂无数据</div>`;
    return;
  }

  const mergedBars = levelData.merged_bars || [];
  const layers     = currentLayers();

  const width  = Math.max(container.clientWidth  - 4, 400);
  const height = Math.max(container.clientHeight || 280, 200);

  const margin     = { top: 16, right: 22, bottom: 26, left: 62 };
  const plotWidth  = width  - margin.left - margin.right;
  const plotHeight = height - margin.top  - margin.bottom;

  // Slice to the last `viewBars` raw bars for display.
  const rawBars = (viewBars > 0 && viewBars < allRawBars.length)
    ? allRawBars.slice(-viewBars)
    : allRawBars;

  const rawStartAbs = rawBars[0].index;
  const rawEndAbs   = rawBars[rawBars.length - 1].index;
  const step        = rawBars.length > 1 ? plotWidth / (rawBars.length - 1) : plotWidth;
  const bodyWidth   = Math.max(4, Math.min(step * 0.58, 14));

  const rawX = (absIndex) => {
    if (rawBars.length === 1) return margin.left + plotWidth / 2;
    return margin.left + (absIndex - rawStartAbs) * step;
  };

  // Build mergedMap only for merged bars that overlap the visible raw range.
  // Bars outside the range are silently skipped; the render helpers already
  // return "" for any index not present in the map.
  const mergedMap = new Map();
  mergedBars.forEach((item) => {
    if (item.raw_end_index < rawStartAbs || item.raw_start_index > rawEndAbs) return;
    const clampedStart = Math.max(item.raw_start_index, rawStartAbs);
    const clampedEnd   = Math.min(item.raw_end_index,   rawEndAbs);
    const startX = rawX(clampedStart);
    const endX   = rawX(clampedEnd);
    mergedMap.set(item.index, {
      startX,
      endX,
      centerX: (startX + endX) / 2,
      width: Math.max(endX - startX, bodyWidth),
    });
  });

  // Price range: only include prices from structures visible in this range
  // so the Y-axis isn't compressed by off-screen data.
  const extraPrices = [
    ...levelData.zhongshus
      .filter((z) => mergedMap.has(z.start_index) || mergedMap.has(z.end_index))
      .flatMap((z) => [z.zd, z.zg]),
    ...levelData.bis
      .filter((b) => mergedMap.has(b.start_index) || mergedMap.has(b.end_index))
      .flatMap((b) => [b.start_price, b.end_price]),
  ];
  const lows  = rawBars.map((b) => b.low).concat(extraPrices);
  const highs = rawBars.map((b) => b.high).concat(extraPrices);
  const priceMinBase = Math.min(...lows);
  const priceMaxBase = Math.max(...highs);
  const priceSpan    = Math.max(priceMaxBase - priceMinBase, priceMaxBase * 0.01, 1);
  const priceMin = priceMinBase - priceSpan * 0.08;
  const priceMax = priceMaxBase + priceSpan * 0.08;

  const y = (price) =>
    margin.top + ((priceMax - price) / (priceMax - priceMin)) * plotHeight;

  const priceTicks = buildTicks(priceMin, priceMax, 5);
  const xTicks     = buildTimeTicks(rawBars);

  const layersHtml = [
    renderGrid(priceTicks, xTicks, margin, width, height, y, rawX),
    layers.zhongshus ? renderZhongshus(levelData.zhongshus, mergedMap, y) : "",
    layers.segments  ? renderSegments(levelData.segments, mergedMap, y)   : "",
    layers.bis       ? renderBis(levelData.bis, mergedMap, y)             : "",
    layers.raw       ? renderRawCandles(rawBars, rawX, y, bodyWidth)      : "",
    layers.merged    ? renderMergedCandles(mergedBars, mergedMap, y)      : "",
    layers.fractals  ? renderFractals(levelData.fractals, mergedMap, y)   : "",
    renderAxes(priceTicks, xTicks, margin, width, height, y, rawX),
  ].join("");

  container.innerHTML = `
    <svg class="svg-chart" viewBox="0 0 ${width} ${height}" width="100%" height="100%"
         role="img" aria-label="${escapeHtml(title)}">
      <title>${escapeHtml(title)}</title>
      ${layersHtml}
    </svg>
  `;
}

function renderGrid(priceTicks, xTicks, margin, width, height, y, rawX) {
  const hLines = priceTicks
    .map(
      (tick) =>
        `<line x1="${margin.left}" y1="${y(tick)}" x2="${width - margin.right}" y2="${y(tick)}" stroke="${COLORS.grid}" stroke-width="1" />`,
    )
    .join("");

  const vLines = xTicks
    .map((tick) => {
      const x = rawX(tick.index);
      return `<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${height - margin.bottom}" stroke="${COLORS.grid}" stroke-width="1" stroke-dasharray="3 6" />`;
    })
    .join("");

  return hLines + vLines;
}

function renderAxes(priceTicks, xTicks, margin, width, height, y, rawX) {
  const labels = priceTicks
    .map(
      (tick) => `
        <text x="${margin.left - 8}" y="${y(tick) + 4}" text-anchor="end" fill="${COLORS.muted}" font-size="11">
          ${tick.toFixed(2)}
        </text>
      `,
    )
    .join("");

  const timeLabels = xTicks
    .map((tick) => {
      const x = rawX(tick.index);
      return `
        <text x="${x}" y="${height - 7}" text-anchor="middle" fill="${COLORS.muted}" font-size="11">
          ${escapeHtml(shortTimestamp(tick.time))}
        </text>
      `;
    })
    .join("");

  return `
    <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="${COLORS.grid}" stroke-width="1.2" />
    ${labels}
    ${timeLabels}
  `;
}

function renderRawCandles(rawBars, rawX, y, bodyWidth) {
  return rawBars
    .map((bar) => {
      const x = rawX(bar.index);
      const rise = bar.close >= bar.open;
      const color = rise ? COLORS.green : COLORS.red;
      const bodyTop    = y(Math.max(bar.open, bar.close));
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
    })
    .join("");
}

function renderMergedCandles(mergedBars, mergedMap, y) {
  return mergedBars
    .map((bar) => {
      const pos = mergedMap.get(bar.index);
      if (!pos) return "";
      const rise   = bar.close >= bar.open;
      const stroke = rise ? COLORS.gold : COLORS.ink;
      const bodyTop    = y(Math.max(bar.open, bar.close));
      const bodyBottom = y(Math.min(bar.open, bar.close));
      const bodyHeight = Math.max(bodyBottom - bodyTop, 1.8);
      const width  = Math.max(pos.width + 6, 10);
      const title  = `merged #${bar.index}\n${formatTimestamp(bar.time)}\nraw: ${bar.raw_start_index} -> ${bar.raw_end_index}\nsize: ${bar.merge_size} (${bar.direction})`;
      return `
        <g>
          <title>${escapeHtml(title)}</title>
          <line x1="${pos.centerX}" y1="${y(bar.high)}" x2="${pos.centerX}" y2="${y(bar.low)}" stroke="${stroke}" stroke-width="2.1" opacity="0.88" />
          <rect x="${pos.centerX - width / 2}" y="${bodyTop}" width="${width}" height="${bodyHeight}" fill="${COLORS.mergedFill}" stroke="${stroke}" stroke-width="1.8" rx="2" />
        </g>
      `;
    })
    .join("");
}

function renderFractals(fractals, mergedMap, y) {
  return fractals
    .map((item) => {
      const pos = mergedMap.get(item.index);
      if (!pos) return "";
      const x     = pos.centerX;
      const markerY = y(item.price);
      const size  = 8;
      const isTop = item.kind === "top";
      const fill  = isTop ? COLORS.red : COLORS.green;
      const points = isTop
        ? `${x},${markerY - 11} ${x - size},${markerY - 1} ${x + size},${markerY - 1}`
        : `${x},${markerY + 11} ${x - size},${markerY + 1} ${x + size},${markerY + 1}`;
      const title = `${item.kind} fractal #${item.index}\nevent: ${formatTimestamp(item.event_time)}\navailable: ${formatTimestamp(item.available_time)}`;
      return `
        <polygon points="${points}" fill="${fill}" opacity="0.9">
          <title>${escapeHtml(title)}</title>
        </polygon>
      `;
    })
    .join("");
}

function renderBis(bis, mergedMap, y) {
  return bis
    .map((item) => {
      const start = mergedMap.get(item.start_index);
      const end   = mergedMap.get(item.end_index);
      if (!start || !end) return "";
      const stroke = item.direction === "up" ? COLORS.blue : COLORS.ink;
      const title  = `${item.direction} bi ${item.start_index} -> ${item.end_index}\nevent: ${formatTimestamp(item.event_time)}\navailable: ${formatTimestamp(item.available_time)}`;
      return `
        <line x1="${start.centerX}" y1="${y(item.start_price)}" x2="${end.centerX}" y2="${y(item.end_price)}" stroke="${stroke}" stroke-width="2.2" opacity="0.92">
          <title>${escapeHtml(title)}</title>
        </line>
      `;
    })
    .join("");
}

function renderSegments(segments, mergedMap, y) {
  return segments
    .map((item) => {
      const start = mergedMap.get(item.start_index);
      const end   = mergedMap.get(item.end_index);
      if (!start || !end) return "";
      const left   = Math.min(start.centerX, end.centerX);
      const width  = Math.max(Math.abs(end.centerX - start.centerX), 2);
      const top    = y(item.high);
      const height = Math.max(y(item.low) - top, 2);
      const stroke = item.direction === "up" ? COLORS.blue : COLORS.red;
      const title  = `${item.direction} segment ${item.start_index} -> ${item.end_index}\nevent: ${formatTimestamp(item.event_time)}\navailable: ${formatTimestamp(item.available_time)}\nrange: ${item.low} - ${item.high}`;
      return `
        <rect x="${left}" y="${top}" width="${width}" height="${height}" fill="none" stroke="${stroke}" stroke-width="1.4" stroke-dasharray="6 5" opacity="0.7">
          <title>${escapeHtml(title)}</title>
        </rect>
      `;
    })
    .join("");
}

function renderZhongshus(zhongshus, mergedMap, y) {
  return zhongshus
    .map((item) => {
      const start = mergedMap.get(item.start_index);
      const end   = mergedMap.get(item.end_index);
      if (!start || !end) return "";
      const left   = Math.min(start.centerX, end.centerX);
      const width  = Math.max(Math.abs(end.centerX - start.centerX), 2);
      const top    = y(item.zg);
      const height = Math.max(y(item.zd) - top, 2);
      const title  = `zhongshu ${item.start_index} -> ${item.end_index}\nZD:${item.zd} ZG:${item.zg}\nevent: ${formatTimestamp(item.event_time)}\navailable: ${formatTimestamp(item.available_time)}\nevolution: ${item.evolution}`;
      return `
        <rect x="${left}" y="${top}" width="${width}" height="${height}" fill="${COLORS.zhongshuFill}" stroke="${COLORS.zhongshuStroke}" stroke-width="1.6" rx="4">
          <title>${escapeHtml(title)}</title>
        </rect>
      `;
    })
    .join("");
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function buildTicks(min, max, count) {
  if (count <= 1) return [min, max];
  const step = (max - min) / (count - 1);
  return Array.from({ length: count }, (_, idx) => min + step * idx);
}

function buildTimeTicks(rawBars) {
  if (!rawBars.length) return [];
  if (rawBars.length === 1) return [{ index: rawBars[0].index, time: rawBars[0].time }];
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
  const html = `<div class="chart-empty">${escapeHtml(message)}</div>`;
  refs.mainChart.innerHTML      = html;
  refs.subChart.innerHTML       = html;
  refs.mainCounts.textContent   = "-";
  refs.subCounts.textContent    = "-";
  refs.decisionCard.innerHTML   = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
  refs.signalsList.innerHTML    = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
  refs.mergeMainTable.innerHTML = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
  refs.mergeSubTable.innerHTML  = `<div class="stack-item"><p>${escapeHtml(message)}</p></div>`;
}

async function fetchJSON(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    url.searchParams.set(key, String(value));
  });

  const response = await fetch(url);
  const payload  = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) {
    const detail = payload.error?.hint ? ` ${payload.error.hint}` : "";
    throw new Error((payload.error?.message || "请求失败。") + detail);
  }
  return payload;
}

function setStatus(message, tone) {
  refs.statusLine.textContent = message;
  refs.statusLine.classList.remove("status-error", "status-ok");
  if (tone === "error") refs.statusLine.classList.add("status-error");
  else if (tone === "ok") refs.statusLine.classList.add("status-ok");
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
