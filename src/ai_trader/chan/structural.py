from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime

from ai_trader.chan.core.fractal import detect_fractals
from ai_trader.chan.core.include import merge_inclusions
from ai_trader.chan.core.recursive import build_structural_levels_from_segments
from ai_trader.chan.core.segment import build_segments
from ai_trader.chan.core.stroke import build_bis
from ai_trader.types import Bar, Bi, ChanLevel, Fractal, Segment, parse_utc_time


@dataclass(slots=True)
class StructuralSeed:
    """Full-history minimum-level structures with explicit availability."""

    timeframe: str
    bars: list[Bar]
    merged_bars: list[Bar]
    fractals: list[Fractal]
    bis: list[Bi]
    segments: list[Segment]


@dataclass(slots=True)
class StructuralView:
    asof_time: datetime
    bis: list[Bi]
    segments: list[Segment]
    levels: list[ChanLevel]

    def __post_init__(self) -> None:
        self.asof_time = parse_utc_time(self.asof_time)


def _confirmed_prefix(items: list[Bi] | list[Segment]):
    return [item for item in items if item.status == "confirmed"]


def _assert_monotonic_availability(items: list[Bi] | list[Segment]) -> None:
    for previous, current in zip(items, items[1:]):
        if current.available_time < previous.available_time:
            raise ValueError("confirmed structure availability must be monotonic")


def build_structural_seed(
    bars: list[Bar],
    *,
    timeframe: str = "1m",
    min_stroke_bars: int = 5,
    allow_equal_fractal: bool = False,
    require_case2_confirmation: bool = True,
) -> StructuralSeed:
    """Build the minimum-level structures once for later as-of replay."""
    merged = merge_inclusions(bars)
    fractals = detect_fractals(merged, allow_equal=allow_equal_fractal)
    bis = _confirmed_prefix(
        build_bis(fractals, merged, min_bars=min_stroke_bars)
    )
    segments = _confirmed_prefix(
        build_segments(
            bis,
            require_case2_confirmation=require_case2_confirmation,
        )
    )
    _assert_monotonic_availability(bis)
    _assert_monotonic_availability(segments)
    return StructuralSeed(
        timeframe=timeframe,
        bars=list(bars),
        merged_bars=merged,
        fractals=fractals,
        bis=bis,
        segments=segments,
    )


class StructuralReplay:
    """Monotonic as-of replay for a precomputed structural seed."""

    def __init__(
        self,
        seed: StructuralSeed,
        *,
        target_level: int = 2,
        level_names: tuple[str, ...] = ("1m", "5m", "30m"),
    ) -> None:
        self.seed = seed
        self.target_level = target_level
        self.level_names = level_names
        self._bi_times = [item.available_time for item in seed.bis]
        self._segment_times = [item.available_time for item in seed.segments]
        self._last_segment_cursor = -1
        self._last_levels: list[ChanLevel] = []

    def view_at(self, asof_time) -> StructuralView:
        asof = parse_utc_time(asof_time)
        bi_cursor = bisect_right(self._bi_times, asof)
        segment_cursor = bisect_right(self._segment_times, asof)

        if segment_cursor != self._last_segment_cursor:
            self._last_levels = build_structural_levels_from_segments(
                self.seed.segments[:segment_cursor],
                target_level=self.target_level,
                level_names=self.level_names,
            )
            self._last_segment_cursor = segment_cursor

        return StructuralView(
            asof_time=asof,
            bis=self.seed.bis[:bi_cursor],
            segments=self.seed.segments[:segment_cursor],
            levels=self._last_levels,
        )
