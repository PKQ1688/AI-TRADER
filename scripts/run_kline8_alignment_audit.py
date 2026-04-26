from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path, write_csv_rows

ensure_src_on_path()

from ai_trader.chan import build_chan_state, generate_signal
from ai_trader.chan.config import get_chan_config
from ai_trader.chan.core.buy_sell_points import allow_high_conflict_reversal
from ai_trader.chan.core.divergence import _find_trend_segments
from ai_trader.chan.engine import suppress_seen_signal_events
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.types import Bi, Signal, Zhongshu, iso_utc


@dataclass(slots=True)
class SignalAuditRow:
    asof: str
    signal_type: str
    level: str
    confidence: float
    action: str
    conflict_level: str
    phase: str
    ok: bool
    check_name: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "asof": self.asof,
            "signal_type": self.signal_type,
            "level": self.level,
            "confidence": self.confidence,
            "action": self.action,
            "conflict_level": self.conflict_level,
            "phase": self.phase,
            "ok": self.ok,
            "check_name": self.check_name,
            "detail": self.detail,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Chan signal alignment against Kline8 rules")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe-main", default="4h")
    parser.add_argument("--timeframe-sub", default="1h")
    parser.add_argument(
        "--chan-mode",
        default="orthodox_chan",
        choices=("strict_kline8", "orthodox_chan", "pragmatic"),
    )
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2025-12-31T23:59:59Z")
    parser.add_argument("--warmup-bars", type=int, default=120)
    parser.add_argument("--output-root", default="outputs/diagnostics")
    return parser.parse_args()


def _latest_pair(bis: list[Bi], direction: str) -> tuple[Bi, Bi] | None:
    arr = [x for x in bis if x.direction == direction]
    if len(arr) < 2:
        return None
    return arr[-2], arr[-1]


def _anchor_key(signal: Signal) -> tuple[str, int | None, int | None]:
    side = "buy" if signal.type.startswith("B") else "sell"
    return side, signal.anchor_center_start_index, signal.anchor_center_end_index


def _resolve_anchor_center(
    signal: Signal,
    zhongshus: list[Zhongshu],
    fallback: Zhongshu | None,
) -> Zhongshu | None:
    start_idx = signal.anchor_center_start_index
    end_idx = signal.anchor_center_end_index
    if start_idx is not None and end_idx is not None:
        for item in zhongshus:
            if item.start_index == start_idx and item.end_index == end_idx:
                return item
    return fallback


def _rule_b3(
    last_close: float,
    last_bi: Bi | None,
    signal: Signal,
    zhongshus: list[Zhongshu],
    fallback: Zhongshu | None,
) -> tuple[bool, str]:
    zs = _resolve_anchor_center(signal, zhongshus, fallback)
    if zs is None:
        return False, "missing anchor_center"
    del last_bi
    ok = last_close > zs.zg
    return ok, f"close={last_close:.2f}, anchor_zg={zs.zg:.2f}"


def _rule_s3(
    last_close: float,
    last_bi: Bi | None,
    signal: Signal,
    zhongshus: list[Zhongshu],
    fallback: Zhongshu | None,
) -> tuple[bool, str]:
    zs = _resolve_anchor_center(signal, zhongshus, fallback)
    if zs is None:
        return False, "missing anchor_center"
    del last_bi
    ok = last_close < zs.zd
    return ok, f"close={last_close:.2f}, anchor_zd={zs.zd:.2f}"


def _rule_b2(
    signal: Signal,
    first_class_keys: set[tuple[str, int | None, int | None]],
    bis_sub: list[Bi],
) -> tuple[bool, str]:
    if len(bis_sub) < 2:
        return False, "sub_bis<2"
    anchor_key = _anchor_key(signal)
    ok = anchor_key in first_class_keys and bis_sub[-2].direction == "down" and bis_sub[-1].direction == "up"
    return ok, f"has_prior_B1={anchor_key in first_class_keys}, anchor={anchor_key}, sub_pair={bis_sub[-2].direction}->{bis_sub[-1].direction}"


def _rule_s2(
    signal: Signal,
    first_class_keys: set[tuple[str, int | None, int | None]],
    bis_sub: list[Bi],
) -> tuple[bool, str]:
    if len(bis_sub) < 2:
        return False, "sub_bis<2"
    anchor_key = _anchor_key(signal)
    ok = anchor_key in first_class_keys and bis_sub[-2].direction == "up" and bis_sub[-1].direction == "down"
    return ok, f"has_prior_S1={anchor_key in first_class_keys}, anchor={anchor_key}, sub_pair={bis_sub[-2].direction}->{bis_sub[-1].direction}"


def _rule_b1(signal: Signal, bis_main: list[Bi], zhongshus: list[Zhongshu]) -> tuple[bool, str]:
    result = _find_trend_segments(bis_main, zhongshus, "down")
    if result is None:
        return False, "missing a+A+b+B+c"
    _, _, a_dir_bis, c_dir_bis, _, B_zs = result
    if not a_dir_bis or not c_dir_bis:
        return False, "missing a/c leg"
    a_extreme = min(bi.end_price for bi in a_dir_bis)
    c_extreme = min(bi.end_price for bi in c_dir_bis)
    cur_bi = c_dir_bis[-1]
    anchor_ok = (
        signal.anchor_center_start_index == B_zs.start_index
        and signal.anchor_center_end_index == B_zs.end_index
    )
    event_ok = signal.event_time == cur_bi.event_time
    ok = c_extreme < a_extreme and anchor_ok and event_ok
    return ok, (
        f"a_extreme={a_extreme:.2f}, c_extreme={c_extreme:.2f}, "
        f"anchor_ok={anchor_ok}, event_ok={event_ok}"
    )


def _rule_s1(signal: Signal, bis_main: list[Bi], zhongshus: list[Zhongshu]) -> tuple[bool, str]:
    result = _find_trend_segments(bis_main, zhongshus, "up")
    if result is None:
        return False, "missing a+A+b+B+c"
    _, _, a_dir_bis, c_dir_bis, _, B_zs = result
    if not a_dir_bis or not c_dir_bis:
        return False, "missing a/c leg"
    a_extreme = max(bi.end_price for bi in a_dir_bis)
    c_extreme = max(bi.end_price for bi in c_dir_bis)
    cur_bi = c_dir_bis[-1]
    anchor_ok = (
        signal.anchor_center_start_index == B_zs.start_index
        and signal.anchor_center_end_index == B_zs.end_index
    )
    event_ok = signal.event_time == cur_bi.event_time
    ok = c_extreme > a_extreme and anchor_ok and event_ok
    return ok, (
        f"a_extreme={a_extreme:.2f}, c_extreme={c_extreme:.2f}, "
        f"anchor_ok={anchor_ok}, event_ok={event_ok}"
    )


def _primary_reversal_signal(decision, action: str):
    if action == "buy":
        for sig in decision.signals:
            if sig.type in {"B1", "B2", "B3"}:
                return sig
    if action == "sell":
        for sig in decision.signals:
            if sig.type in {"S1", "S2", "S3"}:
                return sig
    return None


def main() -> None:
    args = parse_args()
    cfg = get_chan_config(args.chan_mode)

    bars_main = load_ohlcv(args.exchange, args.symbol, args.timeframe_main, args.start, args.end)
    bars_sub = load_ohlcv(args.exchange, args.symbol, args.timeframe_sub, args.start, args.end)

    signal_counter = Counter()
    action_counter = Counter()
    conflict_counter = Counter()
    policy_violations = defaultdict(list)
    signal_rows: list[SignalAuditRow] = []
    seen_signal_keys: set[tuple] = set()
    turning_signal_guards: dict[tuple, dict[str, object]] = {}
    emitted_first_class_keys: set[tuple[str, int | None, int | None]] = set()

    sub_cursor = 0
    start_index = max(args.warmup_bars, cfg.min_main_bars)
    for i in range(start_index, len(bars_main)):
        asof_bar = bars_main[i]
        while sub_cursor < len(bars_sub) and bars_sub[sub_cursor].time <= asof_bar.time:
            sub_cursor += 1

        snap = build_chan_state(
            bars_main=bars_main[: i + 1],
            bars_sub=bars_sub[:sub_cursor],
            macd_main=None,
            macd_sub=None,
            asof_time=asof_bar.time,
            exchange=args.exchange,
            symbol=args.symbol,
            timeframe_main=args.timeframe_main,
            timeframe_sub=args.timeframe_sub,
            chan_config=cfg,
        )
        decision = generate_signal(snapshot=snap, chan_config=cfg)
        decision = suppress_seen_signal_events(
            decision=decision,
            seen_signal_keys=seen_signal_keys,
            chan_config=cfg,
            min_confidence=cfg.min_confidence,
            active_turning_guards=turning_signal_guards,
            asof_low=asof_bar.low,
            asof_high=asof_bar.high,
        )
        payload = decision.to_contract_dict()

        action = payload["action"]["decision"]
        conflict = payload["risk"]["conflict_level"]
        phase = payload["market_state"]["phase"]
        signal_objects = list(decision.signals)
        signal_payloads = list(payload["signals"])
        sig_types = {item.type for item in signal_objects}

        action_counter[action] += 1
        conflict_counter[conflict] += 1

        primary_signal = _primary_reversal_signal(decision, action)
        allow_reversal_action = allow_high_conflict_reversal(primary_signal, decision.market_state)

        if conflict == "high" and action not in {"wait", "reduce"} and not allow_reversal_action:
            policy_violations["high_conflict_action"].append((iso_utc(asof_bar.time), action, sorted(sig_types)))
        if phase == "transitional" and action != "wait" and not (sig_types & {"B3", "S3"}) and not allow_reversal_action:
            policy_violations["transitional_without_b3s3"].append((iso_utc(asof_bar.time), action, sorted(sig_types)))

        known_first_class_keys = set(emitted_first_class_keys)
        known_first_class_keys.update(
            _anchor_key(item) for item in signal_objects if item.type in {"B1", "S1"}
        )

        for sig_obj, sig in zip(signal_objects, signal_payloads):
            signal_counter[sig_obj.type] += 1
            ok = True
            detail = ""
            check_name = ""

            if sig_obj.type == "B1":
                check_name = "b1_trend_new_low"
                ok, detail = _rule_b1(sig_obj, snap.bis_main, snap.zhongshus_main)
            elif sig_obj.type == "S1":
                check_name = "s1_trend_new_high"
                ok, detail = _rule_s1(sig_obj, snap.bis_main, snap.zhongshus_main)
            elif sig_obj.type == "B2":
                check_name = "b2_need_prior_b1_sub_confirm"
                ok, detail = _rule_b2(sig_obj, known_first_class_keys, snap.bis_sub)
            elif sig_obj.type == "S2":
                check_name = "s2_need_prior_s1_sub_confirm"
                ok, detail = _rule_s2(sig_obj, known_first_class_keys, snap.bis_sub)
            elif sig_obj.type == "B3":
                check_name = "b3_leave_anchor_center"
                close = snap.bars_main[-1].close if snap.bars_main else 0.0
                last_bi = snap.bis_main[-1] if snap.bis_main else None
                ok, detail = _rule_b3(close, last_bi, sig_obj, snap.zhongshus_main, snap.last_zhongshu_main)
            elif sig_obj.type == "S3":
                check_name = "s3_leave_anchor_center"
                close = snap.bars_main[-1].close if snap.bars_main else 0.0
                last_bi = snap.bis_main[-1] if snap.bis_main else None
                ok, detail = _rule_s3(close, last_bi, sig_obj, snap.zhongshus_main, snap.last_zhongshu_main)

            row = SignalAuditRow(
                asof=iso_utc(asof_bar.time),
                signal_type=sig_obj.type,
                level=sig_obj.level,
                confidence=float(sig_obj.confidence),
                action=action,
                conflict_level=conflict,
                phase=phase,
                ok=ok,
                check_name=check_name,
                detail=detail,
            )
            signal_rows.append(row)
            if not ok:
                policy_violations[f"signal_check_fail:{check_name}"].append((row.asof, row.signal_type, row.detail))

        emitted_first_class_keys.update(
            _anchor_key(item) for item in signal_objects if item.type in {"B1", "S1"}
        )

    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pair_key = args.symbol.replace("/", "")
    out_dir = Path(args.output_root) / f"audit_kline8_{pair_key}_{args.timeframe_main}_{args.timeframe_sub}" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    write_csv_rows(out_dir / "signal_audit_rows.csv", [x.to_dict() for x in signal_rows])

    samples = {}
    for t in ["B1", "B2", "B3", "S1", "S2", "S3"]:
        rows = [x.to_dict() for x in signal_rows if x.signal_type == t]
        samples[t] = rows[:10]

    summary = {
        "period": {"start": args.start, "end": args.end},
        "chan_mode": args.chan_mode,
        "counts": {
            "bars_main": len(bars_main),
            "bars_sub": len(bars_sub),
            "signals": dict(signal_counter),
            "actions": dict(action_counter),
            "conflicts": dict(conflict_counter),
        },
        "violations": {k: len(v) for k, v in policy_violations.items()},
        "violation_examples": {k: v[:10] for k, v in policy_violations.items()},
        "samples_by_signal": samples,
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Kline8 对齐审计摘要",
        "",
        f"- chan_mode: {args.chan_mode}",
        f"- period: {args.start} -> {args.end}",
        f"- bars(main/sub): {len(bars_main)}/{len(bars_sub)}",
        f"- signals: {dict(signal_counter)}",
        f"- actions: {dict(action_counter)}",
        f"- conflicts: {dict(conflict_counter)}",
        "",
        "## Violations",
    ]
    if not policy_violations:
        lines.append("- none")
    else:
        for k, v in policy_violations.items():
            lines.append(f"- {k}: {len(v)}")

    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Audit completed. Output: {out_dir}")


if __name__ == "__main__":
    main()
