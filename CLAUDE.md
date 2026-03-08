# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
uv run python -m unittest discover -s tests -p "test_*.py"

# Run a single test file
uv run python -m unittest tests/test_chan_core_rules.py

# Run a single test case
uv run python -m unittest tests.test_chan_core_rules.TestClassName.test_method_name

# Lint / format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Main backtest
uv run python scripts/run_btc_4h_backtest.py

# Bar-by-bar Chan replay (manual verification)
uv run python scripts/run_chan_replay.py

# Chan diagnostic snapshot at a specific timestamp
uv run python scripts/run_chan_diagnostic.py

# Pre-download OHLCV data into local CSV cache
uv run python scripts/warm_cache.py
```

## Architecture

This is an algorithmic trading system built around **Chan Theory (缠论)**, a Chinese technical analysis framework. The system implements a full pipeline: data → signal generation → backtesting → reporting.

### Data Flow

```
Binance OHLCV (via CCXT + CSV cache)
    ↓
Chan Theory Engine  →  ChanSnapshot (market structure state)
    ↓
Signal Generator    →  SignalDecision (B1/B2/B3/S1/S2/S3 signals)
    ↓
Backtest Engine     →  BacktestReport (trades, equity curve, metrics)
    ↓
Output: CSV + JSON + Markdown in outputs/
```

### Core Data Structures (`src/ai_trader/types.py`)

All structures flow from raw bars up to a complete snapshot:
- `Bar` → `Fractal` → `Bi` (stroke) → `Segment` → `Zhongshu` (center/consolidation zone)
- `ChanSnapshot`: complete market structure at a point in time
- `SignalDecision`: action with signals, conflict level, and risk assessment
- `Trade` / `BacktestReport`: execution results

### Chan Theory Engine (`src/ai_trader/chan/`)

**`engine.py`** — the main entry point:
- `build_chan_state(bars_main, bars_sub, config)` → `ChanSnapshot`
- `generate_signal(snapshot, config)` → `SignalDecision`

**`core/`** — processing pipeline called in strict order:
1. `include.py` — merge K-lines by inclusion rules
2. `fractal.py` — detect top/bottom fractals
3. `stroke.py` — build Bi (strokes) from confirmed fractals
4. `segment.py` — build segments from strokes
5. `center.py` — identify Zhongshu (consolidation centers)
6. `trend_phase.py` — infer market state (trending vs ranging)
7. `divergence.py` — detect MACD divergence for signal confirmation
8. `buy_sell_points.py` — generate B1/B2/B3/S1/S2/S3 signals

**`config.py`** — `ChanConfig` with two execution modes:
- `strict_kline8` (default): conservative, requires B2/B3 confidence ≥ 0.65, min 50 main / 100 sub bars
- `pragmatic`: relaxed thresholds, allows S3 signals

### Backtest Engine (`src/ai_trader/backtest/engine.py`)

Iterates bars bar-by-bar (primary timeframe: 4h, sub timeframe: 1h). Key mechanisms:
- Conflict level detection: alignment between main and sub timeframe directions
- Drawdown-based position reduction: 50% reduce at 12% DD
- Freeze/recovery: pause at 18% DD, resume after 21 days or new qualifying setup
- Outputs to `outputs/`: `signals.csv`, `trades.csv`, `equity_curve.csv`, `report.json`, `summary.md`

### Environment Variables

```
AI_TRADER_DATA_DIR          # Local OHLCV CSV cache directory (default: data/raw/)
```

### Key Conventions

- All Chan theory processing must respect bar ordering — never shuffle or reindex bars mid-pipeline.
- `ChanSnapshot` is immutable after construction; signal generation reads it but never mutates it.
- Signal repaint (a signal changing on a previously closed bar) is tracked via `test_repaint_rate.py` and should be kept near zero.
- Tests use `make_synthetic_bars()` from `tests/test_utils.py` to avoid network calls.
