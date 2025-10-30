"""最小示例：拉取一次 MACD 交易信号。"""

from __future__ import annotations

from ai_trader import load_settings, run_once


def main() -> None:
    # 使用默认配置，可按需覆盖交易所/交易对等非敏感参数
    settings = load_settings(exchange_id="binance", symbol="BTC/USDT", timeframe="4h")

    # 调用 orchestrator 触发完整流程（行情 -> 指标 -> LLM 决策）
    result = run_once(settings)

    normalized = result.get("normalized") or result.get("parsed") or {}
    signal = normalized.get("signal", "hold")

    print("== AI Trader Signal ==")
    print(f"symbol: {normalized.get('symbol', settings.symbol)}")
    print(f"timeframe: {normalized.get('timeframe', settings.timeframe)}")
    print(f"signal: {signal}")


if __name__ == "__main__":
    main()
