from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from ai_trader.backtest.engine import run_backtest
from ai_trader.types import Action, BacktestConfig, DataQuality, MarketState, Risk, Signal, SignalDecision
from tests.test_utils import make_synthetic_bars


def _decision(asof_time, action: str, signals: list[Signal]) -> SignalDecision:
    return SignalDecision(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe_main="4h",
        timeframe_sub="1h",
        data_quality=DataQuality(status="ok", notes=""),
        market_state=MarketState(trend_type="up"),
        signals=signals,
        action=Action(decision=action, reason=action),
        risk=Risk(conflict_level="low", notes=""),
        cn_summary=action,
    )


class BacktestExecutionSemanticsTest(unittest.TestCase):
    def setUp(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        self.bars_main = make_synthetic_bars(start=start, count=260, step_hours=4)
        self.bars_sub = make_synthetic_bars(start=start, count=1040, step_hours=1)

    def _snapshot(self, asof_time):
        return SimpleNamespace(asof_time=asof_time, last_zhongshu_main=None)

    def test_strict_reduce_signal_does_not_open_short(self) -> None:
        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            s3 = Signal(
                type="S3",
                level="main",
                trigger="s3",
                invalid_if="invalid",
                confidence=0.70,
                event_time=snapshot.asof_time,
                available_time=snapshot.asof_time,
                invalid_price=100.0,
            )
            return _decision(snapshot.asof_time, "reduce", [s3])

        with patch("ai_trader.backtest.engine.build_chan_state", side_effect=fake_build_chan_state), patch(
            "ai_trader.backtest.engine.generate_signal",
            side_effect=fake_generate_signal,
        ):
            report = run_backtest(
                config=BacktestConfig(chan_mode="strict_kline8"),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(report.metrics["trade_count"], 0.0)
        self.assertEqual(report.metrics["total_return"], 0.0)

    def test_raw_s3_signal_does_not_override_hold_action(self) -> None:
        buy_time = self.bars_main[120].time
        noisy_hold_time = self.bars_main[121].time
        sell_time = self.bars_main[122].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == buy_time:
                signal = Signal(
                    type="B3",
                    level="main",
                    trigger="b3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=100.0,
                )
                return _decision(snapshot.asof_time, "buy", [signal])

            if snapshot.asof_time == noisy_hold_time:
                signal = Signal(
                    type="S3",
                    level="main",
                    trigger="s3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=120.0,
                )
                return _decision(snapshot.asof_time, "hold", [signal])

            if snapshot.asof_time == sell_time:
                signal = Signal(
                    type="S3",
                    level="main",
                    trigger="s3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=120.0,
                )
                return _decision(snapshot.asof_time, "sell", [signal])

            return _decision(snapshot.asof_time, "hold", [])

        with patch("ai_trader.backtest.engine.build_chan_state", side_effect=fake_build_chan_state), patch(
            "ai_trader.backtest.engine.generate_signal",
            side_effect=fake_generate_signal,
        ):
            report = run_backtest(
                config=BacktestConfig(chan_mode="strict_kline8"),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].entry_time, self.bars_main[121].time)
        self.assertEqual(report.trades[0].exit_time, self.bars_main[123].time)

    def test_same_reduce_signal_only_applies_once_per_position(self) -> None:
        buy_time = self.bars_main[120].time
        reduce_time = self.bars_main[121].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        reduce_signal = Signal(
            type="S3",
            level="main",
            trigger="s3",
            invalid_if="invalid",
            confidence=0.70,
            event_time=reduce_time,
            available_time=reduce_time,
            invalid_price=120.0,
        )

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == buy_time:
                signal = Signal(
                    type="B2",
                    level="sub",
                    trigger="b2",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=100.0,
                )
                return _decision(snapshot.asof_time, "buy", [signal])

            if snapshot.asof_time >= reduce_time:
                return _decision(snapshot.asof_time, "reduce", [reduce_signal])

            return _decision(snapshot.asof_time, "hold", [])

        with patch("ai_trader.backtest.engine.build_chan_state", side_effect=fake_build_chan_state), patch(
            "ai_trader.backtest.engine.generate_signal",
            side_effect=fake_generate_signal,
        ):
            report = run_backtest(
                config=BacktestConfig(chan_mode="strict_kline8"),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)


if __name__ == "__main__":
    unittest.main()
