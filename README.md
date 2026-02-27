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

- 买入：仅使用 `B3`，且 `confidence >= 0.65`
- 买入冲突约束：`conflict_level` 不能为 `high`
- 中阴阶段：默认 `wait`；仅出现明确三类点（`B3/S3`）才允许突破默认等待
- 减仓：仅使用 `S3`，且默认只在 `conflict_level=high` 时触发
- `B2/S2` 默认作为观察信号保留输出，不直接触发交易动作（`pragmatic` 模式可放宽）

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
