from __future__ import annotations

from ai_trader.types import Bi, MarketState, Segment, TrendType, Zhongshu


def infer_trend_type(last_price: float, bis: list[Bi], zhongshus: list[Zhongshu]) -> TrendType:
    if len(zhongshus) >= 2:
        prev, cur = zhongshus[-2], zhongshus[-1]
        if cur.dd > prev.gg:
            return "up"
        if cur.gg < prev.dd:
            return "down"

    if zhongshus and bis:
        last_bi = bis[-1]
        last_zs = zhongshus[-1]
        if last_price > last_zs.zg and last_bi.direction == "up":
            return "up"
        if last_price < last_zs.zd and last_bi.direction == "down":
            return "down"

    return "range"


def infer_market_state(
    bars_close: float,
    bis: list[Bi],
    segments: list[Segment],
    zhongshus: list[Zhongshu],
) -> MarketState:
    trend_type = infer_trend_type(bars_close, bis, zhongshus)
    walk_type = "trend" if len(zhongshus) >= 2 and trend_type in {"up", "down"} else "consolidation"

    phase = "trending" if walk_type == "trend" else "consolidating"
    if trend_type == "range" and len(segments) >= 2 and segments[-1].direction != segments[-2].direction:
        phase = "transitional"

    last_zs = zhongshus[-1] if zhongshus else None
    last_zs_payload = {
        "zd": float(last_zs.zd) if last_zs else 0.0,
        "zg": float(last_zs.zg) if last_zs else 0.0,
        "gg": float(last_zs.gg) if last_zs else 0.0,
        "dd": float(last_zs.dd) if last_zs else 0.0,
    }

    current_stroke_dir = bis[-1].direction if bis else "up"
    current_segment_dir = segments[-1].direction if segments else current_stroke_dir

    return MarketState(
        trend_type=trend_type,
        walk_type=walk_type,
        phase=phase,
        zhongshu_count=len(zhongshus),
        last_zhongshu=last_zs_payload,
        current_stroke_dir=current_stroke_dir,
        current_segment_dir=current_segment_dir,
    )
