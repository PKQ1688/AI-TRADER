# AI-TRADER
Using AI for Financial Investment

## BTC 4h 缠论回测

### 先预热本地缓存（推荐）

```bash
uv run python scripts/warm_cache.py \
  --exchange binance \
  --symbol BTC/USDT \
  --start 2022-02-10T00:00:00Z \
  --end 2026-02-10T00:00:00Z \
  --timeframes 4h 1h
```

可选：通过环境变量更改缓存目录（默认 `data/raw/`）：

```bash
export AI_TRADER_DATA_DIR=/absolute/path/to/cache
```

### 运行

```bash
uv sync
uv run python scripts/run_btc_4h_backtest.py
```

输出目录示例：

`outputs/backtest/btc_4h_1h/<run_id>/`

包含：

- `signals.csv`
- `trades.csv`
- `equity_curve.csv`
- `report.json`
- `summary.md`

### 测试

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

## 信号契约（v2）

`generate_signal(...).to_contract_dict()` 输出核心结构如下：

- `data_quality`: `ok | insufficient`
- `market_state`:
  - `trend_type`: `up | down | range`
  - `walk_type`: `consolidation | trend`
  - `phase`: `trending | consolidating | transitional`
  - `zhongshu_count`: 非负整数
  - `last_zhongshu`: `{zd, zg, gg, dd}`
  - `current_stroke_dir`: `up | down`
  - `current_segment_dir`: `up | down`
- `signals[]`:
  - `type`: `B1 | B2 | B3 | S1 | S2 | S3`
  - `level`: `main | sub`
  - `trigger` / `invalid_if` / `confidence`
- `action.decision`: `buy | sell | reduce | hold | wait`
- `risk.conflict_level`: `none | low | high`
- `cn_summary`: 中文结论

## 执行过滤规则（strict_kline8 默认）

信号会完整输出，但动作层默认执行保守过滤：

- 买入：使用 `B2/B3`，且 `confidence >= 0.65`
- 买入冲突约束：`conflict_level` 不能为 `high`
- 中阴阶段：默认 `wait`；仅出现明确三类点（`B3/S3`）才允许突破默认等待
- 减仓：使用 `S2/S3`，且默认只在 `conflict_level=high` 时触发
- `strict_kline8` 仍以保守确认和高冲突约束为主，不直接把减仓信号当作开空信号

## 结构诊断（真实 BTC 行情）

用于输出分型/笔/线段/中枢逐层快照与最终决策：

```bash
uv run python scripts/run_chan_diagnostic.py \
  --exchange binance \
  --symbol BTC/USDT \
  --timeframe-main 4h \
  --timeframe-sub 1h \
  --start 2024-01-01T00:00:00Z \
  --end 2026-02-10T00:00:00Z \
  --asof 2026-02-10T00:00:00Z
```

输出目录示例：

`outputs/diagnostics/BTCUSDT_4h_1h/<run_id>/`

包含：

- `snapshot_meta.json`（结构统计与尾部快照）
- `decision.json`（标准决策契约）
- `fractals_main.csv` / `fractals_sub.csv`
- `bis_main.csv` / `bis_sub.csv`
- `segments_main.csv` / `segments_sub.csv`
- `zhongshus_main.csv` / `zhongshus_sub.csv`
- `summary.md`（可读摘要）

## 逐 Bar 结构回放

用于按主级别 K 线逐根回放，输出每根 bar 的 market state、signals、action，方便人工对照盘感检查。

```bash
uv run python scripts/run_chan_replay.py \
  --exchange binance \
  --symbol BTC/USDT \
  --timeframe-main 4h \
  --timeframe-sub 1h \
  --start 2024-01-01T00:00:00Z \
  --end 2024-06-30T23:59:59Z \
  --tail-bars 30
```

输出目录示例：

`outputs/replays/BTCUSDT_4h_1h/<run_id>/`

包含：

- `replay_rows.csv`（每根主级别 bar 的完整回放明细）
- `focus_rows.csv`（有信号或非 `hold/wait` 动作的重点 bar）
- `summary.json`
- `summary.md`

## 本地人工验图页

用于在浏览器里逐根回放主级别 bar，人工核对：

- 原始 K 线 / 包含处理后的 K 线
- 分型 / 笔 / 线段 / 中枢
- 当前 `signals` 与 `event_time / available_time`

启动示例：

```bash
uv run python scripts/run_chan_review_server.py \
  --exchange binance \
  --symbol BTC/USDT \
  --timeframe-main 4h \
  --timeframe-sub 1h \
  --start 2024-01-01T00:00:00Z \
  --end 2024-03-01T00:00:00Z \
  --chan-mode strict_kline8 \
  --window-main 120 \
  --window-sub 180
```

默认地址：

`http://127.0.0.1:8765`

说明：

- 页面不会在前端重算缠论结构，而是直接展示 Python 引擎返回的快照。
- 页面内可按图层开关分别切换：原始 K 线、包含后 K 线、分型、笔、线段、中枢。
- 包含处理支持显示”合并后的 K 线由哪些原始 K 线组成”。
- `--window-main` / `--window-sub` 控制主/次级别可视 bar 数量（默认 120 / 180）。
- 若本地缓存不足，请先运行 `scripts/warm_cache.py` 预热数据。

## 视觉复核（OpenAI 兼容 `chat/completions`）

先准备一个诊断目录，再提供一张或多张图表截图给视觉模型复核。

推荐通过环境变量注入配置：

```bash
export AI_TRADER_OPENAI_BASE_URL=https://www.packyapi.com
export AI_TRADER_OPENAI_MODEL=gpt-5.4
export AI_TRADER_OPENAI_API_KEY=your_api_key
```

运行示例：

```bash
uv run python scripts/run_chan_vlm_review.py \
  --diagnostic-dir outputs/diagnostics/BTCUSDT_4h_1h/<run_id> \
  --image /absolute/path/to/chart-main.png \
  --image /absolute/path/to/chart-sub.png
```

输出目录示例：

`outputs/vision_reviews/BTCUSDT_4h_1h/<run_id>/`

包含：

- `review_input.json`（发送前的结构化上下文和请求元数据，不含 API key）
- `review_response.json`（模型原始返回）
- `summary.md`（可读摘要）

## 过滤规则对比（strict_kline8 vs pragmatic）

在同一历史区间同时跑两套执行模式，对比核心指标、稳定性和动作分布：

```bash
uv run python scripts/run_filter_comparison.py
```

输出目录示例：

`outputs/comparison/filter_modes/<run_id>/`

包含：

- `comparison.json`（两套配置的完整指标与 delta）
- `summary.md`（可读对比表格，含总收益率、年化收益、最大回撤、夏普、交易数、期望收益、p-value、repaint rate）

## Kline8 对齐审计

按整段历史逐根回放，逐条校验 `B1/B2/B3/S1/S2/S3` 的结构条件和动作约束：

```bash
uv run python scripts/run_kline8_alignment_audit.py \
  --exchange binance \
  --symbol BTC/USDT \
  --timeframe-main 4h \
  --timeframe-sub 1h \
  --start 2024-01-01T00:00:00Z \
  --end 2025-12-31T23:59:59Z
```

输出目录示例：

`outputs/diagnostics/audit_kline8_BTCUSDT_4h_1h/<run_id>/`

包含：

- `summary.md` / `summary.json`
- `signal_audit_rows.csv`（每条信号逐项校验明细）
