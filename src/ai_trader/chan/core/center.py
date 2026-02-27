from __future__ import annotations

from ai_trader.types import Segment, Zhongshu


def _build_center_from_three(s1: Segment, s2: Segment, s3: Segment) -> Zhongshu | None:
    zd = max(s1.low, s2.low, s3.low)
    zg = min(s1.high, s2.high, s3.high)
    if zd > zg:
        return None

    highs = [s1.high, s2.high, s3.high]
    lows = [s1.low, s2.low, s3.low]

    return Zhongshu(
        zd=zd,
        zg=zg,
        gg=max(highs),
        dd=min(lows),
        g=min(highs),
        d=max(lows),
        start_index=s1.start_index,
        end_index=s3.end_index,
        event_time=s3.event_time,
        available_time=max(s1.available_time, s2.available_time, s3.available_time),
        evolution="newborn",
        status="confirmed",
    )


def _overlap_center(a: Zhongshu, b: Zhongshu) -> bool:
    return max(a.zd, b.zd) <= min(a.zg, b.zg)


def _evolution(prev: Zhongshu, cur: Zhongshu) -> str:
    if _overlap_center(prev, cur):
        return "extension"

    if (cur.zg < prev.zd and cur.gg >= prev.dd) or (cur.zd > prev.zg and cur.dd <= prev.gg):
        return "expansion"

    return "newborn"


def build_zhongshus(segments: list[Segment]) -> list[Zhongshu]:
    out: list[Zhongshu] = []
    if len(segments) < 3:
        return out

    for i in range(2, len(segments)):
        candidate = _build_center_from_three(segments[i - 2], segments[i - 1], segments[i])
        if candidate is None:
            continue

        if not out:
            out.append(candidate)
            continue

        prev = out[-1]
        evo = _evolution(prev, candidate)
        if evo == "extension":
            prev.zd = max(prev.zd, candidate.zd)
            prev.zg = min(prev.zg, candidate.zg)
            prev.gg = max(prev.gg, candidate.gg)
            prev.dd = min(prev.dd, candidate.dd)
            prev.g = min(prev.g, candidate.g)
            prev.d = max(prev.d, candidate.d)
            prev.end_index = candidate.end_index
            prev.event_time = candidate.event_time
            prev.available_time = candidate.available_time
            prev.evolution = "extension"
        else:
            candidate.evolution = evo
            out.append(candidate)

    return out
