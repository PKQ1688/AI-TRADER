from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ai_trader.chan.core.center import build_zhongshus, build_zhongshus_from_bis
from ai_trader.chan.core.buy_sell_points import generate_signals
from ai_trader.chan.core.divergence import (
    DivergenceCandidate,
    detect_divergence_candidates,
)
from ai_trader.chan.core.fractal import detect_fractals
from ai_trader.chan.core.include import merge_inclusions
from ai_trader.chan.core.segment import build_segments
from ai_trader.chan.core.stroke import build_bis
from ai_trader.chan.engine import _fresh_signals, generate_signal
from ai_trader.types import (
    Bar,
    Bi,
    ChanSnapshot,
    DataQuality,
    MACDPoint,
    MarketState,
    Segment,
    Signal,
    Zhongshu,
)
from tests.test_utils import make_synthetic_bars


class ChanCoreRulesTest(unittest.TestCase):
    def _t(self, i: int) -> datetime:
        return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * i)

    def _mk_bi(self, idx: int, direction: str, start: float, end: float) -> Bi:
        return Bi(
            direction=direction,  # type: ignore[arg-type]
            start_index=idx,
            end_index=idx + 1,
            start_price=start,
            end_price=end,
            event_time=self._t(idx + 1),
            available_time=self._t(idx + 1),
        )

    def _mk_segment(self, idx: int, direction: str, high: float, low: float) -> Segment:
        return Segment(
            direction=direction,  # type: ignore[arg-type]
            start_index=idx,
            end_index=idx + 2,
            high=high,
            low=low,
            event_time=self._t(idx + 2),
            available_time=self._t(idx + 2),
        )

    def _mk_divergence(
        self, signal_type: str, idx: int, invalid_price: float
    ) -> DivergenceCandidate:
        return DivergenceCandidate(
            signal_type=signal_type,
            mode="trend",
            confidence=0.60,
            trigger=f"{signal_type} trigger",
            invalid_if=f"{signal_type} invalid",
            invalid_price=invalid_price,
            event_time=self._t(idx),
            available_time=self._t(idx),
        )

    def test_merge_inclusions_strict_sequence(self) -> None:
        bars = [
            Bar(time=self._t(0), open=8, high=10, low=5, close=9),
            Bar(time=self._t(1), open=9, high=12, low=6, close=11),
            Bar(time=self._t(2), open=11, high=11, low=7, close=8),
            Bar(time=self._t(3), open=8, high=13, low=8, close=12),
            Bar(time=self._t(4), open=12, high=12, low=9, close=10),
        ]

        merged = merge_inclusions(bars)
        self.assertEqual(len(merged), 3)
        self.assertEqual((merged[1].high, merged[1].low), (12, 7))
        self.assertEqual((merged[2].high, merged[2].low), (13, 9))

    def test_bi_rules_alternating_and_min_length(self) -> None:
        bars = make_synthetic_bars(start=self._t(0), count=220, step_hours=4)
        merged = merge_inclusions(bars)
        fractals = detect_fractals(merged)
        bis = build_bis(fractals, merged, min_bars=5)

        self.assertGreater(len(bis), 5)
        for i in range(1, len(bis)):
            self.assertNotEqual(bis[i - 1].direction, bis[i].direction)
        for bi in bis:
            self.assertGreaterEqual(bi.end_index - bi.start_index + 1, 5)

    def test_segment_case2_requires_reverse_confirmation(self) -> None:
        # 该序列在向上线段特征序列里产生“有缺口分型”，后续给出反向确认
        bis_with_confirm = [
            self._mk_bi(0, "up", 100, 110),
            self._mk_bi(1, "down", 108, 102),
            self._mk_bi(2, "up", 103, 115),
            self._mk_bi(3, "down", 120, 114),
            self._mk_bi(4, "up", 116, 130),
            self._mk_bi(5, "down", 118, 110),
            self._mk_bi(6, "up", 105, 125),
            self._mk_bi(7, "down", 119, 101),
            self._mk_bi(8, "up", 96, 122),
            self._mk_bi(9, "down", 117, 95),
            self._mk_bi(10, "up", 99, 124),
        ]

        bis_no_confirm = bis_with_confirm[:6]

        seg_no = build_segments(bis_no_confirm, require_case2_confirmation=True)
        self.assertTrue(seg_no)
        self.assertEqual(seg_no[0].status, "provisional")

        seg_yes = build_segments(bis_with_confirm, require_case2_confirmation=True)
        self.assertTrue(seg_yes)
        self.assertEqual(seg_yes[0].status, "confirmed")
        self.assertEqual(seg_yes[0].end_index, bis_with_confirm[3].end_index)

    def test_b2_requires_first_pullback_after_b1_to_hold(self) -> None:
        signals = generate_signals(
            divergence_candidates=[self._mk_divergence("B1", 2, 95.0)],
            bis_sub=[
                self._mk_bi(3, "up", 96, 103),
                self._mk_bi(4, "down", 103, 94),
                self._mk_bi(5, "up", 94, 101),
                self._mk_bi(6, "down", 101, 97),
                self._mk_bi(7, "up", 97, 105),
            ],
            segments_sub=[],
            zhongshu_main=None,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertNotIn("B2", {item.type for item in signals})

    def test_b3_requires_sub_level_departure_and_first_pullback(self) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=2,
            event_time=self._t(2),
            available_time=self._t(2),
        )

        signals = generate_signals(
            divergence_candidates=[],
            bis_sub=[],
            segments_sub=[
                self._mk_segment(1, "down", 103.0, 99.0),
                self._mk_segment(2, "up", 108.0, 100.0),
                self._mk_segment(3, "down", 107.0, 103.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertIn("B3", {item.type for item in signals})

    def test_b3_can_use_recent_center_overlap_even_if_center_available_later(
        self,
    ) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=2,
            event_time=self._t(2),
            available_time=self._t(20),
        )

        signals = generate_signals(
            divergence_candidates=[],
            bis_sub=[],
            segments_sub=[
                self._mk_segment(2, "down", 103.0, 99.0),
                self._mk_segment(3, "up", 108.0, 100.0),
                self._mk_segment(4, "down", 107.0, 103.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertIn("B3", {item.type for item in signals})

    def test_b3_not_emitted_without_first_pullback_confirmation(self) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=2,
            event_time=self._t(2),
            available_time=self._t(2),
        )

        signals = generate_signals(
            divergence_candidates=[],
            bis_sub=[],
            segments_sub=[self._mk_segment(2, "up", 108.0, 100.0)],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertNotIn("B3", {item.type for item in signals})

    def test_generate_signal_waits_for_center_confirmation_before_b3_is_fresh(
        self,
    ) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=2,
            event_time=self._t(2),
            available_time=self._t(20),
        )
        market_state = MarketState(
            trend_type="up",
            walk_type="trend",
            phase="trending",
            zhongshu_count=1,
            last_zhongshu={"zd": 98.0, "zg": 102.0, "gg": 110.0, "dd": 92.0},
            current_stroke_dir="up",
            current_segment_dir="up",
        )

        snapshot = ChanSnapshot(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            asof_time=self._t(20),
            bars_main=[
                Bar(time=self._t(19), open=100, high=102, low=99, close=101),
                Bar(time=self._t(20), open=101, high=103, low=100, close=102),
            ],
            bars_sub=[],
            macd_main=[MACDPoint(time=self._t(19), dif=0.0, dea=0.0, hist=1.0)],
            macd_sub=[],
            fractals_main=[],
            fractals_sub=[],
            bis_main=[],
            bis_sub=[],
            segments_main=[],
            segments_sub=[
                self._mk_segment(2, "down", 103.0, 99.0),
                self._mk_segment(3, "up", 108.0, 100.0),
                self._mk_segment(4, "down", 107.0, 103.0),
            ],
            previous_main_bar_time=self._t(19),
            zhongshus_main=[zs],
            zhongshus_sub=[],
            last_zhongshu_main=zs,
            trend_type_main="up",
            market_state_main=market_state,
            data_quality=DataQuality(status="ok", notes=""),
        )

        decision = generate_signal(snapshot)
        self.assertIn("B3", {item.type for item in decision.signals})
        self.assertEqual(decision.signals[0].available_time, self._t(20))
        self.assertEqual(decision.action.decision, "buy")

    def test_fresh_signals_use_previous_raw_main_bar_time(self) -> None:
        signal = Signal(
            type="B3",
            level="main",
            trigger="b3",
            invalid_if="invalid",
            confidence=0.70,
            event_time=self._t(2),
            available_time=self._t(2),
        )
        snapshot = SimpleNamespace(
            bars_main=[
                Bar(time=self._t(0), open=100, high=101, low=99, close=100),
                Bar(time=self._t(4), open=100, high=102, low=98, close=101),
            ],
            asof_time=self._t(4),
            previous_main_bar_time=self._t(3),
        )

        self.assertFalse(_fresh_signals(snapshot, [signal]))

    def test_trend_divergence_ignores_history_before_immediate_a_leg(self) -> None:
        bis = [
            self._mk_bi(0, "down", 120, 110),
            self._mk_bi(1, "up", 110, 115),
            self._mk_bi(2, "down", 115, 105),
            self._mk_bi(3, "up", 105, 109),
            self._mk_bi(4, "down", 109, 100),
            self._mk_bi(5, "up", 100, 106),
            self._mk_bi(6, "down", 106, 101),
            self._mk_bi(7, "up", 101, 104),
            self._mk_bi(8, "down", 104, 90),
            self._mk_bi(9, "up", 90, 96),
            self._mk_bi(10, "down", 96, 89),
            self._mk_bi(11, "up", 89, 92),
            self._mk_bi(12, "down", 92, 80),
        ]
        zhongshus = [
            Zhongshu(
                zd=100.0,
                zg=105.0,
                gg=106.0,
                dd=99.0,
                g=105.0,
                d=100.0,
                start_index=4,
                end_index=7,
                event_time=self._t(7),
                available_time=self._t(7),
            ),
            Zhongshu(
                zd=85.0,
                zg=90.0,
                gg=96.0,
                dd=84.0,
                g=90.0,
                d=85.0,
                start_index=8,
                end_index=11,
                event_time=self._t(11),
                available_time=self._t(11),
            ),
        ]
        macd = [
            MACDPoint(time=self._t(1), dif=-5.0, dea=-4.0, hist=-100.0),
            MACDPoint(time=self._t(3), dif=-2.0, dea=-1.0, hist=-10.0),
            MACDPoint(time=self._t(11), dif=0.0, dea=0.0, hist=0.0),
            MACDPoint(time=self._t(13), dif=-2.0, dea=-1.0, hist=-10.0),
        ]

        divergence = detect_divergence_candidates(
            bis=bis,
            zhongshu_count=2,
            trend_type="down",
            macd=macd,
            threshold=0.10,
            zhongshus=zhongshus,
        )

        self.assertEqual(divergence, [])

    def test_bi_zhongshu_extension_shrinks_overlap_range(self) -> None:
        zhongshus = build_zhongshus_from_bis(
            [
                self._mk_bi(0, "up", 10, 20),
                self._mk_bi(1, "down", 22, 14),
                self._mk_bi(2, "up", 15, 24),
                self._mk_bi(3, "down", 18, 16),
            ]
        )

        self.assertEqual(len(zhongshus), 1)
        self.assertEqual((zhongshus[0].zd, zhongshus[0].zg), (16, 18))

    def test_zhongshu_evolution_extension_and_expansion(self) -> None:
        segments = [
            self._mk_segment(0, "up", 110, 100),
            self._mk_segment(1, "down", 108, 98),
            self._mk_segment(2, "up", 109, 99),
            self._mk_segment(3, "down", 112, 101),
            self._mk_segment(4, "up", 111, 102),
            self._mk_segment(5, "down", 113, 103),
            self._mk_segment(6, "up", 116, 109),
        ]

        zhongshus = build_zhongshus(segments)
        # After extension the first center covers segs 0-5.  The last
        # candidate (segs 4-6) does not overlap the center interval but
        # its wave range touches, so it triggers level expansion which
        # *merges* the two centers into one larger center.
        self.assertEqual(len(zhongshus), 1)
        self.assertEqual(zhongshus[0].evolution, "expansion")

    def test_conflict_high_forces_wait(self) -> None:
        t0 = self._t(0)
        bars_main = [
            Bar(time=t0, open=120, high=121, low=118, close=119),
            Bar(time=self._t(1), open=119, high=120, low=94, close=96),
        ]
        bars_sub = [
            Bar(time=t0, open=102, high=104, low=100, close=101),
            Bar(time=self._t(1), open=101, high=103, low=99, close=102),
        ]

        bis_main = [
            Bi(
                direction="down",
                start_index=0,
                end_index=1,
                start_price=120,
                end_price=100,
                event_time=self._t(1),
                available_time=self._t(1),
            ),
            Bi(
                direction="up",
                start_index=1,
                end_index=2,
                start_price=100,
                end_price=112,
                event_time=self._t(2),
                available_time=self._t(2),
            ),
            Bi(
                direction="down",
                start_index=2,
                end_index=3,
                start_price=112,
                end_price=95,
                event_time=self._t(3),
                available_time=self._t(3),
            ),
        ]
        bis_sub = [
            Bi(
                direction="down",
                start_index=0,
                end_index=1,
                start_price=105,
                end_price=99,
                event_time=self._t(1),
                available_time=self._t(1),
            ),
            Bi(
                direction="up",
                start_index=1,
                end_index=2,
                start_price=99,
                end_price=103,
                event_time=self._t(2),
                available_time=self._t(2),
            ),
        ]

        market_state = MarketState(
            trend_type="down",
            walk_type="trend",
            phase="trending",
            zhongshu_count=2,
            last_zhongshu={"zd": 97.0, "zg": 102.0, "gg": 105.0, "dd": 92.0},
            current_stroke_dir="down",
            current_segment_dir="down",
        )

        snapshot = ChanSnapshot(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            asof_time=self._t(3),
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=[
                MACDPoint(time=self._t(1), dif=0.0, dea=0.0, hist=5.0),
                MACDPoint(time=self._t(3), dif=0.0, dea=0.0, hist=1.0),
            ],
            macd_sub=[MACDPoint(time=self._t(1), dif=0.0, dea=0.0, hist=1.0)],
            fractals_main=[],
            fractals_sub=[],
            bis_main=bis_main,
            bis_sub=bis_sub,
            segments_main=[],
            segments_sub=[],
            zhongshus_main=[
                Zhongshu(
                    zd=97.0,
                    zg=102.0,
                    gg=105.0,
                    dd=92.0,
                    g=102.0,
                    d=97.0,
                    start_index=0,
                    end_index=3,
                    event_time=self._t(3),
                    available_time=self._t(3),
                )
            ],
            zhongshus_sub=[],
            last_zhongshu_main=Zhongshu(
                zd=97.0,
                zg=102.0,
                gg=105.0,
                dd=92.0,
                g=102.0,
                d=97.0,
                start_index=0,
                end_index=3,
                event_time=self._t(3),
                available_time=self._t(3),
            ),
            trend_type_main="down",
            market_state_main=market_state,
            data_quality=DataQuality(status="ok", notes=""),
        )

        decision = generate_signal(snapshot)
        self.assertEqual(decision.risk.conflict_level, "high")
        self.assertEqual(decision.action.decision, "wait")

    def test_conflict_high_with_sell_signal_forces_reduce(self) -> None:
        bars_main = [
            Bar(time=self._t(0), open=108, high=110, low=102, close=104),
            Bar(time=self._t(1), open=104, high=105, low=95, close=96),
        ]
        bars_sub = [
            Bar(time=self._t(0), open=101, high=103, low=99, close=100),
            Bar(time=self._t(1), open=100, high=104, low=99, close=103),
        ]

        bis_main = [
            Bi(
                direction="up",
                start_index=0,
                end_index=1,
                start_price=92,
                end_price=97,
                event_time=self._t(1),
                available_time=self._t(1),
            ),
            Bi(
                direction="down",
                start_index=1,
                end_index=2,
                start_price=97,
                end_price=96,
                event_time=self._t(2),
                available_time=self._t(2),
            ),
        ]
        bis_sub = [
            Bi(
                direction="down",
                start_index=0,
                end_index=1,
                start_price=103,
                end_price=99,
                event_time=self._t(1),
                available_time=self._t(1),
            ),
            Bi(
                direction="up",
                start_index=1,
                end_index=2,
                start_price=99,
                end_price=103,
                event_time=self._t(2),
                available_time=self._t(2),
            ),
        ]

        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=2,
            event_time=self._t(2),
            available_time=self._t(2),
        )
        market_state = MarketState(
            trend_type="down",
            walk_type="trend",
            phase="trending",
            zhongshu_count=2,
            last_zhongshu={"zd": 98.0, "zg": 102.0, "gg": 110.0, "dd": 92.0},
            current_stroke_dir="down",
            current_segment_dir="down",
        )

        snapshot = ChanSnapshot(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            asof_time=self._t(5),
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=[MACDPoint(time=self._t(1), dif=0.0, dea=0.0, hist=1.0)],
            macd_sub=[MACDPoint(time=self._t(1), dif=0.0, dea=0.0, hist=1.0)],
            fractals_main=[],
            fractals_sub=[],
            bis_main=bis_main,
            bis_sub=bis_sub,
            segments_main=[],
            segments_sub=[
                self._mk_segment(1, "up", 103.0, 99.0),
                self._mk_segment(2, "down", 101.0, 96.0),
                self._mk_segment(3, "up", 97.0, 93.0),
            ],
            zhongshus_main=[zs],
            zhongshus_sub=[],
            last_zhongshu_main=zs,
            trend_type_main="down",
            market_state_main=market_state,
            data_quality=DataQuality(status="ok", notes=""),
        )

        decision = generate_signal(snapshot)
        signal_types = {item.type for item in decision.signals}
        self.assertIn("S3", signal_types)
        self.assertEqual(decision.risk.conflict_level, "high")
        self.assertEqual(decision.action.decision, "reduce")


if __name__ == "__main__":
    unittest.main()
