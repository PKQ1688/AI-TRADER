from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ai_trader.chan import build_chan_state, generate_signal
from ai_trader.chan.config import get_chan_config
from ai_trader.chan.core.recursive import (
    build_parent_centers_from_expansions,
    build_recursive_levels_from_units,
    build_right_confirmed_walks_from_centers,
    build_structural_levels_from_segments,
    units_from_segments,
)
from ai_trader.chan.core.buy_sell_points import generate_signals
from ai_trader.chan.core.divergence import DivergenceCandidate
from ai_trader.chan.structural import StructuralView
from ai_trader.types import (
    CenterState,
    ChanLevel,
    MarketState,
    Segment,
    StructureUnit,
    WalkState,
    Zhongshu,
)
from tests.test_utils import make_synthetic_bars


class RecursiveChanStructureTest(unittest.TestCase):
    def _unit(
        self,
        idx: int,
        direction: str,
        low: float,
        high: float,
    ) -> StructureUnit:
        event_time = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(
            minutes=idx
        )
        return StructureUnit(
            id=f"u{idx}",
            level=0,
            kind="bi",
            direction=direction,  # type: ignore[arg-type]
            start_index=idx,
            end_index=idx + 1,
            high=high,
            low=low,
            start_price=low if direction == "up" else high,
            end_price=high if direction == "up" else low,
            event_time=event_time,
            available_time=event_time,
            status="confirmed",
        )

    def _center(
        self,
        idx: int,
        *,
        zd: float,
        zg: float,
        dd: float,
        gg: float,
    ) -> CenterState:
        event_time = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(
            minutes=idx
        )
        return CenterState(
            id=f"c{idx}",
            level=0,
            zhongshu=Zhongshu(
                zd=zd,
                zg=zg,
                dd=dd,
                gg=gg,
                g=zg,
                d=zd,
                start_index=idx * 10,
                end_index=idx * 10 + 9,
                event_time=event_time,
                available_time=event_time,
                origin_available_time=event_time,
            ),
            source_unit_ids=[f"u{idx}"],
        )

    def _segment(self, idx: int, status: str = "confirmed") -> Segment:
        event_time = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(
            minutes=idx
        )
        direction = "up" if idx % 2 == 0 else "down"
        return Segment(
            direction=direction,
            start_index=idx,
            end_index=idx + 1,
            high=20 + idx,
            low=10 + idx,
            event_time=event_time,
            available_time=event_time,
            status=status,  # type: ignore[arg-type]
        )

    def _walk(
        self,
        idx: int,
        direction: str,
        *,
        low: float,
        high: float,
    ) -> WalkState:
        event_time = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(
            minutes=idx
        )
        return WalkState(
            id=f"w{idx}",
            level=1,
            kind="consolidation",
            start_index=idx * 10,
            end_index=idx * 10 + 9,
            high=high,
            low=low,
            event_time=event_time,
            available_time=event_time,
            center_ids=[f"c{idx}"],
            status="confirmed",
            move_direction=direction,  # type: ignore[arg-type]
        )

    def test_structural_seed_excludes_provisional_segments(self) -> None:
        segments = [
            self._segment(0),
            self._segment(1),
            self._segment(2, status="provisional"),
        ]

        units = units_from_segments(segments)

        self.assertEqual(len(units), 2)
        self.assertTrue(all(item.kind == "segment" for item in units))
        self.assertTrue(all(item.status == "confirmed" for item in units))

    def test_parent_expansion_consumes_each_child_once(self) -> None:
        centers = [
            self._center(0, zd=10, zg=12, dd=8, gg=14),
            self._center(1, zd=15, zg=17, dd=13, gg=19),
            self._center(2, zd=20, zg=22, dd=14, gg=24),
            self._center(3, zd=25, zg=27, dd=14, gg=29),
        ]

        parents = build_parent_centers_from_expansions(
            centers,
            allow_extension=True,
        )

        self.assertEqual(len(parents), 1)
        self.assertEqual(
            parents[0].source_unit_ids,
            ["c0", "c1", "c2", "c3"],
        )
        self.assertEqual(len(set(parents[0].source_unit_ids)), 4)

    def test_selected_level_disables_parent_extension(self) -> None:
        centers = [
            self._center(0, zd=10, zg=12, dd=8, gg=14),
            self._center(1, zd=15, zg=17, dd=13, gg=19),
            self._center(2, zd=20, zg=22, dd=14, gg=24),
            self._center(3, zd=25, zg=27, dd=14, gg=29),
        ]

        parents = build_parent_centers_from_expansions(
            centers,
            allow_extension=False,
        )

        self.assertEqual(len(parents), 2)
        self.assertEqual(parents[0].source_unit_ids, ["c0", "c1"])
        self.assertEqual(parents[1].source_unit_ids, ["c2", "c3"])
        self.assertTrue(
            set(parents[0].source_unit_ids).isdisjoint(
                parents[1].source_unit_ids
            )
        )

    def test_right_confirmed_walks_are_non_overlapping_and_omit_open_run(
        self,
    ) -> None:
        centers = [
            self._center(0, zd=10, zg=12, dd=9, gg=13),
            self._center(1, zd=15, zg=17, dd=14, gg=18),
            self._center(2, zd=7, zg=9, dd=6, gg=10),
            self._center(3, zd=16, zg=18, dd=15, gg=19),
        ]

        walks = build_right_confirmed_walks_from_centers(centers)

        self.assertEqual(len(walks), 2)
        self.assertEqual([item.direction for item in walks], ["up", "down"])
        self.assertEqual(walks[0].center_ids, ["c0"])
        self.assertEqual(walks[1].center_ids, ["c1"])
        self.assertTrue(
            set(walks[0].center_ids).isdisjoint(walks[1].center_ids)
        )
        self.assertEqual(
            walks[0].available_time,
            centers[2].zhongshu.available_time,
        )
        self.assertEqual(
            walks[1].available_time,
            centers[3].zhongshu.available_time,
        )
        self.assertNotIn("c2", {item for walk in walks for item in walk.center_ids})
        self.assertEqual(walks[0].to_unit().direction, "up")

    def test_strict_weak_second_buy_is_recorded_but_not_executable(self) -> None:
        first_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candidate = DivergenceCandidate(
            signal_type="B1",
            mode="trend",
            confidence=0.61,
            trigger="B1",
            invalid_if="below B1",
            invalid_price=100.0,
            event_time=first_time,
            available_time=first_time,
            anchor_center_start_index=1,
            anchor_center_end_index=9,
            level=2,
        )
        walks = [
            self._walk(1, "up", low=100.0, high=120.0),
            self._walk(2, "down", low=99.0, high=115.0),
        ]

        signals = generate_signals(
            divergence_candidates=[candidate],
            bis_sub=[],
            segments_sub=[],
            zhongshu_main=None,
            market_state=MarketState(trend_type="down", phase="trending"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            structural_walks=walks,
            strict_mode=True,
            structure_level=2,
        )

        b2 = next(item for item in signals if item.type == "B2")
        self.assertFalse(b2.executable)
        self.assertEqual(b2.confidence, 1.0)
        self.assertIn("B2:weak", b2.structure_path)
        self.assertEqual(b2.invalid_price, 99.0)

    def test_strict_third_buy_uses_completed_structural_walks(self) -> None:
        center_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        center = Zhongshu(
            zd=100.0,
            zg=110.0,
            dd=95.0,
            gg=115.0,
            g=110.0,
            d=100.0,
            start_index=0,
            end_index=9,
            event_time=center_time,
            available_time=center_time,
        )
        walks = [
            self._walk(1, "up", low=108.0, high=125.0),
            self._walk(2, "down", low=110.0, high=122.0),
        ]

        signals = generate_signals(
            divergence_candidates=[],
            bis_sub=[],
            segments_sub=[],
            zhongshu_main=center,
            market_state=MarketState(trend_type="up", phase="trending"),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            structural_walks=walks,
            strict_mode=True,
            structure_level=2,
        )

        b3 = next(item for item in signals if item.type == "B3")
        self.assertTrue(b3.executable)
        self.assertEqual(b3.invalid_price, 110.0)
        self.assertEqual(b3.source_level, 2)

    def test_structural_levels_start_from_confirmed_1m_segments(self) -> None:
        segments = [self._segment(idx) for idx in range(3)]

        levels = build_structural_levels_from_segments(segments)

        self.assertEqual(len(levels), 1)
        self.assertEqual(levels[0].level, 0)
        self.assertEqual(levels[0].timeframe, "structural:1m")
        self.assertEqual(len(levels[0].units), 3)
        self.assertEqual(len(levels[0].centers), 1)

    def test_overlapping_walk_snapshots_are_not_promoted(self) -> None:
        ranges = [
            (10, 20),
            (14, 20),
            (15, 24),
            (18, 28),
            (30, 40),
            (32, 38),
            (33, 42),
            (36, 46),
            (50, 60),
            (52, 58),
            (53, 62),
        ]
        units = [
            self._unit(
                idx,
                "up" if idx % 2 == 0 else "down",
                low,
                high,
            )
            for idx, (low, high) in enumerate(ranges)
        ]

        levels = build_recursive_levels_from_units(
            units,
            timeframe="30m",
            max_depth=3,
        )

        self.assertEqual(len(levels), 1)
        self.assertGreaterEqual(len(levels[0].walks), 2)

    def test_build_chan_state_exposes_recursive_levels(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(
            start=start,
            count=360,
            step_hours=4,
            drift=2.0,
            wave_amp=320.0,
        )
        bars_sub = make_synthetic_bars(
            start=start,
            count=1440,
            step_hours=1,
            drift=0.6,
            wave_amp=90.0,
        )

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
        )

        self.assertTrue(snapshot.structure_levels_main)
        self.assertEqual(len(snapshot.structure_levels_main), 1)
        base = snapshot.structure_levels_main[0]
        self.assertEqual(base.level, 0)
        self.assertTrue(base.units)
        self.assertTrue(base.centers or base.walks)

        payload = generate_signal(snapshot).to_contract_dict()
        market_state = payload["market_state"]
        self.assertIn("current_walk", market_state)
        self.assertIn("level_states", market_state)
        self.assertTrue(market_state["level_states"])
        self.assertEqual(market_state["level_states"][0]["level"], 0)

    def test_strict_recursive_mode_requires_asof_structural_view(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(
            start=start,
            count=60,
            step_hours=4,
        )
        bars_sub = make_synthetic_bars(
            start=start,
            count=240,
            step_hours=1,
        )

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
            chan_config=get_chan_config("strict_recursive"),
        )

        self.assertEqual(snapshot.data_quality.status, "insufficient")
        self.assertIn("缺少 1m 结构回放", snapshot.data_quality.notes)

    def test_strict_recursive_mode_uses_target_level_center(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(
            start=start,
            count=60,
            step_hours=4,
        )
        bars_sub = make_synthetic_bars(
            start=start,
            count=240,
            step_hours=1,
        )
        asof = bars_main[-1].time
        target_center = self._center(
            20,
            zd=100,
            zg=110,
            dd=90,
            gg=120,
        )
        target_center.level = 2
        target_center.zhongshu.event_time = asof
        target_center.zhongshu.available_time = asof
        target_center.zhongshu.origin_available_time = asof
        structural_view = StructuralView(
            asof_time=asof,
            bis=[],
            segments=[],
            levels=[
                ChanLevel(
                    level=2,
                    timeframe="structural:30m",
                    centers=[target_center],
                )
            ],
        )

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=asof,
            chan_config=get_chan_config("strict_recursive"),
            structural_view=structural_view,
        )

        self.assertEqual(snapshot.data_quality.status, "ok")
        self.assertEqual(snapshot.zhongshus_main, [target_center.zhongshu])
        self.assertEqual(
            snapshot.structure_levels_main[-1].timeframe,
            "structural:30m",
        )


if __name__ == "__main__":
    unittest.main()
