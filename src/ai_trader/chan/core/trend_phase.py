from __future__ import annotations

from ai_trader.types import Bi, MarketState, Segment, TrendType, Zhongshu


def _segment_overlaps_center(segment: Segment, zhongshu: Zhongshu) -> bool:
    return segment.high >= zhongshu.zd and segment.low <= zhongshu.zg


def _build_oscillation_state(
    segments: list[Segment],
    center: Zhongshu | None,
    anchor_source: str,
) -> dict[str, float | int | str | bool]:
    if center is None:
        return {
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

    center_confirmed_at = center.origin_available_time or center.available_time
    oscillation_segments = [
        item
        for item in segments
        if item.status == "confirmed"
        and item.available_time >= center_confirmed_at
        and _segment_overlaps_center(item, center)
    ]
    tracked_segments = oscillation_segments[-9:]
    z_value = (center.zd + center.zg) / 2
    zn_values = [(item.high + item.low) / 2 for item in tracked_segments]
    latest_zn = zn_values[-1] if zn_values else z_value

    if not zn_values:
        bias = "none"
        direction = "none"
        breakout = "none"
        first_breakout = False
    else:
        if latest_zn > z_value:
            bias = "strong"
        elif latest_zn < z_value:
            bias = "weak"
        else:
            bias = "neutral"

        if len(zn_values) >= 2:
            if zn_values[-1] > zn_values[-2]:
                direction = "rising"
            elif zn_values[-1] < zn_values[-2]:
                direction = "falling"
            else:
                direction = "flat"
        else:
            direction = "flat"

        if latest_zn > center.zg:
            breakout = "above_zg"
        elif latest_zn < center.zd:
            breakout = "below_zd"
        else:
            breakout = "inside"

        earlier_breakouts = any(
            value > center.zg or value < center.zd for value in zn_values[:-1]
        )
        first_breakout = breakout in {"above_zg", "below_zd"} and not earlier_breakouts

    return {
        "anchor_source": anchor_source,
        "anchor_start_index": center.start_index,
        "z": float(z_value),
        "latest_zn": float(latest_zn),
        "count": len(tracked_segments),
        "total_count": len(oscillation_segments),
        "bias": bias,
        "direction": direction,
        "breakout": breakout,
        "first_breakout": first_breakout,
        "limit_reached": len(oscillation_segments) > 9,
    }


def find_latest_trend_center_pair(
    zhongshus: list[Zhongshu],
) -> tuple[TrendType, int, int] | None:
    for idx in range(len(zhongshus) - 1, 0, -1):
        prev, cur = zhongshus[idx - 1], zhongshus[idx]
        if cur.dd > prev.gg:
            return "up", idx - 1, idx
        if cur.gg < prev.dd:
            return "down", idx - 1, idx
    return None


def infer_trend_type(
    last_price: float, bis: list[Bi], zhongshus: list[Zhongshu]
) -> TrendType:
    del last_price, bis

    pair = find_latest_trend_center_pair(zhongshus)
    if pair is None:
        return "range"
    return pair[0]


def infer_market_state(
    bars_close: float,
    bis: list[Bi],
    segments: list[Segment],
    zhongshus: list[Zhongshu],
) -> MarketState:
    trend_pair = find_latest_trend_center_pair(zhongshus)
    trend_type = trend_pair[0] if trend_pair is not None else "range"
    walk_type = "consolidation"
    last_zs = zhongshus[-1] if zhongshus else None
    phase = "consolidating"
    oscillation_anchor = last_zs
    oscillation_anchor_source = "current_center" if last_zs is not None else "none"

    if trend_pair is not None:
        _, _, trend_cur_idx = trend_pair
        trend_tail_zs = zhongshus[trend_cur_idx]
        post_center_segments = [
            item
            for item in segments
            if item.status == "confirmed"
            and item.available_time > trend_tail_zs.available_time
        ]
        has_later_centers = trend_cur_idx < len(zhongshus) - 1
        has_transition_segments = (
            len(post_center_segments) >= 2
            and post_center_segments[-1].direction != post_center_segments[-2].direction
        )
        if has_later_centers or has_transition_segments:
            phase = "transitional"
            oscillation_anchor = trend_tail_zs
            oscillation_anchor_source = "prior_trend_center"
        else:
            walk_type = "trend"
            phase = "trending"
    elif last_zs is not None:
        post_center_segments = [
            item
            for item in segments
            if item.status == "confirmed" and item.available_time > last_zs.available_time
        ]
        if (
            len(post_center_segments) >= 2
            and post_center_segments[-1].direction != post_center_segments[-2].direction
        ):
            phase = "transitional"

    oscillation_state = _build_oscillation_state(
        segments=segments,
        center=oscillation_anchor,
        anchor_source=oscillation_anchor_source,
    )

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
        oscillation_state=oscillation_state,
    )
