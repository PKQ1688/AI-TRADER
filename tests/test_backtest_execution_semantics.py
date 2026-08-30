from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from ai_trader.backtest.engine import run_backtest
from ai_trader.types import (
    Action,
    Bar,
    BacktestConfig,
    DataQuality,
    DEFAULT_STRUCTURE_LOOKBACK_MAIN_BARS,
    DEFAULT_STRUCTURE_LOOKBACK_SUB_BARS,
    FundingRate,
    MarketState,
    Risk,
    Signal,
    SignalDecision,
)
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
        # The default BacktestConfig starts on 2022-02-10.  Keeping exactly
        # 120 warm-up bars before it makes execution fixtures exercise the
        # configured evaluation boundary instead of relying on pre-start
        # signals.
        start = datetime(2022, 1, 21, tzinfo=timezone.utc)
        self.bars_main = make_synthetic_bars(start=start, count=260, step_hours=4)
        self.bars_sub = make_synthetic_bars(start=start, count=1040, step_hours=1)

    def _snapshot(self, asof_time, center_start_index: int | None = None):
        zhongshu = None
        if center_start_index is not None:
            zhongshu = SimpleNamespace(
                start_index=center_start_index, available_time=asof_time
            )
        return SimpleNamespace(asof_time=asof_time, last_zhongshu_main=zhongshu)

    def test_backtest_prefetches_history_but_starts_evaluation_at_config_start(
        self,
    ) -> None:
        load_calls = []
        asof_times = []

        def fake_load_ohlcv(exchange, symbol, timeframe, start_utc, end_utc):
            load_calls.append((timeframe, start_utc, end_utc))
            return self.bars_main if timeframe == "4h" else self.bars_sub

        def fake_compute_macd(_bars):
            return []

        def fake_build_chan_state(*args, **kwargs):
            asof_times.append(kwargs["asof_time"])
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch("ai_trader.backtest.engine.load_ohlcv", side_effect=fake_load_ohlcv),
            patch(
                "ai_trader.backtest.engine.compute_macd", side_effect=fake_compute_macd
            ),
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                    start_utc="2022-02-10T00:00:00Z",
                    end_utc="2022-02-13T08:00:00Z",
                    history_prefetch_days=30,
                )
            )

        self.assertEqual(load_calls[0][1], "2022-01-11T00:00:00Z")
        self.assertEqual(load_calls[1][1], "2022-01-11T00:00:00Z")
        self.assertTrue(asof_times)
        self.assertTrue(report.signals)
        self.assertGreaterEqual(report.signals[0]["time"], "2022-02-10T00:00:00Z")
        self.assertEqual(report.metrics["trade_count"], 0.0)

    def test_structure_lookback_limits_chan_state_inputs(self) -> None:
        input_lengths = []
        asof_times = []

        def fake_build_chan_state(*args, **kwargs):
            input_lengths.append((len(kwargs["bars_main"]), len(kwargs["bars_sub"])))
            asof_times.append(kwargs["asof_time"])
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    structure_lookback_main_bars=25,
                    structure_lookback_sub_bars=80,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertTrue(input_lengths)
        self.assertLessEqual(max(item[0] for item in input_lengths), 25)
        self.assertLessEqual(max(item[1] for item in input_lengths), 80)
        self.assertEqual(asof_times[0], self.bars_main[120].time)
        self.assertEqual(report.metrics["trade_count"], 0.0)

    def test_strict_recursive_backtest_replays_one_minute_structure_asof(self) -> None:
        replay_calls = []
        structural_views = []
        input_lengths = []
        seed = object()

        class FakeReplay:
            def view_at(self, asof_time):
                replay_calls.append(asof_time)
                return ("structural-view", asof_time)

        def fake_build_chan_state(*args, **kwargs):
            structural_views.append(kwargs["structural_view"])
            input_lengths.append(
                (len(kwargs["bars_main"]), len(kwargs["macd_main"]))
            )
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_structural_seed",
                return_value=seed,
            ) as build_seed,
            patch(
                "ai_trader.backtest.engine.StructuralReplay",
                return_value=FakeReplay(),
            ) as replay_type,
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="strict_recursive",
                    allow_short_entries=False,
                    structure_lookback_main_bars=25,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
                bars_structure=self.bars_sub,
            )

        build_seed.assert_called_once()
        replay_type.assert_called_once()
        self.assertTrue(replay_calls)
        self.assertEqual(replay_calls[0], self.bars_main[120].time)
        self.assertEqual(
            structural_views[0],
            ("structural-view", self.bars_main[120].time),
        )
        self.assertEqual(input_lengths[0], (25, 121))
        self.assertEqual(report.metrics["trade_count"], 0.0)

    def test_strict_recursive_backtest_rejects_empty_structure_data(self) -> None:
        report = run_backtest(
            config=BacktestConfig(chan_mode="strict_recursive"),
            bars_main=self.bars_main,
            bars_sub=self.bars_sub,
            bars_structure=[],
        )

        self.assertFalse(report.pass_checks["data_ready"])
        self.assertIn("严格递归模式缺少 1m 数据", report.fail_reasons[0])

    def test_positive_funding_rate_is_cost_for_long_position(self) -> None:
        buy_time = self.bars_main[120].time
        sell_time = self.bars_main[122].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == buy_time:
                signal = Signal(
                    type="B2",
                    level="sub",
                    trigger="buy",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=buy_time,
                    available_time=buy_time,
                    invalid_price=0.0,
                )
                return _decision(buy_time, "buy", [signal])
            if snapshot.asof_time == sell_time:
                signal = Signal(
                    type="S2",
                    level="sub",
                    trigger="sell",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=sell_time,
                    available_time=sell_time,
                    invalid_price=1_000_000.0,
                )
                return _decision(sell_time, "sell", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            base = run_backtest(
                BacktestConfig(
                    exchange="binanceusdm",
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
                funding_rates=[],
            )
            funded = run_backtest(
                BacktestConfig(
                    exchange="binanceusdm",
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
                funding_rates=[
                    FundingRate(
                        time=self.bars_main[121].time,
                        rate=0.0001,
                        mark_price=self.bars_main[121].close,
                    )
                ],
            )

        self.assertEqual(len(base.trades), 1)
        self.assertEqual(len(funded.trades), 1)
        expected = (
            -funded.trades[0].quantity
            * self.bars_main[121].close
            * 0.0001
        )
        self.assertAlmostEqual(funded.trades[0].funding_pnl, expected)
        self.assertAlmostEqual(
            funded.trades[0].net_pnl,
            base.trades[0].net_pnl + expected,
        )

    def test_positive_funding_rate_is_income_for_short_position(self) -> None:
        sell_time = self.bars_main[120].time
        buy_time = self.bars_main[122].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == sell_time:
                signal = Signal(
                    type="S3",
                    level="main",
                    trigger="sell",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=sell_time,
                    available_time=sell_time,
                    invalid_price=1_000_000.0,
                )
                return _decision(sell_time, "sell", [signal])
            if snapshot.asof_time == buy_time:
                signal = Signal(
                    type="B1",
                    level="main",
                    trigger="buy",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=buy_time,
                    available_time=buy_time,
                    invalid_price=0.0,
                )
                return _decision(buy_time, "buy", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            funded = run_backtest(
                BacktestConfig(
                    exchange="binanceusdm",
                    chan_mode="pragmatic",
                    allow_short_entries=True,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
                funding_rates=[
                    FundingRate(
                        time=self.bars_main[121].time,
                        rate=0.0001,
                        mark_price=self.bars_main[121].close,
                    )
                ],
            )

        self.assertEqual(len(funded.trades), 1)
        self.assertEqual(funded.trades[0].side, "short")
        self.assertGreater(funded.trades[0].funding_pnl, 0.0)
        self.assertEqual(funded.significance.sample_size, 1)

    def test_last_bar_funding_is_included_in_open_position_equity(self) -> None:
        buy_time = self.bars_main[120].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == buy_time:
                signal = Signal(
                    type="B2",
                    level="sub",
                    trigger="buy",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=buy_time,
                    available_time=buy_time,
                    invalid_price=0.0,
                )
                return _decision(buy_time, "buy", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            base = run_backtest(
                BacktestConfig(
                    exchange="binanceusdm",
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
                funding_rates=[],
            )
            funded = run_backtest(
                BacktestConfig(
                    exchange="binanceusdm",
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
                funding_rates=[
                    FundingRate(
                        time=self.bars_main[-1].time,
                        rate=0.0001,
                        mark_price=self.bars_main[-1].close,
                    )
                ],
            )

        quantity = base.equity_curve[-1].position_value / self.bars_main[-1].close
        expected = -quantity * self.bars_main[-1].close * 0.0001
        self.assertAlmostEqual(
            funded.equity_curve[-1].equity,
            base.equity_curve[-1].equity + expected,
        )

    def test_optional_end_liquidation_closes_open_position(self) -> None:
        buy_time = self.bars_main[120].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == buy_time:
                signal = Signal(
                    type="B2",
                    level="sub",
                    trigger="buy",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=buy_time,
                    available_time=buy_time,
                    invalid_price=0.0,
                )
                return _decision(buy_time, "buy", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                BacktestConfig(
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                    liquidate_at_end=True,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].exit_reason, "end_of_test")
        self.assertEqual(report.equity_curve[-1].position_value, 0.0)

    def test_default_structure_lookback_is_bounded(self) -> None:
        bars_main = make_synthetic_bars(
            start=datetime(2022, 1, 1, tzinfo=timezone.utc),
            count=DEFAULT_STRUCTURE_LOOKBACK_MAIN_BARS + 80,
            step_hours=4,
        )
        bars_sub = make_synthetic_bars(
            start=datetime(2022, 1, 1, tzinfo=timezone.utc),
            count=DEFAULT_STRUCTURE_LOOKBACK_SUB_BARS + 320,
            step_hours=1,
        )
        input_lengths = []

        def fake_build_chan_state(*args, **kwargs):
            input_lengths.append((len(kwargs["bars_main"]), len(kwargs["bars_sub"])))
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(),
                bars_main=bars_main,
                bars_sub=bars_sub,
            )

        self.assertTrue(input_lengths)
        self.assertLessEqual(
            max(item[0] for item in input_lengths),
            DEFAULT_STRUCTURE_LOOKBACK_MAIN_BARS,
        )
        self.assertLessEqual(
            max(item[1] for item in input_lengths),
            DEFAULT_STRUCTURE_LOOKBACK_SUB_BARS,
        )
        self.assertEqual(report.metrics["trade_count"], 0.0)

    def test_blocked_buy_candidates_do_not_count_as_effective_samples(self) -> None:
        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
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
            return _decision(snapshot.asof_time, "wait", [signal])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(report.metrics["trade_count"], 0.0)
        self.assertFalse(report.pass_checks["sample_count_ge_80"])
        self.assertIn("有效B2/B3样本不足80", report.fail_reasons)

    def test_open_signal_still_counts_for_forward_return_evaluation(self) -> None:
        buy_time = self.bars_main[120].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time != buy_time:
                return _decision(snapshot.asof_time, "hold", [])
            signal = Signal(
                type="B3",
                level="main",
                trigger="b3",
                invalid_if="invalid",
                confidence=0.70,
                event_time=snapshot.asof_time,
                available_time=snapshot.asof_time,
                invalid_price=0.0,
            )
            return _decision(snapshot.asof_time, "buy", [signal])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(report.trades, [])
        self.assertEqual(report.significance.sample_size, 1)
        expected_return = (
            self.bars_main[123].close - self.bars_main[121].open
        ) / self.bars_main[121].open
        self.assertAlmostEqual(report.significance.observed_mean, expected_return)

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

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
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

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(chan_mode="strict_kline8"),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 2)
        self.assertEqual(report.trades[0].entry_time, self.bars_main[120].time)
        self.assertEqual(report.trades[0].exit_time, self.bars_main[122].time)
        self.assertEqual(report.trades[1].side, "short")

    def test_intrabar_break_of_invalid_price_exits_at_next_open(self) -> None:
        buy_time = self.bars_main[120].time
        invalid_price = self.bars_main[121].low + 1.0
        self.assertGreater(self.bars_main[121].close, invalid_price)

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
                    invalid_price=invalid_price,
                )
                return _decision(snapshot.asof_time, "buy", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].entry_time, self.bars_main[120].time)
        self.assertEqual(report.trades[0].exit_time, self.bars_main[121].time)
        self.assertLess(report.trades[0].max_adverse_excursion, 0.0)
        self.assertIsNotNone(report.trades[0].mae_time)
        self.assertEqual(
            report.trades[0].to_dict()["confirmation_lag_hours"],
            0.0,
        )

    def test_one_minute_execution_exits_at_first_stop_bar(self) -> None:
        buy_time = self.bars_main[120].time
        entry_open = self.bars_main[121].open
        invalid_price = entry_open - 10.0
        stop_time = buy_time + timedelta(minutes=5)
        execution_bar = Bar(
            time=stop_time,
            open=invalid_price + 5.0,
            high=invalid_price + 8.0,
            low=invalid_price - 1.0,
            close=invalid_price + 2.0,
        )

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
                    event_time=buy_time,
                    available_time=buy_time,
                    invalid_price=invalid_price,
                )
                return _decision(snapshot.asof_time, "buy", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="strict_kline8",
                    allow_short_entries=False,
                    execution_timeframe="1m",
                    invalidation_mode="intrabar",
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
                bars_execution=[execution_bar],
            )

        self.assertEqual(len(report.trades), 1)
        trade = report.trades[0]
        self.assertEqual(trade.exit_reason, "invalidated")
        self.assertEqual(trade.exit_time, stop_time)
        self.assertAlmostEqual(
            trade.exit_price,
            invalid_price * (1 - BacktestConfig().slippage_rate),
        )

    def test_close_confirmation_ignores_intrabar_invalid_price_break(
        self,
    ) -> None:
        buy_time = self.bars_main[120].time
        invalid_price = self.bars_main[121].low + 1.0
        self.assertGreater(self.bars_main[121].close, invalid_price)
        self.assertTrue(
            all(
                bar.close > invalid_price
                for bar in self.bars_main[121:]
            )
        )

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
                    invalid_price=invalid_price,
                )
                return _decision(snapshot.asof_time, "buy", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan",
                    allow_short_entries=False,
                    invalidation_mode="close",
                    liquidate_at_end=True,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].exit_reason, "end_of_test")
        self.assertEqual(report.trades[0].exit_time, self.bars_main[-1].time)

    def test_reversal_cooldown_skips_same_bar_opposite_entry(self) -> None:
        buy_time = self.bars_main[120].time
        sell_time = self.bars_main[121].time

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
                    invalid_price=0.0,
                )
                return _decision(snapshot.asof_time, "buy", [signal])
            if snapshot.asof_time == sell_time:
                signal = Signal(
                    type="S3",
                    level="main",
                    trigger="s3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=1_000_000.0,
                )
                return _decision(snapshot.asof_time, "sell", [signal])
            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            legacy = run_backtest(
                config=BacktestConfig(
                    chan_mode="pragmatic",
                    allow_short_entries=True,
                    liquidate_at_end=True,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )
            conservative = run_backtest(
                config=BacktestConfig(
                    chan_mode="pragmatic",
                    allow_short_entries=True,
                    reversal_cooldown_bars=1,
                    liquidate_at_end=True,
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual([trade.side for trade in legacy.trades], ["long", "short"])
        self.assertEqual([trade.side for trade in conservative.trades], ["long"])

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

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(chan_mode="strict_kline8"),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)

    def test_sell_can_close_long_without_opening_short_when_disabled(self) -> None:
        buy_time = self.bars_main[120].time
        sell_time = self.bars_main[121].time

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

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="strict_kline8", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].side, "long")
        expected_slippage = (
            report.trades[0].quantity
            * (self.bars_main[121].open + self.bars_main[122].open)
            * 0.0002
        )
        self.assertAlmostEqual(
            report.trades[0].slippage_cost,
            expected_slippage,
        )
        self.assertGreaterEqual(report.equity_curve[-1].cash, 0.0)

    def test_high_conflict_b2_buy_does_not_open_long_in_orthodox_mode(self) -> None:
        buy_time = self.bars_main[120].time
        sell_time = self.bars_main[121].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

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
                return SignalDecision(
                    exchange="binance",
                    symbol="BTC/USDT",
                    timeframe_main="4h",
                    timeframe_sub="1h",
                    data_quality=DataQuality(status="ok", notes=""),
                    market_state=MarketState(trend_type="down"),
                    signals=[signal],
                    action=Action(decision="buy", reason="buy"),
                    risk=Risk(conflict_level="high", notes=""),
                    cn_summary="buy",
                )

            if snapshot.asof_time == sell_time:
                signal = Signal(
                    type="S1",
                    level="main",
                    trigger="s1",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=120.0,
                )
                return SignalDecision(
                    exchange="binance",
                    symbol="BTC/USDT",
                    timeframe_main="4h",
                    timeframe_sub="1h",
                    data_quality=DataQuality(status="ok", notes=""),
                    market_state=MarketState(trend_type="up"),
                    signals=[signal],
                    action=Action(decision="sell", reason="sell"),
                    risk=Risk(conflict_level="low", notes=""),
                    cn_summary="sell",
                )

            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 0)
        self.assertEqual(report.metrics["trade_count"], 0.0)

    def test_high_conflict_b3_buy_does_not_open_long_in_orthodox_mode(self) -> None:
        buy_time = self.bars_main[120].time
        sell_time = self.bars_main[121].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"], center_start_index=8)

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == buy_time:
                signal = Signal(
                    type="B3",
                    level="main",
                    trigger="b3",
                    invalid_if="invalid",
                    confidence=0.68,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=100.0,
                    anchor_center_start_index=8,
                )
                return SignalDecision(
                    exchange="binance",
                    symbol="BTC/USDT",
                    timeframe_main="4h",
                    timeframe_sub="1h",
                    data_quality=DataQuality(status="ok", notes=""),
                    market_state=MarketState(trend_type="up"),
                    signals=[signal],
                    action=Action(decision="buy", reason="buy"),
                    risk=Risk(conflict_level="high", notes=""),
                    cn_summary="buy",
                )

            if snapshot.asof_time == sell_time:
                signal = Signal(
                    type="S3",
                    level="main",
                    trigger="s3",
                    invalid_if="invalid",
                    confidence=0.68,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=120.0,
                    anchor_center_start_index=9,
                )
                return SignalDecision(
                    exchange="binance",
                    symbol="BTC/USDT",
                    timeframe_main="4h",
                    timeframe_sub="1h",
                    data_quality=DataQuality(status="ok", notes=""),
                    market_state=MarketState(trend_type="down"),
                    signals=[signal],
                    action=Action(decision="sell", reason="sell"),
                    risk=Risk(conflict_level="low", notes=""),
                    cn_summary="sell",
                )

            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 0)
        self.assertEqual(report.metrics["trade_count"], 0.0)

    def test_fresh_reduce_signals_only_apply_once_per_position(self) -> None:
        buy_time = self.bars_main[120].time
        first_reduce_time = self.bars_main[121].time

        def fake_build_chan_state(*args, **kwargs):
            return self._snapshot(kwargs["asof_time"])

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

            if snapshot.asof_time >= first_reduce_time:
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
                return _decision(snapshot.asof_time, "reduce", [signal])

            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(chan_mode="strict_kline8"),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].entry_time, self.bars_main[120].time)
        self.assertEqual(report.trades[0].exit_time, self.bars_main[121].time)

    def test_same_main_center_only_allows_one_effective_b3_entry(self) -> None:
        first_buy_time = self.bars_main[120].time
        first_sell_time = self.bars_main[121].time
        second_buy_time = self.bars_main[122].time
        second_sell_time = self.bars_main[123].time

        def fake_build_chan_state(*args, **kwargs):
            asof_time = kwargs["asof_time"]
            if asof_time in {
                first_buy_time,
                first_sell_time,
                second_buy_time,
                second_sell_time,
            }:
                return self._snapshot(asof_time, center_start_index=7)
            return self._snapshot(asof_time)

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time in {first_buy_time, second_buy_time}:
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

            if snapshot.asof_time in {first_sell_time, second_sell_time}:
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

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].entry_time, self.bars_main[120].time)
        self.assertEqual(report.trades[0].exit_time, self.bars_main[121].time)

    def test_different_main_centers_can_each_trigger_one_effective_b3_entry(
        self,
    ) -> None:
        first_buy_time = self.bars_main[120].time
        first_sell_time = self.bars_main[121].time
        second_buy_time = self.bars_main[122].time
        second_sell_time = self.bars_main[123].time

        def fake_build_chan_state(*args, **kwargs):
            asof_time = kwargs["asof_time"]
            if asof_time in {first_buy_time, first_sell_time}:
                return self._snapshot(asof_time, center_start_index=7)
            if asof_time in {second_buy_time, second_sell_time}:
                return self._snapshot(asof_time, center_start_index=9)
            return self._snapshot(asof_time)

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time in {first_buy_time, second_buy_time}:
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

            if snapshot.asof_time in {first_sell_time, second_sell_time}:
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

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 2)
        self.assertEqual(report.trades[0].entry_time, self.bars_main[120].time)
        self.assertEqual(report.trades[1].entry_time, self.bars_main[122].time)

    def test_b3_execution_uses_signal_anchor_center_not_snapshot_last_center(
        self,
    ) -> None:
        first_buy_time = self.bars_main[120].time
        first_sell_time = self.bars_main[121].time
        second_buy_time = self.bars_main[122].time
        second_sell_time = self.bars_main[123].time

        def fake_build_chan_state(*args, **kwargs):
            asof_time = kwargs["asof_time"]
            if asof_time in {
                first_buy_time,
                first_sell_time,
                second_buy_time,
                second_sell_time,
            }:
                return self._snapshot(asof_time, center_start_index=7)
            return self._snapshot(asof_time)

        def fake_generate_signal(snapshot, **kwargs):
            if snapshot.asof_time == first_buy_time:
                signal = Signal(
                    type="B3",
                    level="main",
                    trigger="b3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=100.0,
                    anchor_center_start_index=7,
                )
                return _decision(snapshot.asof_time, "buy", [signal])

            if snapshot.asof_time == second_buy_time:
                signal = Signal(
                    type="B3",
                    level="main",
                    trigger="b3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=100.0,
                    anchor_center_start_index=9,
                )
                return _decision(snapshot.asof_time, "buy", [signal])

            if snapshot.asof_time == first_sell_time:
                signal = Signal(
                    type="S3",
                    level="main",
                    trigger="s3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=120.0,
                    anchor_center_start_index=7,
                )
                return _decision(snapshot.asof_time, "sell", [signal])

            if snapshot.asof_time == second_sell_time:
                signal = Signal(
                    type="S3",
                    level="main",
                    trigger="s3",
                    invalid_if="invalid",
                    confidence=0.70,
                    event_time=snapshot.asof_time,
                    available_time=snapshot.asof_time,
                    invalid_price=120.0,
                    anchor_center_start_index=9,
                )
                return _decision(snapshot.asof_time, "sell", [signal])

            return _decision(snapshot.asof_time, "hold", [])

        with (
            patch(
                "ai_trader.backtest.engine.build_chan_state",
                side_effect=fake_build_chan_state,
            ),
            patch(
                "ai_trader.backtest.engine.generate_signal",
                side_effect=fake_generate_signal,
            ),
        ):
            report = run_backtest(
                config=BacktestConfig(
                    chan_mode="orthodox_chan", allow_short_entries=False
                ),
                bars_main=self.bars_main,
                bars_sub=self.bars_sub,
            )

        self.assertEqual(len(report.trades), 2)
        self.assertEqual(report.trades[0].entry_time, self.bars_main[120].time)
        self.assertEqual(report.trades[1].entry_time, self.bars_main[122].time)


if __name__ == "__main__":
    unittest.main()
