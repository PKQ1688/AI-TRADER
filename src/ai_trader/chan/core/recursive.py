from __future__ import annotations

from collections.abc import Sequence

from ai_trader.chan.core.center import classify_center_relation
from ai_trader.types import (
    Bi,
    CenterState,
    ChanLevel,
    ChanWalkKind,
    Segment,
    StructureUnit,
    WalkState,
    Zhongshu,
)


def units_from_bis(bis: list[Bi], level: int = 0) -> list[StructureUnit]:
    units: list[StructureUnit] = []
    for idx, bi in enumerate(bis):
        units.append(
            StructureUnit(
                id=f"L{level}:bi:{idx}:{bi.start_index}-{bi.end_index}",
                level=level,
                kind="bi",
                direction=bi.direction,
                start_index=bi.start_index,
                end_index=bi.end_index,
                high=bi.high,
                low=bi.low,
                start_price=bi.start_price,
                end_price=bi.end_price,
                event_time=bi.event_time,
                available_time=bi.available_time,
                status=bi.status,
                source_ids=[],
            )
        )
    return units


def units_from_segments(
    segments: list[Segment], level: int = 0
) -> list[StructureUnit]:
    """Use confirmed minimum-analysis-level segments as recursive units.

    Lesson 57 fixes the recursion seed by first choosing a minimum analysis
    level.  At that level, each completed segment is treated as a completed
    sub-level walk.  Provisional segments are deliberately excluded: their
    endpoints may still change and therefore cannot form a confirmed center.
    """
    units: list[StructureUnit] = []
    for idx, segment in enumerate(segments):
        if segment.status != "confirmed":
            continue
        if segment.direction == "up":
            start_price, end_price = segment.low, segment.high
        else:
            start_price, end_price = segment.high, segment.low
        units.append(
            StructureUnit(
                id=(
                    f"L{level}:segment:{idx}:"
                    f"{segment.start_index}-{segment.end_index}"
                ),
                level=level,
                kind="segment",
                direction=segment.direction,
                start_index=segment.start_index,
                end_index=segment.end_index,
                high=segment.high,
                low=segment.low,
                start_price=start_price,
                end_price=end_price,
                event_time=segment.event_time,
                available_time=segment.available_time,
                status="confirmed",
                source_ids=[],
            )
        )
    return units


def _center_from_three_units(
    u1: StructureUnit, u2: StructureUnit, u3: StructureUnit, center_id: str
) -> CenterState | None:
    if any(item.status != "confirmed" for item in (u1, u2, u3)):
        return None
    zd = max(u1.low, u2.low, u3.low)
    zg = min(u1.high, u2.high, u3.high)
    if zd > zg:
        return None

    highs = [u1.high, u2.high, u3.high]
    lows = [u1.low, u2.low, u3.low]
    available_time = max(u1.available_time, u2.available_time, u3.available_time)
    zhongshu = Zhongshu(
        zd=zd,
        zg=zg,
        start_index=u1.start_index,
        end_index=u3.end_index,
        event_time=u3.event_time,
        available_time=available_time,
        origin_available_time=available_time,
        gg=max(highs),
        dd=min(lows),
        g=min(highs),
        d=max(lows),
        evolution="newborn",
        status="confirmed",
    )
    return CenterState(
        id=center_id,
        level=u1.level,
        zhongshu=zhongshu,
        source_unit_ids=[u1.id, u2.id, u3.id],
        evolution="newborn",
    )


def _extend_center(center: CenterState, unit: StructureUnit) -> None:
    zs = center.zhongshu
    # A center's core interval is established by its first three completed
    # sub-level units.  An overlapping later unit extends the center without
    # redefining that original [ZD, ZG] interval.
    zs.gg = max(zs.gg, unit.high)
    zs.dd = min(zs.dd, unit.low)
    zs.g = min(zs.g, unit.high)
    zs.d = max(zs.d, unit.low)
    zs.end_index = unit.end_index
    zs.event_time = unit.event_time
    zs.available_time = max(zs.available_time, unit.available_time)
    zs.evolution = "extension"
    center.evolution = "extension"
    if unit.id not in center.source_unit_ids:
        center.source_unit_ids.append(unit.id)


def _append_center(out: list[CenterState], candidate: CenterState) -> None:
    if not out:
        out.append(candidate)
        return

    prev = out[-1]
    relation = classify_center_relation(prev.zhongshu, candidate.zhongshu)
    if relation == "extension":
        for unit_id in candidate.source_unit_ids:
            if unit_id not in prev.source_unit_ids:
                prev.source_unit_ids.append(unit_id)
        prev_zs = prev.zhongshu
        cand_zs = candidate.zhongshu
        prev_zs.gg = max(prev_zs.gg, cand_zs.gg)
        prev_zs.dd = min(prev_zs.dd, cand_zs.dd)
        prev_zs.g = min(prev_zs.g, cand_zs.g)
        prev_zs.d = max(prev_zs.d, cand_zs.d)
        prev_zs.end_index = cand_zs.end_index
        prev_zs.event_time = cand_zs.event_time
        prev_zs.available_time = cand_zs.available_time
        prev_zs.evolution = "extension"
        prev.evolution = "extension"
        return

    if relation == "expansion":
        candidate.evolution = "expansion"
        candidate.zhongshu.evolution = "expansion"
    out.append(candidate)


def build_centers_from_units(
    units: list[StructureUnit], *, allow_extension: bool = True
) -> list[CenterState]:
    out: list[CenterState] = []
    if len(units) < 3:
        return out

    i = 0
    while i + 2 < len(units):
        candidate = _center_from_three_units(
            units[i],
            units[i + 1],
            units[i + 2],
            center_id=f"L{units[i].level}:zs:{i}:{units[i].start_index}-{units[i + 2].end_index}",
        )
        if candidate is None:
            i += 1
            continue

        # u1/u3 are Z1/Z2.  The next extension candidate is the completed
        # same-direction Z3 at i+4, not the reverse connector at i+3.
        j = i + 4
        while allow_extension and j < len(units):
            z_unit = units[j]
            if z_unit.status != "confirmed":
                break
            if (
                z_unit.low > candidate.zhongshu.zg
                or z_unit.high < candidate.zhongshu.zd
            ):
                break
            connector = units[j - 1]
            if connector.id not in candidate.source_unit_ids:
                candidate.source_unit_ids.append(connector.id)
            _extend_center(candidate, z_unit)
            j += 2

        _append_center(out, candidate)
        if j < len(units):
            # Keep source units of consecutive centers disjoint.  The unit
            # immediately before j connects the completed center to the next
            # one; the first non-overlapping Z unit starts the new search.
            i = j
        else:
            break

    return out


def _unit_ids_between(units: Sequence[StructureUnit], start: int, end: int) -> list[str]:
    return [
        item.id
        for item in units
        if item.start_index >= start and item.end_index <= end
    ]


def _walk_kind_from_centers(prev: CenterState, cur: CenterState) -> ChanWalkKind:
    relation = classify_center_relation(prev.zhongshu, cur.zhongshu)
    if relation == "trend_up":
        return "trend_up"
    if relation == "trend_down":
        return "trend_down"
    return "consolidation"


def build_walks_from_centers(
    centers: list[CenterState], units: list[StructureUnit]
) -> list[WalkState]:
    if not centers:
        return []
    if len(centers) == 1:
        center = centers[0]
        zs = center.zhongshu
        return [
            WalkState(
                id=f"L{center.level}:walk:0:{zs.start_index}-{zs.end_index}",
                level=center.level,
                kind="consolidation",
                start_index=zs.start_index,
                end_index=zs.end_index,
                high=zs.gg,
                low=zs.dd,
                event_time=zs.event_time,
                available_time=zs.available_time,
                center_ids=[center.id],
                source_unit_ids=list(center.source_unit_ids),
                status=zs.status,
            )
        ]

    walks: list[WalkState] = []
    start_center_idx = 0
    for idx in range(1, len(centers)):
        prev = centers[idx - 1]
        cur = centers[idx]
        kind = _walk_kind_from_centers(prev, cur)
        if idx > start_center_idx + 1:
            prev_kind = walks[-1].kind if walks else None
            if kind != prev_kind:
                start_center_idx = idx - 1

        start = centers[start_center_idx].zhongshu.start_index
        end = cur.zhongshu.end_index
        window_units = [
            item for item in units if item.start_index >= start and item.end_index <= end
        ]
        if not window_units:
            window_units = units
        high = max(item.high for item in window_units)
        low = min(item.low for item in window_units)
        walks.append(
            WalkState(
                id=f"L{cur.level}:walk:{len(walks)}:{start}-{end}",
                level=cur.level,
                kind=kind,
                start_index=start,
                end_index=end,
                high=high,
                low=low,
                event_time=cur.zhongshu.event_time,
                available_time=cur.zhongshu.available_time,
                center_ids=[item.id for item in centers[start_center_idx : idx + 1]],
                source_unit_ids=_unit_ids_between(units, start, end),
                status="confirmed",
            )
        )
    return walks


def _center_move_direction(
    previous: CenterState,
    current: CenterState,
) -> tuple[str, bool]:
    """Return the connection direction and whether it is a strict trend move."""
    relation = classify_center_relation(previous.zhongshu, current.zhongshu)
    if relation == "trend_up":
        return "up", True
    if relation == "trend_down":
        return "down", True

    previous_mid = (previous.zhongshu.zd + previous.zhongshu.zg) / 2
    current_mid = (current.zhongshu.zd + current.zhongshu.zg) / 2
    if current_mid > previous_mid:
        return "up", False
    if current_mid < previous_mid:
        return "down", False

    previous_wave_mid = (previous.zhongshu.dd + previous.zhongshu.gg) / 2
    current_wave_mid = (current.zhongshu.dd + current.zhongshu.gg) / 2
    if current_wave_mid > previous_wave_mid:
        return "up", False
    if current_wave_mid < previous_wave_mid:
        return "down", False
    return "none", False


def build_right_confirmed_walks_from_centers(
    centers: list[CenterState],
) -> list[WalkState]:
    """Build non-overlapping completed walks with conservative right confirmation.

    A completed walk must contain at least one center.  A directional run is
    not emitted until an opposite run has itself established a center and its
    direction can be observed from the next completed center.  Consequently
    the last run is always omitted as unfinished.  Source centers are assigned
    to one walk only, preserving the uniqueness required by recursive
    construction.
    """
    if len(centers) < 3:
        return []

    # (first relation index, last relation index, direction, all relations
    # strictly separate their center waves in that direction)
    runs: list[tuple[int, int, str, bool]] = []
    for relation_index, (previous, current) in enumerate(
        zip(centers, centers[1:])
    ):
        direction, strict_trend = _center_move_direction(previous, current)
        if direction == "none":
            if runs:
                start, _, run_direction, _ = runs[-1]
                runs[-1] = (
                    start,
                    relation_index,
                    run_direction,
                    False,
                )
            continue
        if runs and runs[-1][2] == direction:
            start, _, _, was_strict = runs[-1]
            runs[-1] = (
                start,
                relation_index,
                direction,
                was_strict and strict_trend,
            )
        else:
            runs.append(
                (relation_index, relation_index, direction, strict_trend)
            )

    walks: list[WalkState] = []
    for run_index in range(len(runs) - 1):
        start_relation, end_relation, direction, all_strict = runs[run_index]
        next_start_relation, _, next_direction, _ = runs[run_index + 1]
        expected_opposite = "down" if direction == "up" else "up"
        if next_direction != expected_opposite:
            continue

        # The center at end_relation + 1 starts the opposite run and therefore
        # belongs to that next walk.  This keeps source-center membership
        # disjoint while allowing the price paths to connect at the boundary.
        source_centers = centers[start_relation : end_relation + 1]
        if not source_centers:
            continue

        confirmation_index = next_start_relation + 1
        if confirmation_index >= len(centers):
            continue
        confirmation_center = centers[confirmation_index]

        source_ids: list[str] = []
        for center in source_centers:
            for source_id in center.source_unit_ids:
                if source_id not in source_ids:
                    source_ids.append(source_id)

        first = source_centers[0].zhongshu
        last = source_centers[-1].zhongshu
        is_trend = len(source_centers) >= 2 and all_strict
        kind: ChanWalkKind = (
            f"trend_{direction}" if is_trend else "consolidation"
        )  # type: ignore[assignment]
        walks.append(
            WalkState(
                id=(
                    f"L{source_centers[0].level}:walk:right:{len(walks)}:"
                    f"{first.start_index}-{last.end_index}"
                ),
                level=source_centers[0].level,
                kind=kind,
                start_index=first.start_index,
                end_index=last.end_index,
                high=max(item.zhongshu.gg for item in source_centers),
                low=min(item.zhongshu.dd for item in source_centers),
                event_time=last.event_time,
                available_time=max(
                    last.available_time,
                    confirmation_center.zhongshu.available_time,
                ),
                center_ids=[item.id for item in source_centers],
                source_unit_ids=source_ids,
                status="confirmed",
                move_direction=direction,  # type: ignore[arg-type]
            )
        )

    return walks


def _parent_center_from_pair(
    prev: CenterState,
    cur: CenterState,
    pair_index: int,
) -> CenterState | None:
    if prev.level != cur.level:
        return None
    if classify_center_relation(prev.zhongshu, cur.zhongshu) != "expansion":
        return None

    zd = max(prev.zhongshu.dd, cur.zhongshu.dd)
    zg = min(prev.zhongshu.gg, cur.zhongshu.gg)
    if zd > zg:
        return None

    available_time = max(
        prev.zhongshu.available_time,
        cur.zhongshu.available_time,
    )
    parent_zs = Zhongshu(
        zd=zd,
        zg=zg,
        start_index=prev.zhongshu.start_index,
        end_index=cur.zhongshu.end_index,
        event_time=cur.zhongshu.event_time,
        available_time=available_time,
        origin_available_time=available_time,
        gg=max(prev.zhongshu.gg, cur.zhongshu.gg),
        dd=min(prev.zhongshu.dd, cur.zhongshu.dd),
        g=min(prev.zhongshu.gg, cur.zhongshu.gg),
        d=max(prev.zhongshu.dd, cur.zhongshu.dd),
        evolution="expansion",
        status="confirmed",
    )
    parent_id = (
        f"L{prev.level + 1}:zs:exp:{pair_index}:"
        f"{parent_zs.start_index}-{parent_zs.end_index}"
    )
    return CenterState(
        id=parent_id,
        level=prev.level + 1,
        zhongshu=parent_zs,
        source_unit_ids=[prev.id, cur.id],
        evolution="expansion",
    )


def _center_wave_overlaps_parent(
    parent: CenterState,
    child: CenterState,
) -> bool:
    return (
        max(parent.zhongshu.zd, child.zhongshu.dd)
        <= min(parent.zhongshu.zg, child.zhongshu.gg)
    )


def _extend_parent_with_center(
    parent: CenterState,
    child: CenterState,
) -> None:
    parent_zs = parent.zhongshu
    child_zs = child.zhongshu
    parent_zs.gg = max(parent_zs.gg, child_zs.gg)
    parent_zs.dd = min(parent_zs.dd, child_zs.dd)
    parent_zs.g = min(parent_zs.g, child_zs.gg)
    parent_zs.d = max(parent_zs.d, child_zs.dd)
    parent_zs.end_index = child_zs.end_index
    parent_zs.event_time = child_zs.event_time
    parent_zs.available_time = max(
        parent_zs.available_time,
        child_zs.available_time,
    )
    parent_zs.evolution = "extension"
    parent.evolution = "extension"
    parent.source_unit_ids.append(child.id)


def build_parent_centers_from_expansions(
    centers: list[CenterState],
    *,
    allow_extension: bool = True,
) -> list[CenterState]:
    """Recursively promote non-overlapping current-level centers.

    Lesson 20 says a higher-level center exists exactly when the oscillation
    ranges around two consecutive same-level centers overlap.  A sliding
    adjacent-pair implementation would promote ``C0+C1`` and ``C1+C2`` as
    two parents, making the same child center belong to two supposedly
    consecutive structures.  This parser consumes each child once.  Below
    the selected decomposition level, later child ranges may extend the
    parent; at the selected level extension is disabled (lesson 38).
    """
    parents: list[CenterState] = []
    i = 0
    while i + 1 < len(centers):
        parent = _parent_center_from_pair(centers[i], centers[i + 1], i)
        if parent is None:
            i += 1
            continue

        j = i + 2
        if allow_extension:
            while j < len(centers):
                child = centers[j]
                if child.level != centers[i].level:
                    break
                if not _center_wave_overlaps_parent(parent, child):
                    break
                _extend_parent_with_center(parent, child)
                j += 1

        for child in centers[i:j]:
            child.parent_center_id = parent.id
        parents.append(parent)
        i = j

    return parents


def _units_from_centers(
    centers: list[CenterState],
    *,
    level: int,
) -> list[StructureUnit]:
    units: list[StructureUnit] = []
    for center in centers:
        zs = center.zhongshu
        midpoint = (zs.zd + zs.zg) / 2
        units.append(
            StructureUnit(
                id=center.id,
                level=level,
                kind="center",
                direction="none",
                start_index=zs.start_index,
                end_index=zs.end_index,
                high=zs.gg,
                low=zs.dd,
                start_price=midpoint,
                end_price=midpoint,
                event_time=zs.event_time,
                available_time=zs.available_time,
                status=zs.status,
                source_ids=list(center.source_unit_ids),
            )
        )
    return units


def build_structural_levels_from_segments(
    segments: list[Segment],
    *,
    target_level: int = 2,
    level_names: tuple[str, ...] = ("1m", "5m", "30m"),
) -> list[ChanLevel]:
    """Build 1m→5m→30m structural levels from confirmed 1m segments.

    The names are conventional structural labels, not fixed candle lengths.
    Each higher level is made from non-overlapping, right-confirmed lower-level
    walks rather than fixed-duration candles.  Levels below ``target_level``
    allow center extension.  The selected target level does not, implementing
    the fixed-level decomposition rule from lesson 38.  Only confirmed
    structures participate.
    """
    if target_level < 0:
        raise ValueError("target_level must be non-negative")
    if len(level_names) <= target_level:
        raise ValueError("level_names must include the selected target level")

    base_units = units_from_segments(segments, level=0)
    if not base_units:
        return []

    levels: list[ChanLevel] = []
    units = base_units

    for level_no in range(target_level + 1):
        centers = build_centers_from_units(
            units,
            allow_extension=level_no < target_level,
        )
        walks = build_right_confirmed_walks_from_centers(centers)
        levels.append(
            ChanLevel(
                level=level_no,
                timeframe=f"structural:{level_names[level_no]}",
                units=units,
                centers=centers,
                walks=walks,
            )
        )
        if level_no == target_level or not walks:
            break

        next_units = [item.to_unit() for item in walks]
        if len(next_units) < 3 or not _units_are_strictly_sequential(next_units):
            break
        units = next_units

    return levels


def _units_are_strictly_sequential(units: list[StructureUnit]) -> bool:
    """Return whether units can serve as consecutive lower-level walks.

    Recursive Chan construction requires completed *consecutive* lower-level
    walk types.  Cumulative walk snapshots may be useful as current-state
    descriptions, but overlapping snapshots are not independent source units
    and must never be promoted into a higher-level center.
    """
    return all(
        current.start_index >= previous.end_index
        for previous, current in zip(units, units[1:])
    )


def build_recursive_levels_from_units(
    base_units: list[StructureUnit],
    timeframe: str,
    max_depth: int = 3,
) -> list[ChanLevel]:
    levels: list[ChanLevel] = []
    units = base_units
    level_no = base_units[0].level if base_units else 0

    for _ in range(max_depth):
        centers = build_centers_from_units(units)
        walks = build_walks_from_centers(centers, units)
        parent_centers = build_parent_centers_from_expansions(centers)
        level = ChanLevel(
            level=level_no,
            timeframe=timeframe,
            units=units,
            centers=centers + parent_centers,
            walks=walks,
        )
        levels.append(level)

        next_units = [walk.to_unit() for walk in walks if walk.status == "confirmed"]
        if len(next_units) < 3:
            break
        if not _units_are_strictly_sequential(next_units):
            break
        units = next_units
        level_no += 1

    return levels


def build_recursive_levels_from_bis(
    bis: list[Bi],
    timeframe: str,
    max_depth: int = 3,
) -> list[ChanLevel]:
    return build_recursive_levels_from_units(
        units_from_bis(bis, level=0),
        timeframe=timeframe,
        max_depth=max_depth,
    )
