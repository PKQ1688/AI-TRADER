from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["strict_kline8", "orthodox_chan", "pragmatic"]


@dataclass(slots=True)
class ChanConfig:
    mode: Mode = "orthodox_chan"
    min_main_bars: int = 50
    min_sub_bars: int = 100
    min_stroke_bars: int = 5
    allow_equal_fractal: bool = False
    require_case2_confirmation: bool = True
    divergence_threshold: float = 0.10
    min_confidence: float = 0.60
    transitional_confidence_cap: float = 0.60
    missing_macd_penalty: float = 0.10
    execution_buy_types: tuple[str, ...] = ("B2", "B3")
    execution_reduce_types: tuple[str, ...] = ("S2", "S3")
    execution_sell_types: tuple[str, ...] = ()
    execution_buy_min_confidence: float = 0.65
    execution_reduce_min_confidence: float = 0.65
    require_non_high_conflict_buy: bool = True
    reduce_only_on_high_conflict: bool = True
    prefer_first_class_signals: bool = False
    require_sub_interval_confirmation: bool = True
    include_consolidation_divergence_hint: bool = False


STRICT_KLINE8 = ChanConfig(
    require_sub_interval_confirmation=False,
)
ORTHODOX_CHAN = ChanConfig(
    mode="orthodox_chan",
    execution_buy_types=("B2", "B3"),
    execution_reduce_types=("S2", "S3"),
    execution_sell_types=(),
    require_sub_interval_confirmation=False,
    include_consolidation_divergence_hint=False,
)
PRAGMATIC = ChanConfig(
    mode="pragmatic",
    min_main_bars=40,
    min_sub_bars=80,
    min_stroke_bars=4,
    allow_equal_fractal=True,
    require_case2_confirmation=False,
    divergence_threshold=0.12,
    min_confidence=0.55,
    execution_buy_types=("B2", "B3"),
    execution_reduce_types=("S2", "S3"),
    execution_sell_types=("S3",),
    execution_buy_min_confidence=0.60,
    execution_reduce_min_confidence=0.60,
    require_non_high_conflict_buy=False,
    reduce_only_on_high_conflict=False,
    require_sub_interval_confirmation=False,
    include_consolidation_divergence_hint=True,
)


def get_chan_config(mode: Mode = "orthodox_chan") -> ChanConfig:
    if mode == "strict_kline8":
        return STRICT_KLINE8
    if mode == "orthodox_chan":
        return ORTHODOX_CHAN
    return PRAGMATIC
