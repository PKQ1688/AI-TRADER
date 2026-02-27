from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ai_trader.chan.core.center import build_zhongshus
from ai_trader.chan.core.fractal import detect_fractals
from ai_trader.chan.core.include import merge_inclusions
from ai_trader.chan.core.segment import build_segments
from ai_trader.chan.core.stroke import build_bis
from ai_trader.chan.engine import generate_signal
from ai_trader.types import (
    Bar,
    Bi,
    ChanSnapshot,
    DataQuality,
    MACDPoint,
    MarketState,
    Segment,
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
        self.assertGreaterEqual(len(zhongshus), 2)
        self.assertEqual(zhongshus[0].evolution, "extension")
        self.assertEqual(zhongshus[1].evolution, "expansion")

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
            Bi(direction="down", start_index=0, end_index=1, start_price=120, end_price=100, event_time=self._t(1), available_time=self._t(1)),
            Bi(direction="up", start_index=1, end_index=2, start_price=100, end_price=112, event_time=self._t(2), available_time=self._t(2)),
            Bi(direction="down", start_index=2, end_index=3, start_price=112, end_price=95, event_time=self._t(3), available_time=self._t(3)),
        ]
        bis_sub = [
            Bi(direction="down", start_index=0, end_index=1, start_price=105, end_price=99, event_time=self._t(1), available_time=self._t(1)),
            Bi(direction="up", start_index=1, end_index=2, start_price=99, end_price=103, event_time=self._t(2), available_time=self._t(2)),
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
            Bi(direction="up", start_index=0, end_index=1, start_price=92, end_price=97, event_time=self._t(1), available_time=self._t(1)),
            Bi(direction="down", start_index=1, end_index=2, start_price=97, end_price=96, event_time=self._t(2), available_time=self._t(2)),
        ]
        bis_sub = [
            Bi(direction="down", start_index=0, end_index=1, start_price=103, end_price=99, event_time=self._t(1), available_time=self._t(1)),
            Bi(direction="up", start_index=1, end_index=2, start_price=99, end_price=103, event_time=self._t(2), available_time=self._t(2)),
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
            asof_time=self._t(2),
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=[MACDPoint(time=self._t(1), dif=0.0, dea=0.0, hist=1.0)],
            macd_sub=[MACDPoint(time=self._t(1), dif=0.0, dea=0.0, hist=1.0)],
            fractals_main=[],
            fractals_sub=[],
            bis_main=bis_main,
            bis_sub=bis_sub,
            segments_main=[],
            segments_sub=[],
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
