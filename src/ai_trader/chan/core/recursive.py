from __future__ import annotations

from collections.abc import Sequence

from ai_trader.chan.core.center import classify_center_relation
from ai_trader.types import (
    Bi,
    CenterState,
    ChanLevel,
    ChanWalkKind,
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


def _center_from_three_units(
    u1: StructureUnit, u2: StructureUnit, u3: StructureUnit, center_id: str
) -> CenterState | None:
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
    zs.zd = max(zs.zd, unit.low)
    zs.zg = min(zs.zg, unit.high)
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
        prev_zs.zd = max(prev_zs.zd, cand_zs.zd)
        prev_zs.zg = min(prev_zs.zg, cand_zs.zg)
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


def build_centers_from_units(units: list[StructureUnit]) -> list[CenterState]:
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

        j = i + 3
        while j < len(units):
            unit = units[j]
            if unit.low <= candidate.zhongshu.zg and unit.high >= candidate.zhongshu.zd:
                _extend_center(candidate, unit)
                j += 1
            else:
                break

        _append_center(out, candidate)
        i = j

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


def build_parent_centers_from_expansions(centers: list[CenterState]) -> list[CenterState]:
    parents: list[CenterState] = []
    for idx in range(1, len(centers)):
        prev = centers[idx - 1]
        cur = centers[idx]
        if classify_center_relation(prev.zhongshu, cur.zhongshu) != "expansion":
            continue
        zd = max(prev.zhongshu.dd, cur.zhongshu.dd)
        zg = min(prev.zhongshu.gg, cur.zhongshu.gg)
        if zd > zg:
            continue
        available_time = max(prev.zhongshu.available_time, cur.zhongshu.available_time)
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
            g=min(prev.zhongshu.g, cur.zhongshu.g),
            d=max(prev.zhongshu.d, cur.zhongshu.d),
            evolution="expansion",
            status="confirmed",
        )
        parent_id = f"L{prev.level + 1}:zs:exp:{idx - 1}-{idx}:{parent_zs.start_index}-{parent_zs.end_index}"
        prev.parent_center_id = parent_id
        cur.parent_center_id = parent_id
        parents.append(
            CenterState(
                id=parent_id,
                level=prev.level + 1,
                zhongshu=parent_zs,
                source_unit_ids=[prev.id, cur.id],
                evolution="expansion",
            )
        )
    return parents


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
        if len(next_units) < 1:
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
