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

时间约定：本地 CSV 与交易所接口的 K 线 `time` 是开盘时间；`load_ohlcv` 返回给缠论、回放和回测的 `Bar.time` 统一平移为收盘后可用时间。因此所有 `start`/`end`/`asof` 参数都按“已收完可使用”的时间理解，避免把未完成 K 线提前纳入结构判断。

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

## 执行过滤规则（orthodox_chan 默认）

信号会完整输出，但动作层默认执行保守过滤：

- 买入：使用 `B2/B3`，且 `confidence >= 0.65`
- 买入阶段约束：仅在 `phase=trending` 时执行；盘整/中阴阶段先等待
- 买入冲突约束：`conflict_level` 不能为 `high`
- 中阴阶段：默认 `wait`；`B3/S3` 先降级为观察或减仓，不直接开仓/反手
- 减仓：使用 `S2/S3`，且默认只在 `conflict_level=high` 时触发
- `orthodox_chan` 现在是默认正统模式；`strict_kline8` 仍保留更严格的确认，不直接把减仓信号当作开空信号

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
