# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.12 algorithmic trading system for Chan Theory (缠论) signal generation and backtesting.

- `src/ai_trader/` contains package code.
- `src/ai_trader/chan/` implements Chan state construction, structure rules, and B1/B2/B3/S1/S2/S3 signals.
- `src/ai_trader/backtest/` contains execution, metrics, and significance logic.
- `src/ai_trader/data/` handles OHLCV loading and Binance cache access.
- `scripts/` contains cache warming, diagnostics, replays, audits, and BTC 4h backtests.
- `tests/` contains unit and contract tests plus fixtures in `tests/fixtures/`.
- `data/` and `outputs/` are local artifacts; avoid committing generated caches or reports unless needed.

## Build, Test, and Development Commands

- `uv sync` installs dependencies from the lockfile.
- `uv run python -m unittest discover -s tests -p "test_*.py"` runs the full test suite.
- `uv run python -m unittest tests/test_chan_core_rules.py` runs one test file.
- `uv run ruff check src/ tests/` checks linting.
- `uv run ruff format src/ tests/` formats source and tests.
- `uv run python scripts/warm_cache.py` preloads OHLCV CSV cache data.
- `uv run python scripts/run_btc_4h_backtest.py` runs the main BTC 4h backtest.
- `uv run python scripts/run_chan_diagnostic.py` and `uv run python scripts/run_chan_replay.py` generate diagnostics and bar-by-bar replay output.

## Coding Style & Naming Conventions

Use four-space indentation and Python 3.12 syntax. Prefer typed dataclasses and existing domain names such as `Bar`, `Fractal`, `Bi`, `Segment`, `Zhongshu`, `ChanSnapshot`, and `SignalDecision`. Keep Chan processing order stable; do not reorder bars mid-pipeline. Use `snake_case` for modules, functions, variables, and test methods.

## Testing Guidelines

Tests use Python `unittest`. Name files `test_*.py` and methods `test_*`. Prefer synthetic bars from `tests/test_utils.py` for deterministic tests without network calls. Add focused tests when changing signal semantics, repaint behavior, temporal consistency, execution rules, or data-quality handling.

## Commit & Pull Request Guidelines

Recent commits use short summaries, sometimes with prefixes such as `feat:`. Keep messages scoped to the behavioral change, for example `feat: tighten Chan replay diagnostics` or `Fix third-class signal confidence`.

Pull requests should include strategy or execution changes, tests run, relevant data intervals, and generated output paths under `outputs/`. Link related issues when available. Include screenshots only for UI-facing changes under `web/`.

## Security & Configuration Tips

Set `AI_TRADER_DATA_DIR` to override the default `data/raw/` cache. Keep API keys, exchange credentials, and private datasets out of Git. Treat backtest output as local evidence unless a PR needs a small reproducible fixture.
