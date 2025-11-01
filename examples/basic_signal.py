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
    symbol = normalized.get('symbol', settings.symbol)
    timeframe = normalized.get('timeframe', settings.timeframe)

    # 根据信号类型添加表情符号和颜色
    signal_emoji = {
        "buy": "🟢",
        "sell": "🔴",
        "hold": "🟡"
    }

    signal_desc = {
        "buy": "买入",
        "sell": "卖出",
        "hold": "观望"
    }

    print(f"\n📊 {symbol} ({timeframe})")
    print(f"{signal_emoji.get(signal, '⚪')} 交易建议: {signal_desc.get(signal, '未知')}")


if __name__ == "__main__":
    main()
