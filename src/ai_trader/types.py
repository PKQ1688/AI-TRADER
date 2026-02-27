from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

TrendType = Literal["up", "down", "range"]
WalkType = Literal["consolidation", "trend"]
PhaseType = Literal["trending", "consolidating", "transitional"]
SignalType = Literal["B1", "B2", "B3", "S1", "S2", "S3"]
SignalLevel = Literal["main", "sub"]
DecisionType = Literal["buy", "sell", "reduce", "hold", "wait"]
ConflictLevel = Literal["none", "low", "high"]
StructureStatus = Literal["provisional", "confirmed"]
ZhongshuEvolution = Literal["newborn", "extension", "expansion"]


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
    gg: float = 0.0
    dd: float = 0.0
    g: float = 0.0
    d: float = 0.0
    evolution: ZhongshuEvolution = "newborn"
    status: StructureStatus = "confirmed"

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)
        if self.gg == 0.0 and self.dd == 0.0:
            self.gg = self.zg
            self.dd = self.zd
        if self.g == 0.0 and self.d == 0.0:
            self.g = self.zg
            self.d = self.zd


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

    def __post_init__(self) -> None:
        self.event_time = parse_utc_time(self.event_time)
        self.available_time = parse_utc_time(self.available_time)
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_contract_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "level": self.level,
            "trigger": self.trigger,
            "invalid_if": self.invalid_if,
            "confidence": self.confidence,
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
            },
            "signals": [item.to_contract_dict() for item in self.signals],
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
    zhongshus_main: list[Zhongshu] = field(default_factory=list)
    zhongshus_sub: list[Zhongshu] = field(default_factory=list)
    last_zhongshu_main: Zhongshu | None = None
    trend_type_main: TrendType = "range"
    market_state_main: MarketState | None = None
    data_quality: DataQuality = field(default_factory=lambda: DataQuality(status="insufficient", notes=""))

    def __post_init__(self) -> None:
        self.asof_time = parse_utc_time(self.asof_time)


@dataclass(slots=True)
class Trade:
    side: Literal["long"]
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

    def __post_init__(self) -> None:
        self.entry_time = parse_utc_time(self.entry_time)
        self.exit_time = parse_utc_time(self.exit_time)

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BacktestConfig:
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe_main: str = "4h"
    timeframe_sub: str = "1h"
    chan_mode: Literal["strict_kline8", "pragmatic"] = "strict_kline8"
    start_utc: str = "2022-02-10T00:00:00Z"
    end_utc: str = "2026-02-10T00:00:00Z"
    initial_capital: float = 100000.0
    fee_rate: float = 0.001
    slippage_rate: float = 0.0002
    macd_divergence_threshold: float = 0.10
    min_confidence: float = 0.60
    drawdown_reduce_threshold: float = 0.12
    drawdown_freeze_threshold: float = 0.18
    freeze_recovery_days: int = 21
    reduce_ratio: float = 0.50
    benchmark: str = "time_matched_random"
    random_seed: int = 7


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
            "trades": [item.to_dict() for item in self.trades],
            "signals": self.signals,
            "equity_curve": [item.to_dict() for item in self.equity_curve],
        }
