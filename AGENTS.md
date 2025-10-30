# Repository Guidelines

## Project Structure & Module Organization
Source lives under `src/ai_trader/`, split by responsibility: `agents/` holds trading agent orchestration, `config/` wraps runtime settings, `data/` integrates with CCXT gateways, `core/logging/` centralizes structured logging, and `orchestrator/runner.py` wires the end-to-end signal flow. Import `ai_trader` at package root for the public API (`run_once`, `load_settings`). Example usage resides in `examples/basic_signal.py`; treat `dist/` as generated output and never hand-edit. Keep new assets co-located with their domain modules and mirror that layout when adding tests or utilities.

## Build, Test, and Development Commands
Use `uv sync` to create the Python 3.12 environment from `pyproject.toml`/`uv.lock`. Run `uv run python examples/basic_signal.py` for a smoke test of the full agent pipeline. During development, `uv run python -c "from ai_trader import run_once; print(run_once())"` triggers a single inference with current settings, making it easy to inspect normalized signals.

## Coding Style & Naming Conventions
Adhere to PEP 8 with four-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and upper-case constants for configuration defaults. Prefer dependency injection via constructor parameters to honor SRP and DIP. Run `uv run ruff check src` before every PR; `uv run ruff format src` keeps formatting consistent. Keep modules lean—extract shared helpers instead of duplicating logic across agents and orchestrator layers.

## Testing Guidelines
Adopt `pytest` under a top-level `tests/` package that mirrors `ai_trader` namespaces (for example, `tests/orchestrator/test_runner.py`). Name files `test_<module>.py` and use fixtures to stub `CcxtGateway` responses. Target ≥80% statement coverage with `uv run pytest --cov=ai_trader --cov-report=term-missing`. Add integration tests for happy-path signal generation plus edge cases where tool payloads are missing or malformed.

## Commit & Pull Request Guidelines
The history follows Conventional Commits (`feat:`, `refactor:`, etc.); continue with `type(scope?): imperative summary`. Provide meaningful bodies when behavior shifts or migrations are required. For PRs, include: concise summary, risk/rollback notes, test evidence (`uv run pytest` output), any configuration prerequisites, and links to tracking issues. Request review from a maintainer familiar with the touched module to preserve clear ownership boundaries.

## Security & Configuration Tips
Store secrets in a local `.env`; `Settings` reads `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENAI_BASE_URL`, and `DEEPSEEK_BASE_URL`. Never commit credentials or per-user debug artifacts. Validate new integrations against sandbox keys first, and document any required environment variables in `README.md` and example scripts so agents remain reproducible.
