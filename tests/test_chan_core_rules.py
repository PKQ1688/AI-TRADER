from __future__ import annotations

import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ai_trader.chan.core.center import build_zhongshus, build_zhongshus_from_bis
from ai_trader.chan.core.buy_sell_points import decide_action, generate_signals
from ai_trader.chan.config import get_chan_config
from ai_trader.chan.core.divergence import (
    DivergenceCandidate,
    detect_divergence_candidates,
)
from ai_trader.chan.core.fractal import detect_fractals
from ai_trader.chan.core.include import merge_inclusions
from ai_trader.chan.core.segment import build_segments
from ai_trader.chan.core.stroke import build_bis
from ai_trader.chan.core.trend_phase import infer_market_state
from ai_trader.chan.core.trend_phase import infer_trend_type
from ai_trader.chan.engine import _fresh_signals, _sub_interval_confirmed, build_chan_state, generate_signal, suppress_seen_signal_events
from ai_trader.types import (
    Action,
    Bar,
    Bi,
    ChanSnapshot,
    DataQuality,
    Fractal,
    MACDPoint,
    MarketState,
    Risk,
    Segment,
    Signal,
    SignalDecision,
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
        self,
        signal_type: str | None,
        idx: int,
        invalid_price: float,
        mode: str = "trend",
        anchor_center_start_index: int | None = None,
        anchor_center_end_index: int | None = None,
        anchor_center_available_time=None,
    ) -> DivergenceCandidate:
        return DivergenceCandidate(
            signal_type=signal_type,
            mode=mode,
            confidence=0.60,
            trigger=f"{signal_type} trigger",
            invalid_if=f"{signal_type} invalid",
            invalid_price=invalid_price,
            event_time=self._t(idx),
            available_time=self._t(idx),
            anchor_center_start_index=anchor_center_start_index,
            anchor_center_end_index=anchor_center_end_index,
            anchor_center_available_time=anchor_center_available_time,
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

    def test_segment_requires_initial_three_bi_overlap(self) -> None:
        bis = [
            self._mk_bi(0, "up", 100, 110),
            self._mk_bi(1, "down", 125, 118),
            self._mk_bi(2, "up", 130, 142),
        ]

        self.assertEqual(build_segments(bis, require_case2_confirmation=True), [])

    def test_confirmed_segments_share_boundary_and_alternate_direction(self) -> None:
        bis = [
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
            self._mk_bi(11, "down", 123, 111),
            self._mk_bi(12, "up", 112, 128),
            self._mk_bi(13, "down", 135, 121),
            self._mk_bi(14, "up", 122, 138),
            self._mk_bi(15, "down", 134, 118),
            self._mk_bi(16, "up", 120, 142),
        ]

        segments = build_segments(bis, require_case2_confirmation=False)

        self.assertGreaterEqual(len(segments), 2)
        for i in range(1, len(segments)):
            self.assertNotEqual(segments[i - 1].direction, segments[i].direction)
            self.assertEqual(segments[i - 1].end_index - 1, segments[i].start_index)

    def test_segment_keeps_opposite_provisional_when_boundary_lacks_overlap(self) -> None:
        bis = [
            Bi(
                direction="up",
                start_index=114,
                end_index=119,
                start_price=104106.09,
                end_price=105260.36,
                event_time=self._t(12),
                available_time=self._t(12),
            ),
            Bi(
                direction="down",
                start_index=119,
                end_index=128,
                start_price=105260.36,
                end_price=100272.68,
                event_time=self._t(13),
                available_time=self._t(13),
            ),
            Bi(
                direction="up",
                start_index=128,
                end_index=134,
                start_price=100272.68,
                end_price=106457.44,
                event_time=self._t(14),
                available_time=self._t(14),
            ),
            Bi(
                direction="down",
                start_index=134,
                end_index=141,
                start_price=106457.44,
                end_price=101560.0,
                event_time=self._t(15),
                available_time=self._t(15),
            ),
            Bi(
                direction="up",
                start_index=154,
                end_index=162,
                start_price=91231.0,
                end_price=100777.93,
                event_time=self._t(16),
                available_time=self._t(16),
            ),
            Bi(
                direction="down",
                start_index=162,
                end_index=166,
                start_price=100777.93,
                end_price=96155.0,
                event_time=self._t(17),
                available_time=self._t(17),
            ),
            Bi(
                direction="up",
                start_index=175,
                end_index=179,
                start_price=95620.34,
                end_price=97323.09,
                event_time=self._t(18),
                available_time=self._t(18),
            ),
        ]

        segments = build_segments(bis, require_case2_confirmation=True)

        self.assertEqual(
            [
                (item.direction, item.start_index, item.end_index, item.status)
                for item in segments
            ],
            [
                ("up", 114, 141, "confirmed"),
                ("down", 134, 179, "provisional"),
            ],
        )

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

    def test_b2_emits_on_first_pullback_holding_b1_low_and_reconfirming(self) -> None:
        signals = generate_signals(
            divergence_candidates=[
                self._mk_divergence(
                    "B1",
                    2,
                    95.0,
                    anchor_center_start_index=0,
                    anchor_center_end_index=2,
                    anchor_center_available_time=self._t(2),
                )
            ],
            bis_sub=[
                self._mk_bi(3, "up", 96, 103),
                self._mk_bi(4, "down", 103, 97),
                self._mk_bi(5, "up", 97, 105),
            ],
            segments_sub=[],
            zhongshu_main=None,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        b2 = next(item for item in signals if item.type == "B2")
        self.assertEqual(b2.event_time, self._t(6))
        self.assertEqual(b2.available_time, self._t(6))
        self.assertEqual(b2.invalid_price, 95.0)
        self.assertEqual(b2.anchor_center_start_index, 0)
        self.assertEqual(b2.anchor_center_end_index, 2)
        self.assertEqual(b2.anchor_center_available_time, self._t(2))

    def test_b2_not_emitted_after_sub_level_new_center_confirms(self) -> None:
        late_center = Zhongshu(
            zd=96.0,
            zg=101.0,
            gg=105.0,
            dd=92.0,
            g=101.0,
            d=96.0,
            start_index=4,
            end_index=6,
            event_time=self._t(6),
            available_time=self._t(6),
            origin_available_time=self._t(6),
            evolution="newborn",
        )

        signals = generate_signals(
            divergence_candidates=[self._mk_divergence("B1", 2, 95.0)],
            bis_sub=[
                self._mk_bi(3, "up", 96, 103),
                self._mk_bi(4, "down", 103, 97),
                self._mk_bi(5, "up", 97, 105),
            ],
            segments_sub=[],
            zhongshus_sub=[late_center],
            zhongshu_main=None,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertNotIn("B2", {item.type for item in signals})

    def test_b2_keeps_window_open_during_same_center_extension(self) -> None:
        extension_center = Zhongshu(
            zd=96.0,
            zg=101.0,
            gg=105.0,
            dd=92.0,
            g=101.0,
            d=96.0,
            start_index=1,
            end_index=6,
            event_time=self._t(6),
            available_time=self._t(6),
            origin_available_time=self._t(1),
            evolution="extension",
        )

        signals = generate_signals(
            divergence_candidates=[self._mk_divergence("B1", 2, 95.0)],
            bis_sub=[
                self._mk_bi(3, "up", 96, 103),
                self._mk_bi(4, "down", 103, 97),
                self._mk_bi(5, "up", 97, 105),
            ],
            segments_sub=[],
            zhongshus_sub=[extension_center],
            zhongshu_main=None,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertIn("B2", {item.type for item in signals})

    def test_s2_not_emitted_after_sub_level_new_center_confirms(self) -> None:
        late_center = Zhongshu(
            zd=99.0,
            zg=104.0,
            gg=108.0,
            dd=95.0,
            g=104.0,
            d=99.0,
            start_index=4,
            end_index=6,
            event_time=self._t(6),
            available_time=self._t(6),
            origin_available_time=self._t(6),
            evolution="newborn",
        )

        signals = generate_signals(
            divergence_candidates=[self._mk_divergence("S1", 2, 105.0)],
            bis_sub=[
                self._mk_bi(3, "down", 104, 97),
                self._mk_bi(4, "up", 97, 102),
                self._mk_bi(5, "down", 102, 95),
            ],
            segments_sub=[],
            zhongshus_sub=[late_center],
            zhongshu_main=None,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertNotIn("S2", {item.type for item in signals})

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
                self._mk_segment(4, "up", 112.0, 104.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertIn("B3", {item.type for item in signals})

    def test_b3_uses_bi_context_when_present(self) -> None:
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
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(1, "down", 103.0, 99.0),
                self._mk_bi(2, "up", 100.0, 108.0),
                self._mk_bi(3, "down", 107.0, 103.0),
                self._mk_bi(4, "up", 104.0, 112.0),
            ],
        )

        b3 = next(item for item in signals if item.type == "B3")
        self.assertEqual(b3.event_time, self._t(4))
        self.assertEqual(b3.available_time, self._t(5))

    def test_b3_bi_context_requires_departure_after_center_end(self) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=4,
            event_time=self._t(2),
            available_time=self._t(2),
        )

        signals = generate_signals(
            divergence_candidates=[],
            bis_sub=[],
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(3, "up", 100.0, 108.0),
                self._mk_bi(4, "down", 107.0, 103.0),
                self._mk_bi(5, "up", 104.0, 112.0),
            ],
        )

        self.assertNotIn("B3", {item.type for item in signals})

    def test_b3_bi_context_rejects_pullback_returning_into_center(self) -> None:
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
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(1, "down", 103.0, 99.0),
                self._mk_bi(2, "up", 100.0, 108.0),
                self._mk_bi(3, "down", 107.0, 101.0),
            ],
        )

        self.assertNotIn("B3", {item.type for item in signals})

    def test_b3_bi_context_requires_confirmation_to_stay_above_center(self) -> None:
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
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(3, "up", 100.0, 108.0),
                self._mk_bi(4, "down", 107.0, 103.0),
                self._mk_bi(5, "up", 101.0, 109.0),
            ],
        )

        self.assertNotIn("B3", {item.type for item in signals})

    def test_b3_segment_requires_confirmation_to_stay_above_center(self) -> None:
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
                self._mk_segment(4, "up", 109.0, 101.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertNotIn("B3", {item.type for item in signals})

    def test_b3_not_emitted_if_center_available_later_than_pullback(
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

        self.assertNotIn("B3", {item.type for item in signals})

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

    def test_b3_uses_first_pullback_not_later_one(self) -> None:
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
                self._mk_segment(4, "up", 112.0, 104.0),
                self._mk_segment(5, "down", 111.0, 105.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        b3 = next(item for item in signals if item.type == "B3")
        self.assertEqual(b3.event_time, self._t(5))
        self.assertEqual(b3.available_time, self._t(6))
        self.assertEqual(b3.anchor_center_start_index, 0)
        self.assertEqual(b3.anchor_center_end_index, 2)
        self.assertEqual(b3.anchor_center_available_time, self._t(2))

    def test_b3_not_emitted_when_first_pullback_returns_to_center(self) -> None:
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
                self._mk_segment(3, "down", 107.0, 101.0),
                self._mk_segment(4, "up", 112.0, 104.0),
                self._mk_segment(5, "down", 111.0, 105.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="up"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertNotIn("B3", {item.type for item in signals})

    def test_generate_signal_does_not_emit_b3_if_center_confirms_after_pullback(
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
        self.assertNotIn("B3", {item.type for item in decision.signals})
        self.assertEqual(decision.action.decision, "hold")

    def test_b3_not_emitted_if_pullback_precedes_origin_center_confirmation(
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
            origin_available_time=self._t(6),
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

        self.assertNotIn("B3", {item.type for item in signals})

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

    def test_suppress_seen_signal_events_emits_structural_event_only_once(self) -> None:
        signal = Signal(
            type="S3",
            level="main",
            trigger="s3",
            invalid_if="invalid",
            confidence=0.68,
            event_time=self._t(5),
            available_time=self._t(6),
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        decision = SignalDecision(
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

        seen_signal_keys: set[tuple] = set()
        first = suppress_seen_signal_events(
            decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
        )
        self.assertEqual(first.action.decision, "sell")
        self.assertEqual(len(first.signals), 1)
        self.assertEqual(len(seen_signal_keys), 1)

        second = suppress_seen_signal_events(
            decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
        )
        self.assertFalse(second.signals)
        self.assertEqual(second.action.decision, "hold")

    def test_suppress_seen_signal_events_dedupes_b3s3_by_center(self) -> None:
        first_signal = Signal(
            type="S3",
            level="main",
            trigger="s3",
            invalid_if="invalid",
            confidence=0.68,
            event_time=self._t(5),
            available_time=self._t(6),
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        second_signal = Signal(
            type="S3",
            level="main",
            trigger="s3",
            invalid_if="invalid",
            confidence=0.68,
            event_time=self._t(6),
            available_time=self._t(7),
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        first_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[first_signal],
            action=Action(decision="sell", reason="sell"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="sell",
        )
        second_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[second_signal],
            action=Action(decision="sell", reason="sell"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="sell",
        )

        seen_signal_keys: set[tuple] = set()
        first = suppress_seen_signal_events(
            first_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
        )
        self.assertEqual(first.action.decision, "sell")
        self.assertEqual(len(first.signals), 1)

        second = suppress_seen_signal_events(
            second_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
        )
        self.assertFalse(second.signals)
        self.assertEqual(second.action.decision, "hold")

    def test_suppress_seen_signal_events_blocks_repeated_b1_until_invalidated(
        self,
    ) -> None:
        first_signal = Signal(
            type="B1",
            level="main",
            trigger="b1",
            invalid_if="invalid",
            confidence=0.85,
            event_time=self._t(5),
            available_time=self._t(6),
            invalid_price=100.0,
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        repeat_signal = Signal(
            type="B1",
            level="main",
            trigger="b1",
            invalid_if="invalid",
            confidence=0.85,
            event_time=self._t(6),
            available_time=self._t(7),
            invalid_price=105.0,
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        first_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[first_signal],
            action=Action(decision="buy", reason="buy"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="buy",
        )
        repeat_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[repeat_signal],
            action=Action(decision="buy", reason="buy"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="buy",
        )

        seen_signal_keys: set[tuple] = set()
        guards: dict[tuple, dict[str, object]] = {}
        first = suppress_seen_signal_events(
            first_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
            active_turning_guards=guards,
            asof_low=101.0,
            asof_high=110.0,
        )
        self.assertEqual(first.action.decision, "buy")
        self.assertEqual(len(first.signals), 1)

        second = suppress_seen_signal_events(
            repeat_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
            active_turning_guards=guards,
            asof_low=104.0,
            asof_high=112.0,
        )
        self.assertFalse(second.signals)
        self.assertEqual(second.action.decision, "hold")

    def test_suppress_seen_signal_events_rearms_b1_after_invalidation(
        self,
    ) -> None:
        first_signal = Signal(
            type="B1",
            level="main",
            trigger="b1",
            invalid_if="invalid",
            confidence=0.85,
            event_time=self._t(5),
            available_time=self._t(6),
            invalid_price=100.0,
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        next_signal = Signal(
            type="B1",
            level="main",
            trigger="b1",
            invalid_if="invalid",
            confidence=0.85,
            event_time=self._t(8),
            available_time=self._t(9),
            invalid_price=95.0,
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        first_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[first_signal],
            action=Action(decision="buy", reason="buy"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="buy",
        )
        empty_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[],
            action=Action(decision="hold", reason="hold"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="hold",
        )
        next_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[next_signal],
            action=Action(decision="buy", reason="buy"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="buy",
        )

        seen_signal_keys: set[tuple] = set()
        guards: dict[tuple, dict[str, object]] = {}
        suppress_seen_signal_events(
            first_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
            active_turning_guards=guards,
            asof_low=101.0,
            asof_high=110.0,
        )
        suppress_seen_signal_events(
            empty_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
            active_turning_guards=guards,
            asof_low=99.0,
            asof_high=110.0,
        )

        rearmed = suppress_seen_signal_events(
            next_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
            active_turning_guards=guards,
            asof_low=96.0,
            asof_high=108.0,
        )
        self.assertEqual(rearmed.action.decision, "buy")
        self.assertEqual(len(rearmed.signals), 1)

    def test_suppress_seen_signal_events_allows_b2_after_b1_same_center(
        self,
    ) -> None:
        b1_signal = Signal(
            type="B1",
            level="main",
            trigger="b1",
            invalid_if="invalid",
            confidence=0.85,
            event_time=self._t(5),
            available_time=self._t(6),
            invalid_price=100.0,
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        b2_signal = Signal(
            type="B2",
            level="sub",
            trigger="b2",
            invalid_if="invalid",
            confidence=0.97,
            event_time=self._t(6),
            available_time=self._t(7),
            invalid_price=100.0,
            anchor_center_start_index=7,
            anchor_center_end_index=9,
        )
        b1_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[b1_signal],
            action=Action(decision="buy", reason="buy"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="buy",
        )
        b2_decision = SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type="down"),
            signals=[b2_signal],
            action=Action(decision="buy", reason="buy"),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary="buy",
        )

        seen_signal_keys: set[tuple] = set()
        guards: dict[tuple, dict[str, object]] = {}
        suppress_seen_signal_events(
            b1_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
            active_turning_guards=guards,
            asof_low=101.0,
            asof_high=110.0,
        )
        confirmed = suppress_seen_signal_events(
            b2_decision,
            seen_signal_keys,
            get_chan_config("orthodox_chan"),
            0.60,
            active_turning_guards=guards,
            asof_low=101.0,
            asof_high=111.0,
        )
        self.assertEqual(confirmed.action.decision, "buy")
        self.assertEqual(len(confirmed.signals), 1)
        self.assertEqual(confirmed.signals[0].type, "B2")

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

    def test_strict_mode_skips_consolidation_divergence_as_b1(self) -> None:
        bis = [
            self._mk_bi(0, "down", 120, 110),
            self._mk_bi(1, "up", 110, 116),
            self._mk_bi(2, "down", 116, 106),
            self._mk_bi(3, "up", 106, 112),
            self._mk_bi(4, "down", 112, 102),
        ]
        macd = [
            MACDPoint(time=self._t(1), dif=-3.0, dea=-2.0, hist=-8.0),
            MACDPoint(time=self._t(3), dif=-2.0, dea=-1.5, hist=-4.0),
            MACDPoint(time=self._t(5), dif=-1.0, dea=-0.8, hist=-2.0),
        ]

        divergence = detect_divergence_candidates(
            bis=bis,
            zhongshu_count=1,
            trend_type="down",
            macd=macd,
            threshold=0.10,
            zhongshus=[],
            include_consolidation_divergence_hint=False,
        )

        self.assertEqual(divergence, [])

    def test_consolidation_divergence_requires_reentry_to_center_between_departures(
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
            available_time=self._t(2),
        )
        bis = [
            self._mk_bi(2, "down", 101, 96),
            self._mk_bi(3, "up", 96, 97),
            self._mk_bi(4, "down", 100, 94),
        ]
        macd = [
            MACDPoint(time=self._t(3), dif=-2.0, dea=-1.0, hist=-8.0),
            MACDPoint(time=self._t(5), dif=-1.0, dea=-0.8, hist=-4.0),
        ]

        divergence = detect_divergence_candidates(
            bis=bis,
            zhongshu_count=1,
            trend_type="range",
            macd=macd,
            threshold=0.10,
            zhongshus=[zs],
            include_consolidation_divergence_hint=True,
            consolidation_anchor=zs,
        )

        self.assertEqual(divergence, [])

    def test_consolidation_divergence_detects_non_executable_hint_inside_single_center(self) -> None:
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
        bis = [
            self._mk_bi(2, "down", 101, 96),
            self._mk_bi(3, "up", 96, 101),
            self._mk_bi(4, "down", 100, 94),
        ]
        macd = [
            MACDPoint(time=self._t(3), dif=-2.0, dea=-1.0, hist=-8.0),
            MACDPoint(time=self._t(5), dif=-1.0, dea=-0.8, hist=-4.0),
        ]

        divergence = detect_divergence_candidates(
            bis=bis,
            zhongshu_count=1,
            trend_type="range",
            macd=macd,
            threshold=0.10,
            zhongshus=[zs],
            include_consolidation_divergence_hint=True,
            consolidation_anchor=zs,
        )

        self.assertEqual(len(divergence), 1)
        self.assertIsNone(divergence[0].signal_type)
        self.assertEqual(divergence[0].mode, "consolidation")
        self.assertIn("盘整背驰", divergence[0].trigger)
        self.assertEqual(divergence[0].anchor_center_start_index, 0)
        self.assertEqual(divergence[0].anchor_center_end_index, 2)
        self.assertEqual(divergence[0].anchor_center_available_time, self._t(2))

    def test_trend_divergence_c_leg_stops_before_next_center_boundary(self) -> None:
        bis = [
            self._mk_bi(0, "down", 120, 90),
            self._mk_bi(1, "up", 90, 112),
            self._mk_bi(2, "down", 112, 101),
            self._mk_bi(3, "up", 101, 107),
            self._mk_bi(4, "down", 107, 102),
            self._mk_bi(5, "up", 102, 110),
            self._mk_bi(6, "down", 110, 96),
            self._mk_bi(7, "up", 96, 100),
            self._mk_bi(8, "down", 100, 88),
            self._mk_bi(9, "up", 88, 94),
            self._mk_bi(10, "down", 94, 90),
            self._mk_bi(11, "up", 90, 96),
            self._mk_bi(12, "down", 96, 91),
            self._mk_bi(13, "up", 91, 95),
            self._mk_bi(14, "down", 95, 92),
            self._mk_bi(15, "up", 92, 94),
            self._mk_bi(16, "down", 94, 93),
            self._mk_bi(17, "up", 93, 97),
            self._mk_bi(18, "down", 97, 80),
        ]
        zhongshus = [
            Zhongshu(
                zd=102.0,
                zg=107.0,
                gg=112.0,
                dd=101.0,
                g=107.0,
                d=102.0,
                start_index=2,
                end_index=5,
                event_time=self._t(5),
                available_time=self._t(5),
            ),
            Zhongshu(
                zd=90.0,
                zg=94.0,
                gg=100.0,
                dd=88.0,
                g=94.0,
                d=90.0,
                start_index=8,
                end_index=11,
                event_time=self._t(11),
                available_time=self._t(11),
            ),
            Zhongshu(
                zd=93.0,
                zg=94.0,
                gg=95.0,
                dd=92.0,
                g=94.0,
                d=93.0,
                start_index=14,
                end_index=17,
                event_time=self._t(17),
                available_time=self._t(17),
            ),
        ]
        macd = [
            MACDPoint(time=self._t(1), dif=-4.0, dea=-3.0, hist=-10.0),
            MACDPoint(time=self._t(9), dif=0.0, dea=0.0, hist=0.0),
            MACDPoint(time=self._t(13), dif=-2.0, dea=-1.0, hist=-3.0),
            MACDPoint(time=self._t(19), dif=-1.0, dea=-0.5, hist=-1.0),
        ]

        divergence = detect_divergence_candidates(
            bis=bis,
            zhongshu_count=3,
            trend_type="down",
            macd=macd,
            threshold=0.10,
            zhongshus=zhongshus,
        )

        self.assertEqual(divergence, [])

    def test_trend_divergence_detects_b1_from_bounded_c_leg(self) -> None:
        bis = [
            self._mk_bi(0, "down", 120, 90),
            self._mk_bi(1, "up", 90, 112),
            self._mk_bi(2, "down", 112, 101),
            self._mk_bi(3, "up", 101, 107),
            self._mk_bi(4, "down", 107, 102),
            self._mk_bi(5, "up", 102, 110),
            self._mk_bi(6, "down", 110, 96),
            self._mk_bi(7, "up", 96, 100),
            self._mk_bi(8, "down", 100, 88),
            self._mk_bi(9, "up", 88, 94),
            self._mk_bi(10, "down", 94, 90),
            self._mk_bi(11, "up", 90, 96),
            self._mk_bi(12, "down", 96, 84),
            self._mk_bi(13, "up", 84, 92),
        ]
        zhongshus = [
            Zhongshu(
                zd=102.0,
                zg=107.0,
                gg=112.0,
                dd=101.0,
                g=107.0,
                d=102.0,
                start_index=2,
                end_index=5,
                event_time=self._t(5),
                available_time=self._t(5),
            ),
            Zhongshu(
                zd=90.0,
                zg=94.0,
                gg=100.0,
                dd=88.0,
                g=94.0,
                d=90.0,
                start_index=8,
                end_index=11,
                event_time=self._t(11),
                available_time=self._t(11),
            ),
        ]
        macd = [
            MACDPoint(time=self._t(1), dif=-4.0, dea=-3.0, hist=-10.0),
            MACDPoint(time=self._t(8), dif=0.0, dea=0.0, hist=0.0),
            MACDPoint(time=self._t(13), dif=-1.5, dea=-1.0, hist=-4.0),
        ]

        divergence = detect_divergence_candidates(
            bis=bis,
            zhongshu_count=2,
            trend_type="down",
            macd=macd,
            threshold=0.10,
            zhongshus=zhongshus,
        )

        self.assertEqual(len(divergence), 1)
        self.assertEqual(divergence[0].signal_type, "B1")
        self.assertEqual(divergence[0].available_time, self._t(13))
        self.assertEqual(divergence[0].anchor_center_start_index, 8)
        self.assertEqual(divergence[0].anchor_center_end_index, 11)
        self.assertEqual(divergence[0].anchor_center_available_time, self._t(11))

    def test_trend_divergence_rejects_center_expansion_pair(self) -> None:
        bis = [
            self._mk_bi(0, "down", 120, 90),
            self._mk_bi(1, "up", 90, 112),
            self._mk_bi(2, "down", 112, 101),
            self._mk_bi(3, "up", 101, 107),
            self._mk_bi(4, "down", 107, 102),
            self._mk_bi(5, "up", 102, 110),
            self._mk_bi(6, "down", 110, 96),
            self._mk_bi(7, "up", 96, 100),
            self._mk_bi(8, "down", 100, 88),
            self._mk_bi(9, "up", 88, 94),
            self._mk_bi(10, "down", 94, 90),
            self._mk_bi(11, "up", 90, 96),
            self._mk_bi(12, "down", 96, 84),
            self._mk_bi(13, "up", 84, 92),
        ]
        zhongshus = [
            Zhongshu(
                zd=102.0,
                zg=107.0,
                gg=112.0,
                dd=101.0,
                g=107.0,
                d=102.0,
                start_index=2,
                end_index=5,
                event_time=self._t(5),
                available_time=self._t(5),
            ),
            Zhongshu(
                zd=90.0,
                zg=94.0,
                gg=101.0,
                dd=88.0,
                g=94.0,
                d=90.0,
                start_index=8,
                end_index=11,
                event_time=self._t(11),
                available_time=self._t(11),
                evolution="expansion",
            ),
        ]
        macd = [
            MACDPoint(time=self._t(1), dif=-4.0, dea=-3.0, hist=-10.0),
            MACDPoint(time=self._t(8), dif=0.0, dea=0.0, hist=0.0),
            MACDPoint(time=self._t(13), dif=-1.5, dea=-1.0, hist=-4.0),
        ]

        divergence = detect_divergence_candidates(
            bis=bis,
            zhongshu_count=2,
            trend_type="down",
            macd=macd,
            threshold=0.10,
            zhongshus=zhongshus,
            include_consolidation_divergence_hint=False,
        )

        self.assertEqual(divergence, [])

    def test_strict_interval_set_requires_sub_confirmation_for_b1(self) -> None:
        main_candidate = self._mk_divergence("B1", 8, 90.0)
        zs = Zhongshu(
            zd=95.0,
            zg=100.0,
            gg=105.0,
            dd=90.0,
            g=100.0,
            d=95.0,
            start_index=0,
            end_index=3,
            event_time=self._t(3),
            available_time=self._t(3),
        )
        snapshot = SimpleNamespace(
            bars_sub=[Bar(time=self._t(8), open=100, high=101, low=99, close=100)],
            bis_sub=[],
            segments_sub=[],
            macd_sub=[MACDPoint(time=self._t(8), dif=0.0, dea=0.0, hist=-1.0)],
            last_zhongshu_main=zs,
            asof_time=self._t(9),
        )

        with unittest.mock.patch("ai_trader.chan.engine.build_zhongshus_from_bis", return_value=[zs, zs]), unittest.mock.patch(
            "ai_trader.chan.engine.infer_market_state",
            return_value=MarketState(trend_type="down", zhongshu_count=2),
        ), unittest.mock.patch(
            "ai_trader.chan.engine.detect_divergence_candidates",
            return_value=[],
        ):
            filtered = _sub_interval_confirmed(
                snapshot,
                [main_candidate],
                threshold=0.10,
                cfg=SimpleNamespace(
                    require_sub_interval_confirmation=True,
                    missing_macd_penalty=0.10,
                    transitional_confidence_cap=0.60,
                ),
            )

        self.assertEqual(filtered, [])

    def test_strict_interval_accepts_sub_b2_confirmation_for_b1(self) -> None:
        main_candidate = self._mk_divergence("B1", 8, 90.0)
        sub_b1 = self._mk_divergence("B1", 8, 91.0)
        zs = Zhongshu(
            zd=95.0,
            zg=100.0,
            gg=105.0,
            dd=90.0,
            g=100.0,
            d=95.0,
            start_index=0,
            end_index=3,
            event_time=self._t(3),
            available_time=self._t(3),
        )
        snapshot = SimpleNamespace(
            bars_sub=[Bar(time=self._t(11), open=98, high=100, low=97, close=99)],
            bis_sub=[
                self._mk_bi(8, "up", 91, 96),
                self._mk_bi(9, "down", 96, 92),
            ],
            segments_sub=[],
            macd_sub=[],
            last_zhongshu_main=zs,
            asof_time=self._t(11),
        )

        with unittest.mock.patch(
            "ai_trader.chan.engine.build_zhongshus_from_bis",
            return_value=[zs],
        ), unittest.mock.patch(
            "ai_trader.chan.engine.infer_market_state",
            return_value=MarketState(trend_type="down", zhongshu_count=1),
        ), unittest.mock.patch(
            "ai_trader.chan.engine.detect_divergence_candidates",
            return_value=[sub_b1],
        ):
            filtered = _sub_interval_confirmed(
                snapshot,
                [main_candidate],
                threshold=0.10,
                cfg=SimpleNamespace(
                    require_sub_interval_confirmation=True,
                    missing_macd_penalty=0.10,
                    transitional_confidence_cap=0.60,
                ),
            )

        self.assertEqual(filtered, [main_candidate])

    def test_strict_interval_uses_candidate_anchor_center_time(self) -> None:
        main_candidate = self._mk_divergence(
            "B1",
            8,
            90.0,
            anchor_center_start_index=0,
            anchor_center_end_index=3,
            anchor_center_available_time=self._t(3),
        )
        sub_b1 = self._mk_divergence("B1", 8, 91.0)
        main_zs = Zhongshu(
            zd=95.0,
            zg=100.0,
            gg=105.0,
            dd=90.0,
            g=100.0,
            d=95.0,
            start_index=10,
            end_index=13,
            event_time=self._t(13),
            available_time=self._t(13),
        )
        sub_zs = Zhongshu(
            zd=95.0,
            zg=100.0,
            gg=105.0,
            dd=90.0,
            g=100.0,
            d=95.0,
            start_index=0,
            end_index=3,
            event_time=self._t(3),
            available_time=self._t(3),
        )
        snapshot = SimpleNamespace(
            bars_sub=[Bar(time=self._t(11), open=98, high=100, low=97, close=99)],
            bis_sub=[
                self._mk_bi(8, "up", 91, 96),
                self._mk_bi(9, "down", 96, 92),
                self._mk_bi(10, "up", 92, 99),
            ],
            segments_sub=[],
            macd_sub=[],
            last_zhongshu_main=main_zs,
            asof_time=self._t(11),
        )

        with unittest.mock.patch(
            "ai_trader.chan.engine.build_zhongshus_from_bis",
            return_value=[sub_zs],
        ), unittest.mock.patch(
            "ai_trader.chan.engine.infer_market_state",
            return_value=MarketState(trend_type="down", zhongshu_count=1),
        ), unittest.mock.patch(
            "ai_trader.chan.engine.detect_divergence_candidates",
            return_value=[sub_b1],
        ):
            filtered = _sub_interval_confirmed(
                snapshot,
                [main_candidate],
                threshold=0.10,
                cfg=SimpleNamespace(
                    require_sub_interval_confirmation=True,
                    missing_macd_penalty=0.10,
                    transitional_confidence_cap=0.60,
                ),
            )

        self.assertEqual(filtered, [main_candidate])

    def test_generate_signals_skip_non_executable_consolidation_hint(self) -> None:
        signals = generate_signals(
            divergence_candidates=[
                self._mk_divergence(None, 8, 90.0, mode="consolidation")
            ],
            bis_sub=[],
            segments_sub=[],
            zhongshu_main=None,
            market_state=MarketState(trend_type="range", phase="consolidating"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            zhongshus_sub=[],
        )

        self.assertEqual(signals, [])

    def test_generate_signal_in_pragmatic_mode_keeps_consolidation_note_off_signal_list(self) -> None:
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
        snapshot = ChanSnapshot(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            asof_time=self._t(6),
            bars_main=[
                Bar(time=self._t(4), open=100, high=101, low=95, close=96),
                Bar(time=self._t(5), open=96, high=100, low=93, close=94),
            ],
            bars_sub=[],
            macd_main=[
                MACDPoint(time=self._t(3), dif=-2.0, dea=-1.0, hist=-8.0),
                MACDPoint(time=self._t(5), dif=-1.0, dea=-0.8, hist=-4.0),
            ],
            macd_sub=[],
            fractals_main=[],
            fractals_sub=[],
            bis_main=[
                self._mk_bi(2, "down", 101, 96),
                self._mk_bi(3, "up", 96, 101),
                self._mk_bi(4, "down", 100, 94),
            ],
            bis_sub=[],
            segments_main=[],
            segments_sub=[],
            previous_main_bar_time=self._t(4),
            zhongshus_main=[zs],
            zhongshus_sub=[],
            last_zhongshu_main=zs,
            trend_type_main="range",
            market_state_main=MarketState(
                trend_type="range",
                walk_type="consolidation",
                phase="consolidating",
                zhongshu_count=1,
                last_zhongshu={"zd": 98.0, "zg": 102.0, "gg": 110.0, "dd": 92.0},
                current_stroke_dir="down",
                current_segment_dir="down",
                oscillation_state={
                    "anchor_source": "current_center",
                    "anchor_start_index": 0,
                    "z": 100.0,
                    "latest_zn": 97.0,
                    "count": 3,
                    "total_count": 3,
                    "bias": "weak",
                    "direction": "falling",
                    "breakout": "below_zd",
                    "first_breakout": True,
                    "limit_reached": False,
                },
            ),
            data_quality=DataQuality(status="ok", notes=""),
        )

        decision = generate_signal(snapshot, chan_config=get_chan_config("pragmatic"))

        self.assertEqual(decision.signals, [])
        self.assertEqual(decision.action.decision, "hold")
        self.assertIn("盘整背驰", decision.risk.notes)
        self.assertNotIn("B1", {item.type for item in decision.signals})

    def test_strict_interval_accepts_structural_b3_without_sub_macd(self) -> None:
        main_candidate = self._mk_divergence("B1", 4, 90.0)
        main_zs = Zhongshu(
            zd=95.0,
            zg=100.0,
            gg=105.0,
            dd=90.0,
            g=100.0,
            d=95.0,
            start_index=0,
            end_index=3,
            event_time=self._t(3),
            available_time=self._t(3),
        )
        sub_zs = Zhongshu(
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
        snapshot = SimpleNamespace(
            bars_sub=[Bar(time=self._t(8), open=104, high=106, low=103, close=105)],
            bis_sub=[],
            segments_sub=[
                self._mk_segment(4, "down", 103.0, 99.0),
                self._mk_segment(5, "up", 108.0, 100.0),
                self._mk_segment(6, "down", 107.0, 103.0),
                self._mk_segment(7, "up", 112.0, 104.0),
            ],
            macd_sub=[],
            last_zhongshu_main=main_zs,
            asof_time=self._t(9),
        )

        with unittest.mock.patch(
            "ai_trader.chan.engine.build_zhongshus_from_bis",
            return_value=[sub_zs],
        ), unittest.mock.patch(
            "ai_trader.chan.engine.infer_market_state",
            return_value=MarketState(trend_type="up", zhongshu_count=1),
        ), unittest.mock.patch(
            "ai_trader.chan.engine.detect_divergence_candidates",
            return_value=[],
        ):
            filtered = _sub_interval_confirmed(
                snapshot,
                [main_candidate],
                threshold=0.10,
                cfg=SimpleNamespace(
                    require_sub_interval_confirmation=True,
                    missing_macd_penalty=0.10,
                    transitional_confidence_cap=0.60,
                ),
            )

        self.assertEqual(filtered, [main_candidate])

    def test_build_chan_state_uses_bi_zhongshu_for_main_snapshot(self) -> None:
        bars_main = make_synthetic_bars(start=self._t(0), count=60, step_hours=4)
        bars_sub = make_synthetic_bars(start=self._t(0), count=237, step_hours=1)
        main_bis = [self._mk_bi(10, "down", 112.0, 96.0)]
        sub_bis = [self._mk_bi(20, "up", 94.0, 102.0)]
        main_zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=10,
            end_index=13,
            event_time=self._t(13),
            available_time=self._t(13),
        )
        sub_zs = Zhongshu(
            zd=94.0,
            zg=97.0,
            gg=103.0,
            dd=90.0,
            g=97.0,
            d=94.0,
            start_index=20,
            end_index=23,
            event_time=self._t(23),
            available_time=self._t(23),
        )
        market_state = MarketState(
            trend_type="down",
            walk_type="trend",
            phase="trending",
            zhongshu_count=1,
        )

        with unittest.mock.patch(
            "ai_trader.chan.engine.merge_inclusions",
            side_effect=[bars_main, bars_sub],
        ), unittest.mock.patch(
            "ai_trader.chan.engine.detect_fractals",
            side_effect=[[], []],
        ), unittest.mock.patch(
            "ai_trader.chan.engine.build_bis",
            side_effect=[main_bis, sub_bis],
        ), unittest.mock.patch(
            "ai_trader.chan.engine.build_segments",
            side_effect=[[], []],
        ), unittest.mock.patch(
            "ai_trader.chan.engine.build_zhongshus_from_bis",
            side_effect=[[main_zs], [sub_zs]],
        ) as mock_build_zhongshus, unittest.mock.patch(
            "ai_trader.chan.engine.infer_market_state",
            return_value=market_state,
        ):
            snapshot = build_chan_state(
                bars_main=bars_main,
                bars_sub=bars_sub,
                macd_main=[],
                macd_sub=[],
                asof_time=bars_main[-1].time,
            )

        self.assertEqual(mock_build_zhongshus.call_args_list[0].args[0], main_bis)
        self.assertEqual(mock_build_zhongshus.call_args_list[1].args[0], sub_bis)
        self.assertEqual(snapshot.zhongshus_main, [main_zs])
        self.assertIs(snapshot.last_zhongshu_main, main_zs)
        self.assertEqual(snapshot.zhongshus_sub, [sub_zs])
        self.assertEqual(snapshot.market_state_main, market_state)

    def test_market_state_requires_post_center_segments_for_transitional(self) -> None:
        zs = Zhongshu(
            zd=95.0,
            zg=100.0,
            gg=105.0,
            dd=90.0,
            g=100.0,
            d=95.0,
            start_index=0,
            end_index=3,
            event_time=self._t(3),
            available_time=self._t(3),
        )
        segments = [
            Segment(
                direction="up",
                start_index=0,
                end_index=3,
                high=105.0,
                low=95.0,
                event_time=self._t(3),
                available_time=self._t(3),
                status="confirmed",
            ),
            Segment(
                direction="down",
                start_index=4,
                end_index=6,
                high=103.0,
                low=97.0,
                event_time=self._t(4),
                available_time=self._t(4),
                status="confirmed",
            ),
        ]

        state = infer_market_state(
            bars_close=99.0,
            bis=[],
            segments=segments,
            zhongshus=[zs],
        )

        self.assertEqual(state.phase, "consolidating")

    def test_market_state_keeps_trend_before_reentering_prior_center(self) -> None:
        zhongshus = [
            Zhongshu(
                zd=95.0,
                zg=100.0,
                gg=100.0,
                dd=90.0,
                g=100.0,
                d=95.0,
                start_index=0,
                end_index=3,
                event_time=self._t(3),
                available_time=self._t(3),
            ),
            Zhongshu(
                zd=110.0,
                zg=115.0,
                gg=120.0,
                dd=106.0,
                g=115.0,
                d=110.0,
                start_index=4,
                end_index=7,
                event_time=self._t(7),
                available_time=self._t(7),
            ),
        ]
        segments = [
            Segment(
                direction="up",
                start_index=8,
                end_index=10,
                high=122.0,
                low=116.0,
                event_time=self._t(8),
                available_time=self._t(8),
                status="confirmed",
            ),
            Segment(
                direction="down",
                start_index=11,
                end_index=13,
                high=121.0,
                low=116.0,
                event_time=self._t(9),
                available_time=self._t(9),
                status="confirmed",
            ),
        ]

        state = infer_market_state(
            bars_close=118.0,
            bis=[],
            segments=segments,
            zhongshus=zhongshus,
        )

        self.assertEqual(state.trend_type, "up")
        self.assertEqual(state.walk_type, "trend")
        self.assertEqual(state.phase, "trending")

    def test_market_state_tracks_oscillation_zn_breakout(self) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=3,
            event_time=self._t(3),
            available_time=self._t(3),
        )
        segments = [
            Segment(
                direction="up",
                start_index=0,
                end_index=3,
                high=104.0,
                low=98.0,
                event_time=self._t(3),
                available_time=self._t(3),
                status="confirmed",
            ),
            Segment(
                direction="down",
                start_index=4,
                end_index=6,
                high=103.0,
                low=97.0,
                event_time=self._t(4),
                available_time=self._t(4),
                status="confirmed",
            ),
            Segment(
                direction="up",
                start_index=7,
                end_index=9,
                high=110.0,
                low=101.0,
                event_time=self._t(5),
                available_time=self._t(5),
                status="confirmed",
            ),
        ]

        state = infer_market_state(
            bars_close=104.0,
            bis=[],
            segments=segments,
            zhongshus=[zs],
        )

        oscillation = state.oscillation_state
        self.assertEqual(oscillation["anchor_source"], "current_center")
        self.assertEqual(oscillation["count"], 3)
        self.assertEqual(oscillation["bias"], "strong")
        self.assertEqual(oscillation["direction"], "rising")
        self.assertEqual(oscillation["breakout"], "above_zg")
        self.assertTrue(oscillation["first_breakout"])

    def test_market_state_oscillation_limit_reached_after_nine_segments(self) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=3,
            event_time=self._t(3),
            available_time=self._t(3),
        )
        segments = [
            Segment(
                direction="up" if i % 2 == 0 else "down",
                start_index=i * 3,
                end_index=i * 3 + 2,
                high=104.0 + (i % 3),
                low=97.0 + (i % 2),
                event_time=self._t(3 + i),
                available_time=self._t(3 + i),
                status="confirmed",
            )
            for i in range(10)
        ]

        state = infer_market_state(
            bars_close=100.0,
            bis=[],
            segments=segments,
            zhongshus=[zs],
        )

        oscillation = state.oscillation_state
        self.assertEqual(oscillation["count"], 9)
        self.assertEqual(oscillation["total_count"], 10)
        self.assertTrue(oscillation["limit_reached"])

    def test_trend_type_requires_two_same_direction_centers(self) -> None:
        zs = [
            Zhongshu(
                zd=95.0,
                zg=100.0,
                gg=105.0,
                dd=90.0,
                g=100.0,
                d=95.0,
                start_index=0,
                end_index=3,
                event_time=self._t(3),
                available_time=self._t(3),
            )
        ]
        bis = [self._mk_bi(0, "up", 90, 110)]

        self.assertEqual(infer_trend_type(110.0, bis, zs), "range")

    def test_trend_type_keeps_latest_completed_trend_when_later_center_overlaps(self) -> None:
        zhongshus = [
            Zhongshu(
                zd=95.0,
                zg=100.0,
                gg=100.0,
                dd=90.0,
                g=100.0,
                d=95.0,
                start_index=0,
                end_index=3,
                event_time=self._t(3),
                available_time=self._t(3),
            ),
            Zhongshu(
                zd=110.0,
                zg=115.0,
                gg=120.0,
                dd=106.0,
                g=115.0,
                d=110.0,
                start_index=4,
                end_index=7,
                event_time=self._t(7),
                available_time=self._t(7),
            ),
            Zhongshu(
                zd=112.0,
                zg=114.0,
                gg=118.0,
                dd=109.0,
                g=114.0,
                d=112.0,
                start_index=8,
                end_index=11,
                event_time=self._t(11),
                available_time=self._t(11),
                evolution="expansion",
            ),
        ]

        state = infer_market_state(
            bars_close=113.0,
            bis=[],
            segments=[],
            zhongshus=zhongshus,
        )

        self.assertEqual(infer_trend_type(113.0, [], zhongshus), "up")
        self.assertEqual(state.trend_type, "up")
        self.assertEqual(state.walk_type, "consolidation")
        self.assertEqual(state.phase, "transitional")

    def test_transitional_wait_uses_zn_breakout_note_without_b3(self) -> None:
        action, summary = decide_action(
            signals=[],
            market_state=MarketState(
                trend_type="range",
                phase="transitional",
                oscillation_state={
                    "anchor_source": "prior_trend_center",
                    "anchor_start_index": 12,
                    "z": 100.0,
                    "latest_zn": 103.0,
                    "count": 3,
                    "total_count": 3,
                    "bias": "strong",
                    "direction": "rising",
                    "breakout": "above_zg",
                    "first_breakout": True,
                    "limit_reached": False,
                },
            ),
            conflict_level="low",
            min_confidence=0.60,
            chan_config=SimpleNamespace(
                execution_buy_types=("B2", "B3"),
                execution_reduce_types=("S2", "S3"),
                execution_sell_types=(),
                execution_buy_min_confidence=0.65,
                execution_reduce_min_confidence=0.65,
                require_non_high_conflict_buy=True,
                reduce_only_on_high_conflict=True,
            ),
        )

        self.assertEqual(action.decision, "wait")
        self.assertIn("Zn", action.reason)
        self.assertIn("中枢扩展", summary)

    def test_transitional_allows_b1_buy_when_reversing_downtrend(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="B1",
                    level="main",
                    trigger="B1 trigger",
                    invalid_if="B1 invalid",
                    confidence=0.60,
                    event_time=self._t(8),
                    available_time=self._t(8),
                    invalid_price=95.0,
                )
            ],
            market_state=MarketState(
                trend_type="down",
                phase="transitional",
                oscillation_state={
                    "anchor_source": "prior_trend_center",
                    "anchor_start_index": 12,
                    "z": 100.0,
                    "latest_zn": 103.0,
                    "count": 3,
                    "total_count": 3,
                    "bias": "strong",
                    "direction": "rising",
                    "breakout": "above_zg",
                    "first_breakout": True,
                    "limit_reached": False,
                },
            ),
            conflict_level="none",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "buy")
        self.assertIn("B1", action.reason)
        self.assertIn("一类买点", summary)

    def test_transitional_allows_s1_sell_when_reversing_uptrend(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="S1",
                    level="main",
                    trigger="S1 trigger",
                    invalid_if="S1 invalid",
                    confidence=0.60,
                    event_time=self._t(8),
                    available_time=self._t(8),
                    invalid_price=105.0,
                )
            ],
            market_state=MarketState(
                trend_type="up",
                phase="transitional",
                oscillation_state={
                    "anchor_source": "prior_trend_center",
                    "anchor_start_index": 12,
                    "z": 100.0,
                    "latest_zn": 97.0,
                    "count": 3,
                    "total_count": 3,
                    "bias": "weak",
                    "direction": "falling",
                    "breakout": "below_zd",
                    "first_breakout": True,
                    "limit_reached": False,
                },
            ),
            conflict_level="none",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "sell")
        self.assertIn("S1", action.reason)
        self.assertIn("一类卖点", summary)

    def test_transitional_allows_b2_buy_when_reversing_downtrend(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="B2",
                    level="sub",
                    trigger="B2 trigger",
                    invalid_if="B2 invalid",
                    confidence=0.72,
                    event_time=self._t(8),
                    available_time=self._t(8),
                    invalid_price=95.0,
                )
            ],
            market_state=MarketState(
                trend_type="down",
                phase="transitional",
                oscillation_state={
                    "anchor_source": "prior_trend_center",
                    "anchor_start_index": 12,
                    "z": 100.0,
                    "latest_zn": 103.0,
                    "count": 3,
                    "total_count": 3,
                    "bias": "strong",
                    "direction": "rising",
                    "breakout": "above_zg",
                    "first_breakout": True,
                    "limit_reached": False,
                },
            ),
            conflict_level="none",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "buy")
        self.assertIn("保守确认买点", summary)

    def test_generate_signal_in_strict_mode_skips_consolidation_b1_candidate(self) -> None:
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
        snapshot = ChanSnapshot(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            asof_time=self._t(5),
            bars_main=[
                Bar(time=self._t(4), open=100, high=101, low=95, close=96),
                Bar(time=self._t(5), open=96, high=100, low=93, close=94),
            ],
            bars_sub=[],
            macd_main=[
                MACDPoint(time=self._t(3), dif=-2.0, dea=-1.0, hist=-8.0),
                MACDPoint(time=self._t(5), dif=-1.0, dea=-0.8, hist=-4.0),
            ],
            macd_sub=[],
            fractals_main=[],
            fractals_sub=[],
            bis_main=[
                self._mk_bi(2, "down", 101, 96),
                self._mk_bi(3, "up", 96, 101),
                self._mk_bi(4, "down", 100, 94),
            ],
            bis_sub=[],
            segments_main=[],
            segments_sub=[],
            previous_main_bar_time=self._t(4),
            zhongshus_main=[zs],
            zhongshus_sub=[],
            last_zhongshu_main=zs,
            trend_type_main="range",
            market_state_main=MarketState(
                trend_type="range",
                walk_type="consolidation",
                phase="consolidating",
                zhongshu_count=1,
                last_zhongshu={"zd": 98.0, "zg": 102.0, "gg": 110.0, "dd": 92.0},
                current_stroke_dir="down",
                current_segment_dir="down",
                oscillation_state={
                    "anchor_source": "current_center",
                    "anchor_start_index": 0,
                    "z": 100.0,
                    "latest_zn": 97.0,
                    "count": 3,
                    "total_count": 3,
                    "bias": "weak",
                    "direction": "falling",
                    "breakout": "below_zd",
                    "first_breakout": True,
                    "limit_reached": False,
                },
            ),
            data_quality=DataQuality(status="ok", notes=""),
        )

        decision = generate_signal(snapshot, chan_config=get_chan_config("strict_kline8"))

        signal_types = {item.type for item in decision.signals}
        self.assertNotIn("B1", signal_types)
        self.assertEqual(signal_types, set())
        self.assertEqual(decision.action.decision, "hold")
        self.assertEqual(decision.cn_summary, "暂无明确执行信号，以观察为主。")

    def test_orthodox_mode_prefers_b1_over_b2_when_enabled(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="B2",
                    level="main",
                    trigger="B2 trigger",
                    invalid_if="B2 invalid",
                    confidence=0.95,
                    event_time=self._t(8),
                    available_time=self._t(8),
                ),
                Signal(
                    type="B1",
                    level="main",
                    trigger="B1 trigger",
                    invalid_if="B1 invalid",
                    confidence=0.70,
                    event_time=self._t(8),
                    available_time=self._t(8),
                ),
            ],
            market_state=MarketState(trend_type="down", phase="consolidating"),
            conflict_level="low",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "buy")
        self.assertIn("B1", action.reason)
        self.assertIn("一类买点", summary)

    def test_orthodox_mode_allows_b2_buy_under_high_conflict_when_reversing_downtrend(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="B2",
                    level="sub",
                    trigger="B2 trigger",
                    invalid_if="B2 invalid",
                    confidence=0.97,
                    event_time=self._t(8),
                    available_time=self._t(8),
                    invalid_price=95.0,
                )
            ],
            market_state=MarketState(trend_type="down", phase="trending"),
            conflict_level="high",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "buy")
        self.assertIn("买点", summary)

    def test_orthodox_mode_allows_s1_sell_under_high_conflict_when_reversing_uptrend(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="S1",
                    level="main",
                    trigger="S1 trigger",
                    invalid_if="S1 invalid",
                    confidence=0.85,
                    event_time=self._t(8),
                    available_time=self._t(8),
                    invalid_price=105.0,
                )
            ],
            market_state=MarketState(trend_type="up", phase="trending"),
            conflict_level="high",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "sell")
        self.assertIn("卖点", summary)

    def test_orthodox_mode_allows_b3_buy_under_high_conflict(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="B3",
                    level="main",
                    trigger="B3 trigger",
                    invalid_if="B3 invalid",
                    confidence=0.68,
                    event_time=self._t(8),
                    available_time=self._t(8),
                    invalid_price=95.0,
                )
            ],
            market_state=MarketState(trend_type="up", phase="trending"),
            conflict_level="high",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "buy")
        self.assertIn("保守确认买点", summary)

    def test_orthodox_mode_allows_s3_sell_under_high_conflict(self) -> None:
        action, summary = decide_action(
            signals=[
                Signal(
                    type="S3",
                    level="main",
                    trigger="S3 trigger",
                    invalid_if="S3 invalid",
                    confidence=0.68,
                    event_time=self._t(8),
                    available_time=self._t(8),
                    invalid_price=105.0,
                )
            ],
            market_state=MarketState(trend_type="down", phase="trending"),
            conflict_level="high",
            min_confidence=0.60,
            chan_config=get_chan_config("orthodox_chan"),
        )

        self.assertEqual(action.decision, "sell")
        self.assertIn("强卖出条件", action.reason)

    def test_orthodox_mode_keeps_b1_candidate_without_sub_confirmation(self) -> None:
        main_candidate = self._mk_divergence("B1", 8, 90.0)
        snapshot = SimpleNamespace(
            bars_sub=[],
            bis_sub=[],
            segments_sub=[],
            macd_sub=[],
            last_zhongshu_main=None,
        )

        filtered = _sub_interval_confirmed(
            snapshot,
            [main_candidate],
            threshold=0.10,
            cfg=SimpleNamespace(require_sub_interval_confirmation=False),
        )

        self.assertEqual(filtered, [main_candidate])

    def test_equal_same_kind_fractal_keeps_earlier_anchor(self) -> None:
        bars = [
            Bar(time=self._t(i), open=8.0, high=10.0, low=6.0, close=8.0)
            for i in range(9)
        ]
        bars[2] = Bar(time=self._t(2), open=10.0, high=12.0, low=8.0, close=11.0)
        bars[5] = Bar(time=self._t(5), open=10.0, high=12.0, low=8.0, close=11.0)
        bars[8] = Bar(time=self._t(8), open=5.0, high=6.0, low=4.0, close=5.0)
        fractals = [
            Fractal(
                kind="top",
                index=2,
                price=12.0,
                event_time=self._t(2),
                available_time=self._t(2),
            ),
            Fractal(
                kind="top",
                index=5,
                price=12.0,
                event_time=self._t(5),
                available_time=self._t(5),
            ),
            Fractal(
                kind="bottom",
                index=8,
                price=4.0,
                event_time=self._t(8),
                available_time=self._t(8),
            ),
        ]

        bis = build_bis(fractals, bars, min_bars=5)

        self.assertEqual(len(bis), 1)
        self.assertEqual(bis[0].start_index, 2)
        self.assertEqual(bis[0].end_index, 8)

    def test_bi_requires_extreme_bar_range_to_expand(self) -> None:
        bars = [
            Bar(time=self._t(i), open=8.0, high=9.0, low=6.0, close=8.0)
            for i in range(7)
        ]
        bars[2] = Bar(time=self._t(2), open=6.0, high=10.0, low=4.0, close=5.0)
        bars[6] = Bar(time=self._t(6), open=8.0, high=9.0, low=7.0, close=8.5)
        fractals = [
            Fractal(
                kind="bottom",
                index=2,
                price=4.0,
                event_time=self._t(2),
                available_time=self._t(2),
            ),
            Fractal(
                kind="top",
                index=6,
                price=9.0,
                event_time=self._t(6),
                available_time=self._t(6),
            ),
        ]

        bis = build_bis(fractals, bars, min_bars=5)

        self.assertEqual(bis, [])

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

    def test_bi_zhongshu_extension_has_no_artificial_cap(self) -> None:
        zhongshus = build_zhongshus_from_bis(
            [
                self._mk_bi(0, "up", 10.0, 20.0),
                self._mk_bi(1, "down", 19.0, 14.0),
                self._mk_bi(2, "up", 15.0, 24.0),
                self._mk_bi(3, "down", 18.0, 16.0),
                self._mk_bi(4, "up", 17.0, 23.0),
                self._mk_bi(5, "down", 18.0, 17.0),
                self._mk_bi(6, "up", 17.2, 22.0),
                self._mk_bi(7, "down", 17.9, 17.3),
                self._mk_bi(8, "up", 17.4, 21.5),
                self._mk_bi(9, "down", 17.8, 17.5),
                self._mk_bi(10, "up", 17.6, 21.0),
            ]
        )

        self.assertEqual(len(zhongshus), 1)
        self.assertEqual((zhongshus[0].zd, zhongshus[0].zg), (17.6, 17.8))
        self.assertEqual(zhongshus[0].end_index, 11)
        self.assertEqual(zhongshus[0].available_time, self._t(11))

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
        # After extension the first center covers segs 0-5. The last
        # candidate (segs 4-6) does not overlap the center interval but
        # its wave range touches, so it triggers level expansion. We keep
        # the same-level center sequence and mark the newer center as
        # "expansion" instead of collapsing everything into one giant center.
        self.assertEqual(len(zhongshus), 2)
        self.assertEqual(zhongshus[0].evolution, "extension")
        self.assertEqual(zhongshus[1].evolution, "expansion")
        self.assertEqual(zhongshus[1].start_index, 4)

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

    def test_conflict_high_with_s3_signal_executes_sell(self) -> None:
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
            asof_time=self._t(6),
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
                self._mk_segment(4, "down", 95.0, 90.0),
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
        self.assertEqual(decision.action.decision, "sell")

    def test_s3_uses_first_pullback_not_later_one(self) -> None:
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
                self._mk_segment(1, "up", 103.0, 99.0),
                self._mk_segment(2, "down", 101.0, 96.0),
                self._mk_segment(3, "up", 97.0, 93.0),
                self._mk_segment(4, "down", 95.0, 90.0),
                self._mk_segment(5, "up", 96.0, 91.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        s3 = next(item for item in signals if item.type == "S3")
        self.assertEqual(s3.event_time, self._t(5))
        self.assertEqual(s3.available_time, self._t(6))
        self.assertEqual(s3.anchor_center_start_index, 0)
        self.assertEqual(s3.anchor_center_end_index, 2)
        self.assertEqual(s3.anchor_center_available_time, self._t(2))

    def test_s3_uses_bi_context_when_present(self) -> None:
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
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(1, "up", 99.0, 103.0),
                self._mk_bi(2, "down", 101.0, 96.0),
                self._mk_bi(3, "up", 93.0, 97.0),
                self._mk_bi(4, "down", 95.0, 90.0),
            ],
        )

        s3 = next(item for item in signals if item.type == "S3")
        self.assertEqual(s3.event_time, self._t(4))
        self.assertEqual(s3.available_time, self._t(5))

    def test_s3_bi_context_requires_departure_after_center_end(self) -> None:
        zs = Zhongshu(
            zd=98.0,
            zg=102.0,
            gg=110.0,
            dd=92.0,
            g=102.0,
            d=98.0,
            start_index=0,
            end_index=4,
            event_time=self._t(2),
            available_time=self._t(2),
        )

        signals = generate_signals(
            divergence_candidates=[],
            bis_sub=[],
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(3, "down", 101.0, 96.0),
                self._mk_bi(4, "up", 93.0, 97.0),
                self._mk_bi(5, "down", 95.0, 90.0),
            ],
        )

        self.assertNotIn("S3", {item.type for item in signals})

    def test_s3_segment_requires_confirmation_to_stay_below_center(self) -> None:
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
                self._mk_segment(1, "up", 103.0, 99.0),
                self._mk_segment(2, "down", 101.0, 96.0),
                self._mk_segment(3, "up", 97.0, 93.0),
                self._mk_segment(4, "down", 99.0, 90.0),
            ],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
        )

        self.assertNotIn("S3", {item.type for item in signals})

    def test_s3_bi_context_requires_confirmation_to_stay_below_center(self) -> None:
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
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(3, "down", 101.0, 96.0),
                self._mk_bi(4, "up", 93.0, 97.0),
                self._mk_bi(5, "down", 100.0, 95.0),
            ],
        )

        self.assertNotIn("S3", {item.type for item in signals})

    def test_s3_bi_context_rejects_pullback_returning_into_center(self) -> None:
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
            segments_sub=[],
            zhongshu_main=zs,
            market_state=MarketState(trend_type="down"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=[
                self._mk_bi(1, "up", 99.0, 103.0),
                self._mk_bi(2, "down", 101.0, 96.0),
                self._mk_bi(3, "up", 93.0, 99.0),
            ],
        )

        self.assertNotIn("S3", {item.type for item in signals})


if __name__ == "__main__":
    unittest.main()
