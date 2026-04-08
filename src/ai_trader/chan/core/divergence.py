from __future__ import annotations

from dataclasses import dataclass

from ai_trader.types import Bi, MACDPoint, TrendType, Zhongshu


@dataclass(slots=True)
class DivergenceCandidate:
    signal_type: str
    mode: str
    confidence: float
    trigger: str
    invalid_if: str
    invalid_price: float
    event_time: object
    available_time: object
    anchor_center_start_index: int | None = None
    anchor_center_end_index: int | None = None
    anchor_center_available_time: object | None = None


# ---------------------------------------------------------------------------
# MACD helpers – faithful to kline8 lesson 24
# ---------------------------------------------------------------------------


def _macd_area_directed(
    macd: list[MACDPoint], start_time, end_time, direction: str
) -> float:
    """Sum MACD histogram bars that match *direction*.

    kline8-24: "向上的看红柱子，向下看绿柱子".
    For an up move we sum positive hist values; for a down move we sum
    the absolute value of negative hist values.
    """
    total = 0.0
    for pt in macd:
        if pt.time < start_time or pt.time > end_time:
            continue
        if direction == "up" and pt.hist > 0:
            total += pt.hist
        elif direction == "down" and pt.hist < 0:
            total += abs(pt.hist)
    return total


def _zero_axis_pullback(
    macd: list[MACDPoint], start_time, end_time, tolerance: float = 0.15
) -> bool:
    """Check that DIF (or DEA) returned close to the zero axis between
    two segments of the trend.

    kline8-24 & 25: "这个中枢一般会把MACD的黄白线回拉到0轴附近".
    We consider the pullback satisfied if either DIF or DEA crossed zero
    or came within *tolerance* fraction of the recent peak DIF amplitude.
    """
    points = [pt for pt in macd if start_time <= pt.time <= end_time]
    if not points:
        # If there are no MACD points in the gap we cannot verify – be lenient
        return True

    for pt in points:
        # Crossed zero or very close
        if abs(pt.dif) < 1e-9:
            return True
        if pt.dif * pt.dea < 0:
            # DIF and DEA on opposite sides of zero ⇒ crossed
            return True

    # Fallback: if the minimum |DIF| in the window is small relative to
    # the peak |DIF| in the surrounding MACD, treat as pullback.
    min_abs_dif = min(abs(pt.dif) for pt in points)
    all_before = [abs(pt.dif) for pt in macd if pt.time < start_time]
    if all_before:
        peak_dif = max(all_before[-50:]) if len(all_before) > 50 else max(all_before)
        if peak_dif > 0 and min_abs_dif / peak_dif <= tolerance:
            return True

    return False


# ---------------------------------------------------------------------------
# Helpers to locate a+A+b+B+c structure
# ---------------------------------------------------------------------------


def _center_member_span(
    bis: list[Bi], center: Zhongshu
) -> tuple[int, int] | None:
    indices = [
        idx
        for idx, bi in enumerate(bis)
        if bi.start_index >= center.start_index and bi.end_index <= center.end_index
    ]
    if not indices:
        return None
    return indices[0], indices[-1]


def _bi_overlaps_center(bi: Bi, center: Zhongshu) -> bool:
    return bi.high >= center.zd and bi.low <= center.zg


def _bi_leaves_center(bi: Bi, center: Zhongshu, direction: str) -> bool:
    if bi.direction != direction:
        return False
    if direction == "down":
        return bi.low < center.zd
    return bi.high > center.zg


def _trend_leg_before_center(
    bis: list[Bi],
    zhongshus: list[Zhongshu],
    center_idx: int,
    direction: str,
) -> list[Bi]:
    center_span = _center_member_span(bis, zhongshus[center_idx])
    if center_span is None:
        return []

    first_idx, _ = center_span
    if center_idx > 0:
        prev_span = _center_member_span(bis, zhongshus[center_idx - 1])
        if prev_span is None:
            return []
        start_idx = prev_span[1] + 1
    else:
        start_idx = -1
        for idx in range(first_idx - 1, -1, -1):
            if bis[idx].direction == direction:
                start_idx = idx
                break
        if start_idx < 0:
            return []

    if start_idx >= first_idx:
        return []
    return bis[start_idx:first_idx]


def _trend_leg_after_center(
    bis: list[Bi],
    zhongshus: list[Zhongshu],
    center_idx: int,
) -> list[Bi]:
    center_span = _center_member_span(bis, zhongshus[center_idx])
    if center_span is None:
        return []

    _, last_idx = center_span
    end_idx = len(bis)
    if center_idx + 1 < len(zhongshus):
        next_span = _center_member_span(bis, zhongshus[center_idx + 1])
        if next_span is None:
            return []
        end_idx = next_span[0]

    if last_idx + 1 >= end_idx:
        return []
    return bis[last_idx + 1 : end_idx]


def _find_trend_segments(
    bis: list[Bi],
    zhongshus: list[Zhongshu],
    direction: str,
) -> tuple[list[Bi], list[Bi], list[Bi], list[Bi], Zhongshu, Zhongshu] | None:
    """Try to identify a+A+b+B+c structure.

    For a downtrend direction="down":
      - Need at least 2 zhongshus with zg(later) < zd(earlier) (down trend)
      - a = the connector window before A (first zhongshu)
      - c = the connector window after B (second/last zhongshu)

    Windows are truncated by neighboring centers so c does not spill into
    later expansion / transitional structures.
    """
    if len(zhongshus) < 2:
        return None

    if direction == "down":
        # Find last pair of zhongshus forming downtrend (later.zg < earlier.zd)
        for j in range(len(zhongshus) - 1, 0, -1):
            B = zhongshus[j]
            A = zhongshus[j - 1]
            if B.zg < A.zd:
                a_window = _trend_leg_before_center(bis, zhongshus, j - 1, direction)
                c_window = _trend_leg_after_center(bis, zhongshus, j)
                a_dir = [bi for bi in a_window if bi.direction == direction]
                c_dir = [bi for bi in c_window if bi.direction == direction]
                if a_dir and c_dir:
                    return a_window, c_window, a_dir, c_dir, A, B
    else:
        for j in range(len(zhongshus) - 1, 0, -1):
            B = zhongshus[j]
            A = zhongshus[j - 1]
            if B.zd > A.zg:
                a_window = _trend_leg_before_center(bis, zhongshus, j - 1, direction)
                c_window = _trend_leg_after_center(bis, zhongshus, j)
                a_dir = [bi for bi in a_window if bi.direction == direction]
                c_dir = [bi for bi in c_window if bi.direction == direction]
                if a_dir and c_dir:
                    return a_window, c_window, a_dir, c_dir, A, B

    return None


# ---------------------------------------------------------------------------
# Main detection
# ---------------------------------------------------------------------------


def _latest_pair_same_direction(bis: list[Bi], direction: str) -> tuple[Bi, Bi] | None:
    seq = [item for item in bis if item.direction == direction]
    if len(seq) < 2:
        return None
    return seq[-2], seq[-1]


def _latest_consolidation_departure_pair(
    bis: list[Bi],
    center: Zhongshu,
    direction: str,
) -> tuple[Bi, Bi] | None:
    center_confirmed_at = center.origin_available_time or center.available_time
    confirmed = [
        item
        for item in bis
        if item.status == "confirmed" and item.available_time >= center_confirmed_at
    ]
    departures = [
        idx
        for idx, item in enumerate(confirmed)
        if _bi_leaves_center(item, center, direction)
    ]
    if len(departures) < 2:
        return None

    reentry_direction = "up" if direction == "down" else "down"
    for offset in range(len(departures) - 1, 0, -1):
        prev_idx = departures[offset - 1]
        cur_idx = departures[offset]
        between = confirmed[prev_idx + 1 : cur_idx]
        if not any(
            item.direction == reentry_direction and _bi_overlaps_center(item, center)
            for item in between
        ):
            continue
        return confirmed[prev_idx], confirmed[cur_idx]

    return None


def _build_candidate(
    direction: str,
    mode: str,
    cur_bi: Bi,
    weaken_ratio: float,
    anchor_center: Zhongshu,
) -> DivergenceCandidate:
    if direction == "down":
        signal_type = "B1"
        if mode == "trend":
            trigger = "主级别向下走势创新低但MACD柱子面积衰减（背驰候选）"
            invalid_if = f"价格继续跌破{cur_bi.end_price:.2f}并延续下行"
        else:
            trigger = "单中枢震荡中向下离开后再创新低但力度衰减（盘整背驰候选）"
            invalid_if = f"价格继续跌破{cur_bi.end_price:.2f}并延续离开中枢"
    else:
        signal_type = "S1"
        if mode == "trend":
            trigger = "主级别向上走势创新高但MACD柱子面积衰减（背驰候选）"
            invalid_if = f"价格继续突破{cur_bi.end_price:.2f}并延续上行"
        else:
            trigger = "单中枢震荡中向上离开后再创新高但力度衰减（盘整背驰候选）"
            invalid_if = f"价格继续突破{cur_bi.end_price:.2f}并延续离开中枢"

    base = 0.60 if mode == "trend" else 0.50
    confidence = max(0.0, min(1.0, base + min(0.25, weaken_ratio)))

    return DivergenceCandidate(
        signal_type=signal_type,
        mode=mode,
        confidence=confidence,
        trigger=trigger,
        invalid_if=invalid_if,
        invalid_price=cur_bi.end_price,
        event_time=cur_bi.event_time,
        available_time=cur_bi.available_time,
        anchor_center_start_index=anchor_center.start_index,
        anchor_center_end_index=anchor_center.end_index,
        anchor_center_available_time=anchor_center.origin_available_time
        or anchor_center.available_time,
    )


def detect_divergence_candidates(
    bis: list[Bi],
    zhongshu_count: int,
    trend_type: TrendType,
    macd: list[MACDPoint],
    threshold: float,
    zhongshus: list[Zhongshu] | None = None,
    allow_consolidation_fallback: bool = True,
    consolidation_anchor: Zhongshu | None = None,
) -> list[DivergenceCandidate]:
    """Detect trend divergence and consolidation divergence.

    Improvements over the previous version (aligned with kline8):
    1. MACD area only sums same-direction histogram bars (red for up, green for down).
    2. When >=2 zhongshus exist, tries to find a+A+b+B+c structure and
       compares the MACD area of a-segment vs c-segment.
    3. Requires DIF/DEA zero-axis pullback between segments as precondition
       for trend divergence.
    4. Falls back to restricted single-center oscillation divergence:
       two same-direction departures from one center with a re-entry
       to the center in between.
    """
    out: list[DivergenceCandidate] = []
    if zhongshus is None:
        zhongshus = []

    for direction in ("down", "up"):
        is_trend = (
            (direction == "down" and trend_type == "down")
            or (direction == "up" and trend_type == "up")
        ) and zhongshu_count >= 2

        # ----- Trend divergence via a+A+b+B+c -----
        if is_trend and len(zhongshus) >= 2:
            result = _find_trend_segments(bis, zhongshus, direction)
            if result is not None:
                a_window, c_window, a_dir_bis, c_dir_bis, A_zs, B_zs = result

                # Precondition: c must create new extreme beyond a
                if direction == "down":
                    a_extreme = min(bi.end_price for bi in a_dir_bis)
                    c_extreme = min(bi.end_price for bi in c_dir_bis)
                    price_new_extreme = c_extreme < a_extreme
                else:
                    a_extreme = max(bi.end_price for bi in a_dir_bis)
                    c_extreme = max(bi.end_price for bi in c_dir_bis)
                    price_new_extreme = c_extreme > a_extreme

                if price_new_extreme:
                    # Zero-axis pullback check in the gap (B region)
                    pullback_ok = _zero_axis_pullback(
                        macd, A_zs.available_time, B_zs.available_time
                    )

                    if pullback_ok:
                        a_area = _macd_area_directed(
                            macd, a_window[0].event_time, A_zs.available_time, direction
                        )
                        c_area = _macd_area_directed(
                            macd, B_zs.available_time, c_window[-1].available_time, direction
                        )

                        if a_area > 0:
                            weaken = (a_area - c_area) / a_area
                            if weaken >= threshold:
                                cur_bi = c_dir_bis[-1]
                                out.append(
                                    _build_candidate(
                                        direction,
                                        "trend",
                                        cur_bi,
                                        weaken,
                                        B_zs,
                                    )
                                )
                                continue  # found trend divergence, skip fallback

        if not allow_consolidation_fallback:
            continue

        # ----- Restricted single-center consolidation divergence -----
        center = consolidation_anchor or (zhongshus[-1] if zhongshus else None)
        if center is None:
            continue
        pair = _latest_consolidation_departure_pair(bis, center, direction)
        if pair is None:
            continue

        prev_bi, cur_bi = pair
        if direction == "down":
            price_extreme = cur_bi.end_price < prev_bi.end_price
        else:
            price_extreme = cur_bi.end_price > prev_bi.end_price

        if not price_extreme:
            continue

        prev_area = _macd_area_directed(
            macd, prev_bi.event_time, prev_bi.available_time, direction
        )
        cur_area = _macd_area_directed(
            macd, cur_bi.event_time, cur_bi.available_time, direction
        )

        if prev_area <= 0:
            continue

        weaken_ratio = (prev_area - cur_area) / prev_area
        if weaken_ratio < threshold:
            continue

        mode = "consolidation"
        out.append(_build_candidate(direction, mode, cur_bi, weaken_ratio, center))

    return out
