# 缠论验图页 — 重设计规格文档

> 本文档供二次实现使用，读者无需了解前序对话内容。
> 目标：从现有代码出发，完整重写 `web/chan_review/` 下的三个文件。

---

## 一、项目背景

`web/chan_review/` 是一个**人工审查工具**，用于逐根回放历史 K 线，验证缠论算法的两件事：

1. **结构识别是否正确**：分型、笔、线段、中枢有没有画错
2. **信号时机是否正确**：B1/B2/B3/S1/S2/S3 信号在正确的 bar 上触发

后端服务（`scripts/run_chan_review_server.py`）已经稳定，**不需要改动**。
本次只改前端三个文件：`index.html` / `styles.css` / `app.js`。

### 后端 API（保持不变）

```
GET /api/defaults                          → 返回默认参数
GET /api/session?exchange=...&symbol=...   → 返回 bar 时间戳列表（供 slider 用）
GET /api/snapshot?...&asof=...             → 返回指定时间点的完整缠论快照
```

`/api/snapshot` 返回的数据结构（重要，前端需要消费）：

```json
{
  "snapshot": {
    "meta": { "asof": "...", "timeframe_main": "4h", "timeframe_sub": "1h",
              "window_main": 120, "window_sub": 180, "previous_main_bar_time": "..." },
    "decision": {
      "action": { "decision": "WAIT|BUY|SELL|HOLD|REDUCE", "reason": "..." },
      "cn_summary": "...",
      "market_state": { "trend_type": "...", "walk_type": "...", "phase": "...",
                        "zhongshu_count": 2, "current_stroke_dir": "up",
                        "current_segment_dir": "up" },
      "risk": { "conflict_level": "high", "notes": "..." }
    },
    "signals_full": [
      { "type": "B2", "level": "main", "confidence": 0.78,
        "trigger": "...", "invalid_if": "...",
        "event_time": "...", "available_time": "..." }
    ],
    "main": {  /* 主级别数据，见下 */ },
    "sub":  {  /* 次级别数据，同结构 */ }
  }
}
```

每个 level（main/sub）的结构：

```json
{
  "raw_bars":    [{ "index": 0, "time": "...", "open": 0, "high": 0, "low": 0, "close": 0 }],
  "merged_bars": [{ "index": 0, "time": "...", "open": 0, "high": 0, "low": 0, "close": 0,
                    "raw_start_index": 0, "raw_end_index": 2, "merge_size": 3,
                    "direction": "up" }],
  "fractals":    [{ "index": 0, "kind": "top|bottom", "price": 0,
                    "event_time": "...", "available_time": "..." }],
  "bis":         [{ "start_index": 0, "end_index": 4, "direction": "up|down",
                    "start_price": 0, "end_price": 0,
                    "event_time": "...", "available_time": "..." }],
  "segments":    [{ "start_index": 0, "end_index": 8, "direction": "up|down",
                    "high": 0, "low": 0,
                    "event_time": "...", "available_time": "..." }],
  "zhongshus":   [{ "start_index": 0, "end_index": 6,
                    "zd": 0, "zg": 0, "evolution": "...",
                    "event_time": "...", "available_time": "..." }]
}
```

---

## 二、现有代码的核心问题

| 问题 | 根本原因 |
|------|---------|
| K 线太小（7px 宽） | `window_main=120` 根 bar 全部挤在一张图里显示 |
| 分型三角形遮挡 K 线 | 三角顶点画在 `y(fractal.price)`，和影线端点重合 |
| 两张图高度不够 | 垂直叠放 50/50 分割，每张只有 ~300px |
| 缺乏全局视角 | 只有细节图，看不到当前位置在整段行情里在哪 |
| 右侧信息布局 | 轻微问题，不是核心痛点，小改即可 |

---

## 三、目标设计

### 3.1 整体布局

```
┌─ 顶栏 44px ──────────────────────────────────────────────────────┐
│  缠论验图 | BTC/USDT · 4h/1h · 2024-01-01 → 2025-12-31  [⚙配置] │
├─ 图层栏 32px ────────────────────────────────────────────────────┤
│  图层: [原K] [合K] [分型] [笔] [线段] [中枢]                      │
├───────────────────────────────────────┬──────────────────────────┤
│  左：图表区（flex:1，overflow-y:auto） │  右：控制面板（320px）   │
│                                       │                          │
│  ┌─ 主级别 4h ──────────────────────┐ │  ┌─ 决策卡 ───────────┐ │
│  │  细节图（detail，380px 高）       │ │  │  WAIT              │ │
│  │  显示最近 N 根（默认 40）         │ │  │  市场状态 / 风险    │ │
│  ├──────────────────────────────────┤ │  └────────────────────┘ │
│  │  缩略图（overview，50px 高）      │ │                          │
│  │  显示全部 raw_bars，当前窗口高亮  │ │  ┌─ 时间轴 ───────────┐ │
│  └──────────────────────────────────┘ │  │  2024-03-01 00:00  │ │
│                                       │  │  ━━━━━━━━━●━━━━━━  │ │
│  ┌─ 次级别 1h ──────────────────────┐ │  │  ◀ 上一根  下一根 ▶│ │
│  │  细节图（detail，300px 高）       │ │  └────────────────────┘ │
│  │  显示最近 M 根（默认 50）         │ │                          │
│  ├──────────────────────────────────┤ │  ┌─ 视图控制 ─────────┐ │
│  │  缩略图（overview，50px 高）      │ │  │  数据窗口 主120 次180│ │
│  └──────────────────────────────────┘ │  │  图表视窗 主 40 次 50│ │
│                                       │  └────────────────────┘ │
│  （图表区可垂直滚动，次图不在屏幕内   │                          │
│   时向下滚动即可看到）               │  ▸ 信号 (2)              │
│                                       │  ▸ 包含轨迹              │
└───────────────────────────────────────┴──────────────────────────┘
```

### 3.2 图表区详细结构

每个时间级别（主/次）占用一个"图表卡片"：

```
┌─ chart-card ──────────────────────────────────────────────────┐
│  chart-card-header: [eyebrow] [TF label] [counts]             │
├── detail-surface（高度由 CSS 固定，见下）───────────────────────┤
│  SVG 细节图：只渲染最近 view_main 根 raw bars                  │
│  所有图层（原K、合K、分型、笔、线段、中枢）都在这里画           │
├── overview-surface（50px 固定高）──────────────────────────────┤
│  SVG 缩略图：渲染全部 raw_bars，仅画收盘价折线 + 当前窗口框     │
└───────────────────────────────────────────────────────────────┘
```

---

## 四、关键设计决策

### 4.1 数据窗口 vs 图表视窗（重要）

两个独立概念，不能混用：

| 参数 | 去哪里 | 控制什么 |
|------|--------|---------|
| `window_main` / `window_sub` | 发给后端 `/api/snapshot` | 算法能"看到"的 bar 数，影响结构识别 |
| `view_main` / `view_sub` | 纯前端，不发后端 | 细节图里实际渲染多少根，不影响算法 |

实现方式：`renderDetailChart()` 拿到后端返回的 `raw_bars`（共 window_main 根），只取最后 `view_main` 根进行渲染。

### 4.2 分型标记的偏移修复

**现有问题**：三角顶点 `y(fractal.price)` = 影线端点，与 K 线重叠。

**修复方案**：在 SVG 像素坐标上偏移，而不是在价格坐标上偏移：

```js
// 顶分型：三角顶点在影线端点上方 FRACTAL_OFFSET 像素处
const FRACTAL_OFFSET = 10; // px，固定像素值

// top fractal
const tipY = y(item.price) - FRACTAL_OFFSET;
const points = `${x},${tipY} ${x - size},${tipY + size + 4} ${x + size},${tipY + size + 4}`;

// bottom fractal
const tipY = y(item.price) + FRACTAL_OFFSET;
const points = `${x},${tipY} ${x - size},${tipY - size - 4} ${x + size},${tipY - size - 4}`;
```

### 4.3 缩略图（Overview Strip）实现

缩略图只画**收盘价折线**，不画 K 线形态，高度固定 50px：

```js
function renderOverviewChart(container, levelData, viewStartIndex, viewEndIndex) {
  const bars = levelData.raw_bars;
  if (!bars.length) return;

  const width  = container.clientWidth;
  const height = 50;
  const margin = { top: 4, right: 8, bottom: 4, left: 8 };
  const plotW  = width  - margin.left - margin.right;
  const plotH  = height - margin.top  - margin.bottom;

  // 比例映射
  const minClose = Math.min(...bars.map(b => b.low));
  const maxClose = Math.max(...bars.map(b => b.high));
  const span     = Math.max(maxClose - minClose, 1);
  const xFn = (i) => margin.left + (i / (bars.length - 1)) * plotW;
  const yFn = (p) => margin.top  + ((maxClose - p) / span) * plotH;

  // 价格折线
  const pathD = bars.map((b, i) => `${i === 0 ? "M" : "L"}${xFn(i)},${yFn(b.close)}`).join(" ");

  // 当前视窗高亮框（对应 view_main 的位置）
  const totalBars    = bars.length;
  const viewStart    = viewStartIndex; // raw bars 里的数组下标（非 absIndex）
  const viewEnd      = viewEndIndex;
  const boxX         = xFn(viewStart);
  const boxW         = Math.max(xFn(viewEnd) - xFn(viewStart), 2);

  container.innerHTML = `
    <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}">
      <!-- 背景高亮框 -->
      <rect x="${boxX}" y="${margin.top}" width="${boxW}" height="${plotH}"
            fill="rgba(179, 83, 45, 0.12)" stroke="rgba(179, 83, 45, 0.5)"
            stroke-width="1" rx="1" />
      <!-- 价格折线 -->
      <path d="${pathD}" fill="none" stroke="#6c6154" stroke-width="1" opacity="0.7" />
    </svg>
  `;
}
```

调用时，计算 `viewStartIndex` 和 `viewEndIndex`：

```js
// raw_bars 是后端返回的完整 window_main 根 bars
// view_main 是前端显示根数
const totalBars     = levelData.raw_bars.length;
const viewEnd       = totalBars - 1;               // 最后一根
const viewStart     = Math.max(0, totalBars - view_main); // 往前数 view_main 根
renderOverviewChart(container, levelData, viewStart, viewEnd);
```

### 4.4 图表视窗裁切（Detail Chart）

在 `renderDetailChart()` 中，对 `raw_bars` 做尾部截取：

```js
// 只取最后 viewBars 根用于渲染
const displayBars = (viewBars > 0 && viewBars < allRawBars.length)
  ? allRawBars.slice(-viewBars)
  : allRawBars;

const rawStartAbs = displayBars[0].index;
const rawEndAbs   = displayBars[displayBars.length - 1].index;

// 只把落在 [rawStartAbs, rawEndAbs] 范围内的 merged bars 加入 mergedMap
mergedBars.forEach((item) => {
  if (item.raw_end_index < rawStartAbs || item.raw_start_index > rawEndAbs) return;
  // ...正常构建 mergedMap entry
});

// 价格轴只看可视区域内结构的价格，避免 Y 轴被屏幕外数据压缩
const extraPrices = [
  ...levelData.zhongshus
    .filter(z => mergedMap.has(z.start_index) || mergedMap.has(z.end_index))
    .flatMap(z => [z.zd, z.zg]),
  ...levelData.bis
    .filter(b => mergedMap.has(b.start_index) || mergedMap.has(b.end_index))
    .flatMap(b => [b.start_price, b.end_price]),
];
```

---

## 五、HTML 结构

```html
<div class="app-shell">

  <!-- 顶栏 -->
  <header class="topbar">
    <span class="topbar-brand">缠论验图</span>
    <span class="topbar-divider">|</span>
    <span class="topbar-session" id="session-info">-</span>
    <span class="topbar-spacer"></span>
    <span id="status-line" class="topbar-status">等待加载</span>
    <button class="btn-settings" id="settings-btn" type="button">⚙ 配置</button>
  </header>

  <!-- 图层栏（脱离图表区，独立一行） -->
  <div class="layer-bar">
    <span class="layer-label">图层</span>
    <button class="layer-btn active" data-layer="raw"       type="button">原K</button>
    <button class="layer-btn active" data-layer="merged"    type="button">合K</button>
    <button class="layer-btn active" data-layer="fractals"  type="button">分型</button>
    <button class="layer-btn active" data-layer="bis"       type="button">笔</button>
    <button class="layer-btn active" data-layer="segments"  type="button">线段</button>
    <button class="layer-btn active" data-layer="zhongshus" type="button">中枢</button>
  </div>

  <!-- 主工作区 -->
  <div class="workspace">

    <!-- 左：图表区（可垂直滚动） -->
    <div class="chart-panel">

      <!-- 主级别卡片 -->
      <div class="chart-card">
        <div class="chart-card-header">
          <span class="eyebrow">Main</span>
          <span id="main-tf-label" class="chart-tf">-</span>
          <span id="main-counts"   class="chart-counts">-</span>
        </div>
        <div id="main-detail"   class="detail-surface"></div>
        <div id="main-overview" class="overview-surface"></div>
      </div>

      <!-- 次级别卡片 -->
      <div class="chart-card">
        <div class="chart-card-header">
          <span class="eyebrow">Sub</span>
          <span id="sub-tf-label" class="chart-tf">-</span>
          <span id="sub-counts"   class="chart-counts">-</span>
        </div>
        <div id="sub-detail"   class="detail-surface"></div>
        <div id="sub-overview" class="overview-surface"></div>
      </div>

    </div>

    <!-- 右：控制面板（固定宽，可垂直滚动） -->
    <aside class="side-panel">

      <!-- 决策卡 -->
      <div class="side-block">
        <div id="decision-card" class="decision-card">
          <div class="stack-item"><p>等待加载。</p></div>
        </div>
      </div>

      <!-- 时间轴 -->
      <div class="side-block timeline-block">
        <div class="timeline-top">
          <span class="side-label">时间轴</span>
          <span id="asof-label" class="asof-label">-</span>
        </div>
        <input id="asof-slider" type="range" min="0" max="0" step="1" value="0" />
        <div class="step-row">
          <button id="step-prev" class="btn-step" type="button">◀ 上一根</button>
          <span id="session-summary" class="badge">未加载</span>
          <button id="step-next" class="btn-step" type="button">下一根 ▶</button>
        </div>
      </div>

      <!-- 视图控制 -->
      <div class="side-block view-block">
        <div class="params-grid">
          <div class="params-col">
            <span class="params-col-label">数据窗口</span>
            <div class="window-row">
              <label class="window-field"><span>主</span><input id="window-main" type="number" min="20" step="10" /></label>
              <label class="window-field"><span>次</span><input id="window-sub"  type="number" min="20" step="10" /></label>
            </div>
          </div>
          <div class="params-col">
            <span class="params-col-label">图表视窗</span>
            <div class="window-row">
              <label class="window-field"><span>主</span><input id="view-main" type="number" min="10" step="10" value="40" /></label>
              <label class="window-field"><span>次</span><input id="view-sub"  type="number" min="10" step="10" value="50" /></label>
            </div>
          </div>
        </div>
      </div>

      <!-- 信号（默认展开） -->
      <details class="side-block collapsible" open>
        <summary>信号 <span id="signal-count" class="badge">-</span></summary>
        <div id="signals-list" class="stack-list"></div>
      </details>

      <!-- 包含轨迹（默认折叠） -->
      <details class="side-block collapsible">
        <summary>包含轨迹</summary>
        <div class="audit-panels">
          <p class="eyebrow">主级别</p>
          <div id="merge-main-table" class="table-shell"></div>
          <p class="eyebrow">次级别</p>
          <div id="merge-sub-table" class="table-shell"></div>
        </div>
      </details>

    </aside>
  </div>

  <!-- 配置 Modal -->
  <div class="modal-overlay hidden" id="modal-overlay">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-head">
        <h2 id="modal-title">配置数据源</h2>
        <button class="btn-close" id="modal-close" type="button">✕</button>
      </div>
      <form id="control-form" class="settings-form">
        <div class="settings-grid">
          <label><span>交易所</span><input id="exchange"       name="exchange"        type="text" /></label>
          <label><span>交易对</span><input id="symbol"         name="symbol"          type="text" /></label>
          <label><span>主级别</span><input id="timeframe-main" name="timeframe_main"  type="text" /></label>
          <label><span>次级别</span><input id="timeframe-sub"  name="timeframe_sub"   type="text" /></label>
          <label><span>开始时间</span><input id="start"        name="start"           type="text" /></label>
          <label><span>结束时间</span><input id="end"          name="end"             type="text" /></label>
          <label class="span-2"><span>缠论模式</span>
            <select id="chan-mode" name="chan_mode">
              <option value="strict_kline8">strict_kline8</option>
              <option value="pragmatic">pragmatic</option>
            </select>
          </label>
        </div>
        <div class="modal-actions">
          <button type="button" id="modal-cancel"  class="btn-secondary">取消</button>
          <button type="submit" id="load-button"   class="btn-primary">加载数据</button>
        </div>
      </form>
    </div>
  </div>

</div>
<script src="/app.js" defer></script>
```

---

## 六、CSS 关键规则

```css
/* App Shell：顶栏 + 图层栏 + 工作区，垂直排列 */
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

/* 顶栏：固定 44px */
.topbar { height: 44px; flex-shrink: 0; }

/* 图层栏：独立一行，固定高度，紧贴顶栏下方 */
.layer-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 12px;
  flex-shrink: 0;
  background: var(--panel-alt);
  border-bottom: 1px solid var(--border);
}

/* 工作区：占剩余全部高度 */
.workspace {
  display: flex;
  flex: 1;
  overflow: hidden;  /* 子元素各自滚动 */
}

/* 图表区：可垂直滚动 */
.chart-panel {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

/* 每个图表卡片 */
.chart-card {
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  flex-shrink: 0;  /* 不压缩，确保高度固定 */
}

/* 细节图：固定高度 */
/* 主级别 */
#main-detail { height: 380px; }
/* 次级别 */
#sub-detail  { height: 300px; }

/* 缩略图：固定高度 */
.overview-surface {
  height: 50px;
  flex-shrink: 0;
  border-top: 1px solid var(--border);
  background: var(--panel-alt);
  cursor: default;
}

/* 右侧面板：固定宽，可垂直滚动 */
.side-panel {
  width: 300px;
  flex-shrink: 0;
  overflow-y: auto;
  border-left: 1px solid var(--border-mid);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--panel-alt);
}
```

颜色变量（继承现有 `styles.css` 的配色，无需改动）：
```css
--bg-page, --panel, --panel-alt, --border, --border-mid,
--text, --muted, --subtle, --rust, --gold, --green, --red, --blue, --ink
```

---

## 七、JavaScript 结构

### 7.1 状态对象

```js
const state = {
  defaults: null,
  params: null,      // 当前加载的会话参数（含 window_main/sub）
  session: null,     // /api/session 返回的数据
  snapshot: null,    // 最近一次 /api/snapshot 返回的数据
  snapshotTimer: null,
};
```

### 7.2 refs 对象（所有 DOM 引用）

```js
const refs = {
  // Form（在 Modal 里）
  form, exchange, symbol, timeframeMain, timeframeSub,
  start, end, chanMode,

  // 视图控制（在侧边栏里，不进 modal）
  windowMain, windowSub,   // 数据窗口，发后端
  viewMain, viewSub,        // 图表视窗，纯前端

  // 顶栏
  sessionInfo, statusLine,

  // 时间轴
  slider, asofLabel, sessionSummary,
  stepPrev, stepNext,

  // 图表容器
  mainDetail, mainOverview, mainTfLabel, mainCounts,
  subDetail,  subOverview,  subTfLabel,  subCounts,

  // 决策/信号/审计
  decisionCard, signalsList, signalCount,
  mergeMainTable, mergeSubTable,

  // 图层按钮
  layerBtns,  // document.querySelectorAll('.layer-btn')

  // Modal
  settingsBtn, modalOverlay, modalClose, modalCancel,
};
```

### 7.3 函数列表

```
boot()                     — 启动：获取 defaults → loadSession(true)
applyDefaults(defaults)    — 填充表单默认值
collectFormParams()        — 从表单收集参数（不含 view_main/sub）
snapshotParams()           — 当前快照参数（含实时 window_main/sub）
loadSession(resetSlider)   — 调 /api/session，设置 slider 范围
loadSnapshot()             — 调 /api/snapshot，渲染结果
queueSnapshot(delay)       — 防抖包装 loadSnapshot
updateAsofLabel()          — 更新顶部时间戳显示

renderSnapshot(snapshot)   — 总调度：decision + signals + tables + charts
renderCharts(snapshot)     — 读取 viewMain/viewSub，分别调用两图渲染
renderDetailChart(container, levelData, title, viewBars)
                           — 主图：裁切 raw_bars 至 viewBars 根后渲染
renderOverviewChart(container, levelData, viewStart, viewEnd)
                           — 缩略图：全部 raw_bars 的收盘价折线 + 窗口高亮框
renderDecision(snapshot)   — 渲染决策卡
renderSignals(signals)     — 渲染信号列表，更新 signal-count badge
renderMergeTable(container, mergedBars)
                           — 渲染包含轨迹表格
renderEmptyState(message)  — 清空所有图表区域，显示加载/错误提示

showModal() / hideModal()
currentLayers()            — 返回 { raw, merged, fractals, bis, segments, zhongshus }
setStatus(message, tone)   — 更新顶栏状态文字（tone: 'ok'|'error'|''）
escapeHtml(value)          — XSS 防护
formatTimestamp(value)     — "2024-03-01T00:00:00Z" → "2024-03-01 00:00:00"
shortTimestamp(value)      — 取 MM-DD HH:mm 部分
normalizeWindow(value, fallback)
                           — parseInt，非法值返回 fallback
fetchJSON(path, params)    — GET 请求，解析 JSON，抛出 error.message

buildTicks(min, max, count) — 等间距价格刻度数组
buildTimeTicks(bars)        — 取首/中/尾三个时间刻度

renderGrid(...)             — 画网格线（不变）
renderAxes(...)             — 画坐标轴标签（不变）
renderRawCandles(displayBars, rawX, y, bodyWidth)  — 原始 K 线（不变）
renderMergedCandles(mergedBars, mergedMap, y)       — 包含后 K 线（不变）
renderFractals(fractals, mergedMap, y)              — 分型标记（改：偏移修复）
renderBis(bis, mergedMap, y)                        — 笔（不变）
renderSegments(segments, mergedMap, y)              — 线段（不变）
renderZhongshus(zhongshus, mergedMap, y)            — 中枢（不变）
```

### 7.4 事件绑定（bindEvents）

```js
// Modal
refs.settingsBtn.addEventListener('click', showModal);
refs.modalClose.addEventListener('click', hideModal);
refs.modalCancel.addEventListener('click', hideModal);
refs.modalOverlay.addEventListener('click', e => { if (e.target === refs.modalOverlay) hideModal(); });

// 加载数据
refs.form.addEventListener('submit', async e => {
  e.preventDefault();
  hideModal();
  await loadSession(true);
});

// Slider
refs.slider.addEventListener('input', () => { updateAsofLabel(); queueSnapshot(40); });

// 步进按钮
refs.stepPrev.addEventListener('click', () => { step(-1); });
refs.stepNext.addEventListener('click', () => { step(+1); });

// 键盘左右箭头（非输入框时）
document.addEventListener('keydown', e => {
  if (['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) return;
  if (e.key === 'ArrowLeft')  step(-1);
  if (e.key === 'ArrowRight') step(+1);
});

// 数据窗口变化 → 重新请求后端
[refs.windowMain, refs.windowSub].forEach(inp =>
  inp.addEventListener('change', () => queueSnapshot(120))
);

// 图表视窗变化 → 纯前端重渲染
[refs.viewMain, refs.viewSub].forEach(inp =>
  inp.addEventListener('change', () => { if (state.snapshot) renderCharts(state.snapshot); })
);

// 图层 toggle
refs.layerBtns.forEach(btn =>
  btn.addEventListener('click', () => {
    btn.classList.toggle('active');
    if (state.snapshot) renderSnapshot(state.snapshot);
  })
);

// 窗口大小变化
window.addEventListener('resize', () => { if (state.snapshot) renderCharts(state.snapshot); });

// step 辅助函数
function step(delta) {
  const v = Number(refs.slider.value);
  const min = Number(refs.slider.min);
  const max = Number(refs.slider.max);
  const next = Math.max(min, Math.min(max, v + delta));
  if (next === v) return;
  refs.slider.value = String(next);
  updateAsofLabel();
  queueSnapshot(0);
}
```

---

## 八、renderDetailChart 完整逻辑

这是最核心的改动，完整描述如下：

```js
function renderDetailChart(container, levelData, title, viewBars) {
  const allRawBars = levelData?.raw_bars || [];
  if (!allRawBars.length) {
    container.innerHTML = `<div class="chart-empty">${escapeHtml(title)} 暂无数据</div>`;
    return;
  }

  const mergedBars = levelData.merged_bars || [];
  const layers     = currentLayers();

  // 1. 获取容器实际尺寸（SVG 填满容器）
  const width  = Math.max(container.clientWidth - 2, 400);
  const height = Math.max(container.clientHeight || 300, 200);
  const margin = { top: 16, right: 22, bottom: 26, left: 66 };
  const plotW  = width  - margin.left - margin.right;
  const plotH  = height - margin.top  - margin.bottom;

  // 2. 前端裁切：只取最后 viewBars 根
  const rawBars     = (viewBars > 0 && viewBars < allRawBars.length)
                      ? allRawBars.slice(-viewBars)
                      : allRawBars;
  const rawStartAbs = rawBars[0].index;
  const rawEndAbs   = rawBars[rawBars.length - 1].index;

  // 3. 坐标映射
  const step      = rawBars.length > 1 ? plotW / (rawBars.length - 1) : plotW;
  const bodyWidth = Math.max(4, Math.min(step * 0.6, 16));
  const rawX = (absIdx) => rawBars.length === 1
    ? margin.left + plotW / 2
    : margin.left + (absIdx - rawStartAbs) * step;

  // 4. 构建 mergedMap（只包含视窗内的 merged bars）
  const mergedMap = new Map();
  mergedBars.forEach(item => {
    if (item.raw_end_index < rawStartAbs || item.raw_start_index > rawEndAbs) return;
    const cx = Math.max(item.raw_start_index, rawStartAbs);
    const cy = Math.min(item.raw_end_index,   rawEndAbs);
    const sx = rawX(cx), ex = rawX(cy);
    mergedMap.set(item.index, {
      startX: sx, endX: ex,
      centerX: (sx + ex) / 2,
      width: Math.max(ex - sx, bodyWidth),
    });
  });

  // 5. 价格范围（只用视窗内可见结构的价格）
  const extraPrices = [
    ...levelData.zhongshus
      .filter(z => mergedMap.has(z.start_index) || mergedMap.has(z.end_index))
      .flatMap(z => [z.zd, z.zg]),
    ...levelData.bis
      .filter(b => mergedMap.has(b.start_index) || mergedMap.has(b.end_index))
      .flatMap(b => [b.start_price, b.end_price]),
  ];
  const lows  = rawBars.map(b => b.low).concat(extraPrices);
  const highs = rawBars.map(b => b.high).concat(extraPrices);
  const span  = Math.max(Math.max(...highs) - Math.min(...lows), Math.max(...highs) * 0.01, 1);
  const priceMin = Math.min(...lows)  - span * 0.08;
  const priceMax = Math.max(...highs) + span * 0.08;
  const y = p => margin.top + ((priceMax - p) / (priceMax - priceMin)) * plotH;

  // 6. 拼装图层并输出 SVG
  const priceTicks = buildTicks(priceMin, priceMax, 5);
  const xTicks     = buildTimeTicks(rawBars);
  const html = [
    renderGrid(priceTicks, xTicks, margin, width, height, y, rawX),
    layers.zhongshus ? renderZhongshus(levelData.zhongshus, mergedMap, y) : '',
    layers.segments  ? renderSegments(levelData.segments,   mergedMap, y) : '',
    layers.bis       ? renderBis(levelData.bis,             mergedMap, y) : '',
    layers.raw       ? renderRawCandles(rawBars, rawX, y, bodyWidth)      : '',
    layers.merged    ? renderMergedCandles(mergedBars, mergedMap, y)      : '',
    layers.fractals  ? renderFractals(levelData.fractals, mergedMap, y)   : '',
    renderAxes(priceTicks, xTicks, margin, width, height, y, rawX),
  ].join('');

  container.innerHTML = `
    <svg class="svg-chart" viewBox="0 0 ${width} ${height}"
         width="100%" height="100%" role="img" aria-label="${escapeHtml(title)}">
      <title>${escapeHtml(title)}</title>
      ${html}
    </svg>`;
}
```

---

## 九、renderFractals 修复（偏移版）

```js
const FRACTAL_OFFSET = 10; // 固定像素，不依赖价格

function renderFractals(fractals, mergedMap, y) {
  return fractals.map(item => {
    const pos = mergedMap.get(item.index);
    if (!pos) return '';
    const x    = pos.centerX;
    const size = 7;
    const isTop = item.kind === 'top';
    const fill  = isTop ? COLORS.red : COLORS.green;

    // 偏移：top 分型在影线上方 FRACTAL_OFFSET 像素，bottom 在下方
    const anchorY = y(item.price);
    const tipY    = isTop ? anchorY - FRACTAL_OFFSET : anchorY + FRACTAL_OFFSET;

    const points = isTop
      ? `${x},${tipY} ${x - size},${tipY + size + 3} ${x + size},${tipY + size + 3}`
      : `${x},${tipY} ${x - size},${tipY - size - 3} ${x + size},${tipY - size - 3}`;

    const title = `${item.kind} fractal #${item.index}\nevent: ${formatTimestamp(item.event_time)}\navail: ${formatTimestamp(item.available_time)}`;
    return `<polygon points="${points}" fill="${fill}" opacity="0.88">
      <title>${escapeHtml(title)}</title>
    </polygon>`;
  }).join('');
}
```

---

## 十、renderCharts 调用示意

```js
function renderCharts(snapshot) {
  const meta     = snapshot.meta;
  const viewMain = normalizeWindow(refs.viewMain?.value, 40);
  const viewSub  = normalizeWindow(refs.viewSub?.value, 50);

  refs.mainTfLabel.textContent = `${meta.timeframe_main} 主级别`;
  refs.subTfLabel.textContent  = `${meta.timeframe_sub} 次级别`;

  renderDetailChart(refs.mainDetail, snapshot.main, `${meta.timeframe_main} 主级别`, viewMain);
  renderDetailChart(refs.subDetail,  snapshot.sub,  `${meta.timeframe_sub} 次级别`, viewSub);

  // 缩略图：计算当前视窗在全部 raw_bars 中的起止下标
  const calcViewRange = (levelData, viewBars) => {
    const total = levelData?.raw_bars?.length ?? 0;
    return { start: Math.max(0, total - viewBars), end: total - 1 };
  };
  const mr = calcViewRange(snapshot.main, viewMain);
  const sr = calcViewRange(snapshot.sub,  viewSub);
  renderOverviewChart(refs.mainOverview, snapshot.main, mr.start, mr.end);
  renderOverviewChart(refs.subOverview,  snapshot.sub,  sr.start, sr.end);

  refs.mainCounts.textContent = renderCountLine(snapshot.main);
  refs.subCounts.textContent  = renderCountLine(snapshot.sub);
}
```

---

## 十一、不需要改的部分

以下函数逻辑**完全保留**，只是函数名从 `renderChart` 改为 `renderDetailChart`：

- `renderGrid`
- `renderAxes`
- `renderRawCandles`
- `renderMergedCandles`
- `renderBis`
- `renderSegments`
- `renderZhongshus`
- `buildTicks`
- `buildTimeTicks`
- `renderDecision`
- `renderSignals`
- `renderMergeTable`
- `fetchJSON`
- `escapeHtml`
- `formatTimestamp` / `shortTimestamp`
- `normalizeWindow`

---

## 十二、实现检查清单

实现完成后，验证以下几点：

- [ ] 页面加载后，细节图默认显示 40/50 根，K 线宽度明显大于之前
- [ ] 缩略图（50px）出现在每张细节图下方，可以看到价格走势折线
- [ ] 缩略图上有高亮矩形，对应细节图当前显示的时间范围
- [ ] 修改"图表视窗"数字后，细节图立即更新，缩略图高亮框也跟着变化
- [ ] 修改"数据窗口"数字后，会重新请求后端（会有网络请求）
- [ ] 分型三角形不再贴着 K 线影线，有 10px 的间距
- [ ] ← → 键盘步进正常工作
- [ ] 配置 Modal 打开/关闭正常
- [ ] 页面在 1280px 宽屏幕下图表不溢出

---

*文档版本：2026-03-08*
