from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

TrendType = Literal["up", "down", "range"]
WalkType = Literal["consolidation", "trend"]
ChanWalkKind = Literal["trend_up", "trend_down", "consolidation", "unknown"]
ChanUnitKind = Literal["bar", "fractal", "bi", "segment", "center", "walk"]
PhaseType = Literal["trending", "consolidating", "transitional"]
SignalType = Literal["B1", "B2", "B3", "S1", "S2", "S3"]
SignalLevel = Literal["main", "sub"]
DecisionType = Literal["buy", "sell", "reduce", "hold", "wait"]
ConflictLevel = Literal["none", "low", "high"]
StructureStatus = Literal["provisional", "confirmed"]
ZhongshuEvolution = Literal["newborn", "extension", "expansion"]
DivergenceMode = Literal["trend", "consolidation"]
ConsolidationOutcome = Literal["unknown", "return_to_center", "third_class", "expansion"]


def parse_utc_time(value: datetime | str | int | float) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        self.time = parse_utc_time(self.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": iso_utc(self.time),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(slots=True)
class MACDPoint:
    time: datetime
    dif: float
    dea: float
    hist: float

    def __post_init__(self) -> None:
        self.time = parse_utc_time(self.time)


@dataclass(slots=True)
class FundingRate:
    time: datetime
    rate: float
    mark_price: float = 0.0

    def __post_init__(self) -> None:
        self.time = parse_utc_time(self.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": iso_utc(self.time),
            "rate": self.rate,
            "mark_price": self.mark_price,
        }


@dataclass(slots=True)
class Fractal:
    kind: Literal["top", "bottom"]
    index: int
    price: float
    event_time: datetime
    available_time: datetime
    status: StructureStatus = "confirmed"

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)


@dataclass(slots=True)
class Bi:
    direction: Literal["up", "down"]
    start_index: int
    end_index: int
    start_price: float
    end_price: float
    event_time: datetime
    available_time: datetime
    status: StructureStatus = "confirmed"

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)

    @property
    def high(self) -> float:
        return max(self.start_price, self.end_price)

    @property
    def low(self) -> float:
        return min(self.start_price, self.end_price)


@dataclass(slots=True)
class Segment:
    direction: Literal["up", "down"]
    start_index: int
    end_index: int
    high: float
    low: float
    event_time: datetime
    available_time: datetime
    status: StructureStatus = "confirmed"

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)


@dataclass(slots=True)
class Zhongshu:
    zd: float
    zg: float
    start_index: int
    end_index: int
    event_time: datetime
    available_time: datetime
    origin_available_time: datetime | None = None
    gg: float = 0.0
    dd: float = 0.0
    g: float = 0.0
    d: float = 0.0
    evolution: ZhongshuEvolution = "newborn"
    status: StructureStatus = "confirmed"

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)
        if self.origin_available_time is None:
            self.origin_available_time = self.available_time
        else:
            self.origin_available_time = parse_utc_time(self.origin_available_time)
        if self.gg == 0.0 and self.dd == 0.0:
            self.gg = self.zg
            self.dd = self.zd
        if self.g == 0.0 and self.d == 0.0:
            self.g = self.zg
            self.d = self.zd


@dataclass(slots=True)
class StructureUnit:
    """A recursive Chan structure unit.

    At the lowest tradable level this can represent a Bi. At higher levels it
    represents a completed lower-level walk, so the same center/walk builders
    can be reused recursively.
    """

    id: str
    level: int
    kind: ChanUnitKind
    direction: Literal["up", "down", "none"]
    start_index: int
    end_index: int
    high: float
    low: float
    start_price: float
    end_price: float
    event_time: datetime
    available_time: datetime
    status: StructureStatus = "confirmed"
    source_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "kind": self.kind,
            "direction": self.direction,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "high": self.high,
            "low": self.low,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "event_time": iso_utc(self.event_time),
            "available_time": iso_utc(self.available_time),
            "status": self.status,
            "source_ids": list(self.source_ids),
        }


@dataclass(slots=True)
class CenterState:
    id: str
    level: int
    zhongshu: Zhongshu
    source_unit_ids: list[str]
    evolution: ZhongshuEvolution = "newborn"
    parent_center_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "zd": self.zhongshu.zd,
            "zg": self.zhongshu.zg,
            "gg": self.zhongshu.gg,
            "dd": self.zhongshu.dd,
            "g": self.zhongshu.g,
            "d": self.zhongshu.d,
            "start_index": self.zhongshu.start_index,
            "end_index": self.zhongshu.end_index,
            "event_time": iso_utc(self.zhongshu.event_time),
            "available_time": iso_utc(self.zhongshu.available_time),
            "origin_available_time": iso_utc(self.zhongshu.origin_available_time),
            "evolution": self.evolution,
            "status": self.zhongshu.status,
            "source_unit_ids": list(self.source_unit_ids),
            "parent_center_id": self.parent_center_id,
        }


@dataclass(slots=True)
class WalkState:
    id: str
    level: int
    kind: ChanWalkKind
    start_index: int
    end_index: int
    high: float
    low: float
    event_time: datetime
    available_time: datetime
    center_ids: list[str] = field(default_factory=list)
    source_unit_ids: list[str] = field(default_factory=list)
    status: StructureStatus = "confirmed"
    # A one-center walk is a consolidation by structure, but it still connects
    # two prices in an up/down direction.  Keep that connection direction
    # explicit so it can serve as a lower-level unit in strict recursion.
    move_direction: Literal["up", "down", "none"] = "none"

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)

    @property
    def direction(self) -> Literal["up", "down", "none"]:
        if self.move_direction != "none":
            return self.move_direction
        if self.kind == "trend_up":
            return "up"
        if self.kind == "trend_down":
            return "down"
        return "none"

    def to_unit(self) -> StructureUnit:
        direction = self.direction
        if direction == "up":
            start_price, end_price = self.low, self.high
        elif direction == "down":
            start_price, end_price = self.high, self.low
        else:
            start_price = end_price = (self.high + self.low) / 2
        return StructureUnit(
            id=self.id,
            level=self.level + 1,
            kind="walk",
            direction=direction,
            start_index=self.start_index,
            end_index=self.end_index,
            high=self.high,
            low=self.low,
            start_price=start_price,
            end_price=end_price,
            event_time=self.event_time,
            available_time=self.available_time,
            status=self.status,
            source_ids=list(self.source_unit_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "kind": self.kind,
            "direction": self.direction,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "high": self.high,
            "low": self.low,
            "event_time": iso_utc(self.event_time),
            "available_time": iso_utc(self.available_time),
            "center_ids": list(self.center_ids),
            "source_unit_ids": list(self.source_unit_ids),
            "status": self.status,
        }


@dataclass(slots=True)
class ChanLevel:
    level: int
    timeframe: str
    units: list[StructureUnit] = field(default_factory=list)
    centers: list[CenterState] = field(default_factory=list)
    walks: list[WalkState] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "timeframe": self.timeframe,
            "units": [item.to_dict() for item in self.units],
            "centers": [item.to_dict() for item in self.centers],
            "walks": [item.to_dict() for item in self.walks],
        }


@dataclass(slots=True)
class DivergenceState:
    mode: DivergenceMode
    direction: Literal["up", "down"]
    signal_type: SignalType | None
    level: int
    confidence: float
    weaken_ratio: float
    trigger: str
    invalid_if: str
    invalid_price: float
    event_time: datetime
    available_time: datetime
    anchor_center_id: str | None = None
    anchor_center_start_index: int | None = None
    anchor_center_end_index: int | None = None
    anchor_center_available_time: datetime | None = None
    outcome: ConsolidationOutcome = "unknown"

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)
        if self.anchor_center_available_time is not None:
            self.anchor_center_available_time = parse_utc_time(
                self.anchor_center_available_time
            )
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "direction": self.direction,
            "signal_type": self.signal_type,
            "level": self.level,
            "confidence": self.confidence,
            "weaken_ratio": self.weaken_ratio,
            "trigger": self.trigger,
            "invalid_if": self.invalid_if,
            "invalid_price": self.invalid_price,
            "event_time": iso_utc(self.event_time),
            "available_time": iso_utc(self.available_time),
            "anchor_center_id": self.anchor_center_id,
            "anchor_center_start_index": self.anchor_center_start_index,
            "anchor_center_end_index": self.anchor_center_end_index,
            "anchor_center_available_time": (
                iso_utc(self.anchor_center_available_time)
                if self.anchor_center_available_time is not None
                else None
            ),
            "outcome": self.outcome,
        }


@dataclass(slots=True)
class BuySellPoint:
    type: SignalType
    level: int
    source: Literal["trend_divergence", "second_class", "third_class", "policy"]
    signal: Signal
    center_id: str | None = None
    divergence: DivergenceState | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "level": self.level,
            "source": self.source,
            "center_id": self.center_id,
            "signal": self.signal.to_contract_dict(),
            "divergence": self.divergence.to_dict() if self.divergence else None,
        }


@dataclass(slots=True)
class Signal:
    type: SignalType
    level: SignalLevel
    trigger: str
    invalid_if: str
    confidence: float
    event_time: datetime
    available_time: datetime
    invalid_price: float | None = None
    anchor_center_start_index: int | None = None
    anchor_center_end_index: int | None = None
    anchor_center_available_time: datetime | None = None
    source_level: int = 0
    source: str = "legacy"
    anchor_center_id: str | None = None
    divergence_mode: str | None = None
    structure_path: list[str] = field(default_factory=list)
    executable: bool = True

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)
        if self.anchor_center_available_time is not None:
            self.anchor_center_available_time = parse_utc_time(
                self.anchor_center_available_time
            )
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_contract_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "level": self.level,
            "trigger": self.trigger,
            "invalid_if": self.invalid_if,
            "confidence": self.confidence,
            "event_time": iso_utc(self.event_time),
            "available_time": iso_utc(self.available_time),
            "invalid_price": self.invalid_price,
            "source_level": self.source_level,
            "source": self.source,
            "anchor_center_id": self.anchor_center_id,
            "anchor_center_start_index": self.anchor_center_start_index,
            "anchor_center_end_index": self.anchor_center_end_index,
            "anchor_center_available_time": (
                iso_utc(self.anchor_center_available_time)
                if self.anchor_center_available_time is not None
                else None
            ),
            "divergence_mode": self.divergence_mode,
            "structure_path": list(self.structure_path),
            "executable": self.executable,
        }


@dataclass(slots=True)
class DataQuality:
    status: Literal["ok", "insufficient"]
    notes: str = ""


@dataclass(slots=True)
class MarketState:
    trend_type: TrendType
    walk_type: WalkType = "consolidation"
    phase: PhaseType = "consolidating"
    zhongshu_count: int = 0
    last_zhongshu: dict[str, float] = field(default_factory=lambda: {"zd": 0.0, "zg": 0.0, "gg": 0.0, "dd": 0.0})
    current_stroke_dir: Literal["up", "down"] = "up"
    current_segment_dir: Literal["up", "down"] = "up"
    current_walk: dict[str, Any] = field(
        default_factory=lambda: {
            "id": None,
            "level": 0,
            "kind": "unknown",
            "status": "provisional",
            "missing_sub_walks": 3,
            "possible_next": ["consolidation", "trend_up", "trend_down"],
        }
    )
    level_states: list[dict[str, Any]] = field(default_factory=list)
    oscillation_state: dict[str, Any] = field(
        default_factory=lambda: {
            "anchor_source": "none",
            "anchor_start_index": -1,
            "z": 0.0,
            "latest_zn": 0.0,
            "count": 0,
            "total_count": 0,
            "bias": "none",
            "direction": "none",
            "breakout": "none",
            "first_breakout": False,
            "limit_reached": False,
        }
    )


@dataclass(slots=True)
class Action:
    decision: DecisionType
    reason: str


@dataclass(slots=True)
class Risk:
    conflict_level: ConflictLevel
    notes: str


@dataclass(slots=True)
class SignalDecision:
    exchange: str
    symbol: str
    timeframe_main: str
    timeframe_sub: str
    data_quality: DataQuality
    market_state: MarketState
    signals: list[Signal]
    action: Action
    risk: Risk
    cn_summary: str
    divergences: list[DivergenceState] = field(default_factory=list)
    buy_sell_points: list[BuySellPoint] = field(default_factory=list)

    def to_contract_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe_main": self.timeframe_main,
            "timeframe_sub": self.timeframe_sub,
            "data_quality": asdict(self.data_quality),
            "market_state": {
                "trend_type": self.market_state.trend_type,
                "walk_type": self.market_state.walk_type,
                "phase": self.market_state.phase,
                "zhongshu_count": self.market_state.zhongshu_count,
                "last_zhongshu": self.market_state.last_zhongshu,
                "current_stroke_dir": self.market_state.current_stroke_dir,
                "current_segment_dir": self.market_state.current_segment_dir,
                "current_walk": self.market_state.current_walk,
                "level_states": self.market_state.level_states,
                "oscillation_state": self.market_state.oscillation_state,
            },
            "signals": [item.to_contract_dict() for item in self.signals],
            "divergences": [item.to_dict() for item in self.divergences],
            "buy_sell_points": [item.to_dict() for item in self.buy_sell_points],
            "action": asdict(self.action),
            "risk": asdict(self.risk),
            "cn_summary": self.cn_summary,
        }


@dataclass(slots=True)
class ChanSnapshot:
    exchange: str
    symbol: str
    timeframe_main: str
    timeframe_sub: str
    asof_time: datetime
    bars_main: list[Bar]
    bars_sub: list[Bar]
    macd_main: list[MACDPoint]
    macd_sub: list[MACDPoint]
    fractals_main: list[Fractal]
    fractals_sub: list[Fractal]
    bis_main: list[Bi]
    bis_sub: list[Bi]
    segments_main: list[Segment]
    segments_sub: list[Segment]
    previous_main_bar_time: datetime | None = None
    zhongshus_main: list[Zhongshu] = field(default_factory=list)
    zhongshus_sub: list[Zhongshu] = field(default_factory=list)
    last_zhongshu_main: Zhongshu | None = None
    trend_type_main: TrendType = "range"
    market_state_main: MarketState | None = None
    data_quality: DataQuality = field(default_factory=lambda: DataQuality(status="insufficient", notes=""))
    structure_levels_main: list[ChanLevel] = field(default_factory=list)
    structure_levels_sub: list[ChanLevel] = field(default_factory=list)
    divergences_main: list[DivergenceState] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.asof_time = parse_utc_time(self.asof_time)
        if self.previous_main_bar_time is not None:
            self.previous_main_bar_time = parse_utc_time(self.previous_main_bar_time)


@dataclass(slots=True)
class Trade:
    side: Literal["long", "short"]
    signal_type: SignalType
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    net_pnl: float
    net_return: float
    fees: float
    slippage_cost: float
    forward_3bar_return: float
    benchmark_return: float
    funding_pnl: float = 0.0
    exit_reason: str = "signal"
    signal_event_time: datetime | None = None
    signal_available_time: datetime | None = None
    invalid_price: float | None = None
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    mfe_time: datetime | None = None
    mae_time: datetime | None = None
    mfe_price: float | None = None
    mae_price: float | None = None

    def __post_init__(self) -> None:
        self.entry_time = parse_utc_time(self.entry_time)
        self.exit_time = parse_utc_time(self.exit_time)
        if self.signal_event_time is not None:
            self.signal_event_time = parse_utc_time(self.signal_event_time)
        if self.signal_available_time is not None:
            self.signal_available_time = parse_utc_time(
                self.signal_available_time
            )
        if self.mfe_time is not None:
            self.mfe_time = parse_utc_time(self.mfe_time)
        if self.mae_time is not None:
            self.mae_time = parse_utc_time(self.mae_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "signal_type": self.signal_type,
            "entry_time": iso_utc(self.entry_time),
            "exit_time": iso_utc(self.exit_time),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "net_return": self.net_return,
            "fees": self.fees,
            "slippage_cost": self.slippage_cost,
            "forward_3bar_return": self.forward_3bar_return,
            "benchmark_return": self.benchmark_return,
            "funding_pnl": self.funding_pnl,
            "exit_reason": self.exit_reason,
            "holding_hours": (
                self.exit_time - self.entry_time
            ).total_seconds()
            / 3600,
            "signal_event_time": (
                iso_utc(self.signal_event_time)
                if self.signal_event_time is not None
                else None
            ),
            "signal_available_time": (
                iso_utc(self.signal_available_time)
                if self.signal_available_time is not None
                else None
            ),
            "confirmation_lag_hours": (
                (
                    self.signal_available_time - self.signal_event_time
                ).total_seconds()
                / 3600
                if self.signal_event_time is not None
                and self.signal_available_time is not None
                else None
            ),
            "invalid_price": self.invalid_price,
            "max_favorable_excursion": self.max_favorable_excursion,
            "max_adverse_excursion": self.max_adverse_excursion,
            "mfe_time": (
                iso_utc(self.mfe_time) if self.mfe_time is not None else None
            ),
            "mae_time": (
                iso_utc(self.mae_time) if self.mae_time is not None else None
            ),
            "mfe_price": self.mfe_price,
            "mae_price": self.mae_price,
        }


@dataclass(slots=True)
class EquityPoint:
    time: datetime
    equity: float
    drawdown: float
    cash: float
    position_value: float

    def __post_init__(self) -> None:
        self.time = parse_utc_time(self.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": iso_utc(self.time),
            "equity": self.equity,
            "drawdown": self.drawdown,
            "cash": self.cash,
            "position_value": self.position_value,
        }


@dataclass(slots=True)
class SignificanceReport:
    benchmark: str
    sample_size: int
    observed_mean: float
    benchmark_mean: float
    mean_diff: float
    p_value: float
    ci_low: float
    ci_high: float
    test_method: str = "paired_moving_block_sign_flip_one_sided"
    confidence_method: str = "paired_moving_block_bootstrap_percentile"
    block_size: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_STRUCTURE_LOOKBACK_MAIN_BARS = 720
DEFAULT_STRUCTURE_LOOKBACK_SUB_BARS = 2880


@dataclass(slots=True)
class BacktestConfig:
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe_main: str = "4h"
    timeframe_sub: str = "1h"
    chan_mode: Literal[
        "strict_recursive",
        "strict_kline8",
        "orthodox_chan",
        "pragmatic",
    ] = "orthodox_chan"
    start_utc: str = "2022-02-10T00:00:00Z"
    end_utc: str = "2026-02-10T00:00:00Z"
    history_prefetch_days: int = 365
    initial_capital: float = 100000.0
    fee_rate: float = 0.001
    slippage_rate: float = 0.0002
    macd_divergence_threshold: float = 0.10
    min_confidence: float = 0.60
    drawdown_reduce_threshold: float = 0.12
    drawdown_freeze_threshold: float = 0.18
    freeze_recovery_days: int = 21
    reduce_ratio: float = 0.50
    allow_short_entries: bool = True
    benchmark: str = "year_matched_random_3bar"
    random_seed: int = 7
    # 0 means full-history structure rebuild; keep normal backtests bounded.
    structure_lookback_main_bars: int = DEFAULT_STRUCTURE_LOOKBACK_MAIN_BARS
    structure_lookback_sub_bars: int = DEFAULT_STRUCTURE_LOOKBACK_SUB_BARS
    check_signal_repaint: bool = False
    repaint_check_stride: int = 1
    liquidate_at_end: bool = False
    invalidation_mode: Literal["intrabar", "close"] = "intrabar"
    reversal_cooldown_bars: int = 0
    execution_timeframe: str | None = None

    def __post_init__(self) -> None:
        if self.invalidation_mode not in {"intrabar", "close"}:
            raise ValueError(
                "invalidation_mode must be 'intrabar' or 'close'"
            )
        if self.reversal_cooldown_bars < 0:
            raise ValueError("reversal_cooldown_bars must be >= 0")


@dataclass(slots=True)
class BacktestReport:
    config: BacktestConfig
    metrics: dict[str, Any]
    segmented_metrics: dict[str, dict[str, Any]]
    walk_forward_metrics: dict[str, dict[str, Any]]
    significance: SignificanceReport
    pass_checks: dict[str, bool]
    fail_reasons: list[str]
    signal_repaint_rate: float
    trade_diagnostics: dict[str, Any] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "metrics": self.metrics,
            "segmented_metrics": self.segmented_metrics,
            "walk_forward_metrics": self.walk_forward_metrics,
            "significance": self.significance.to_dict(),
            "pass_checks": self.pass_checks,
            "fail_reasons": self.fail_reasons,
            "signal_repaint_rate": self.signal_repaint_rate,
            "trade_diagnostics": self.trade_diagnostics,
            "trades": [item.to_dict() for item in self.trades],
            "signals": self.signals,
            "equity_curve": [item.to_dict() for item in self.equity_curve],
        }
