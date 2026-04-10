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
from ai_trader.chan.core.buy_sell_points import allow_high_conflict_reversal
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.types import Bi, Zhongshu, iso_utc


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
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2025-12-31T23:59:59Z")
    parser.add_argument("--output-root", default="outputs/diagnostics")
    return parser.parse_args()


def _latest_pair(bis: list[Bi], direction: str) -> tuple[Bi, Bi] | None:
    arr = [x for x in bis if x.direction == direction]
    if len(arr) < 2:
        return None
    return arr[-2], arr[-1]


def _rule_b3(last_close: float, last_bi: Bi | None, zs: Zhongshu | None) -> tuple[bool, str]:
    if zs is None or last_bi is None:
        return False, "missing last_zs/last_bi"
    ok = last_bi.direction == "up" and last_close > zs.zg
    return ok, f"last_bi_dir={last_bi.direction}, close={last_close:.2f}, zg={zs.zg:.2f}"


def _rule_s3(last_close: float, last_bi: Bi | None, zs: Zhongshu | None) -> tuple[bool, str]:
    if zs is None or last_bi is None:
        return False, "missing last_zs/last_bi"
    ok = last_bi.direction == "down" and last_close < zs.zd
    return ok, f"last_bi_dir={last_bi.direction}, close={last_close:.2f}, zd={zs.zd:.2f}"


def _rule_b2(sig_types: set[str], bis_sub: list[Bi]) -> tuple[bool, str]:
    if len(bis_sub) < 2:
        return False, "sub_bis<2"
    ok = "B1" in sig_types and bis_sub[-2].direction == "down" and bis_sub[-1].direction == "up"
    return ok, f"has_B1={'B1' in sig_types}, sub_pair={bis_sub[-2].direction}->{bis_sub[-1].direction}"


def _rule_s2(sig_types: set[str], bis_sub: list[Bi]) -> tuple[bool, str]:
    if len(bis_sub) < 2:
        return False, "sub_bis<2"
    ok = "S1" in sig_types and bis_sub[-2].direction == "up" and bis_sub[-1].direction == "down"
    return ok, f"has_S1={'S1' in sig_types}, sub_pair={bis_sub[-2].direction}->{bis_sub[-1].direction}"


def _rule_b1(bis_main: list[Bi]) -> tuple[bool, str]:
    pair = _latest_pair(bis_main, "down")
    if pair is None:
        return False, "main_down_pair<2"
    prev_bi, cur_bi = pair
    ok = cur_bi.end_price < prev_bi.end_price
    return ok, f"prev_end={prev_bi.end_price:.2f}, cur_end={cur_bi.end_price:.2f}"


def _rule_s1(bis_main: list[Bi]) -> tuple[bool, str]:
    pair = _latest_pair(bis_main, "up")
    if pair is None:
        return False, "main_up_pair<2"
    prev_bi, cur_bi = pair
    ok = cur_bi.end_price > prev_bi.end_price
    return ok, f"prev_end={prev_bi.end_price:.2f}, cur_end={cur_bi.end_price:.2f}"


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

    bars_main = load_ohlcv(args.exchange, args.symbol, args.timeframe_main, args.start, args.end)
    bars_sub = load_ohlcv(args.exchange, args.symbol, args.timeframe_sub, args.start, args.end)

    signal_counter = Counter()
    action_counter = Counter()
    conflict_counter = Counter()
    policy_violations = defaultdict(list)
    signal_rows: list[SignalAuditRow] = []

    for i in range(120, len(bars_main)):
        asof = bars_main[i].time
        sub = [b for b in bars_sub if b.time <= asof]
        snap = build_chan_state(
            bars_main=bars_main[: i + 1],
            bars_sub=sub,
            macd_main=None,
            macd_sub=None,
            asof_time=asof,
            exchange=args.exchange,
            symbol=args.symbol,
            timeframe_main=args.timeframe_main,
            timeframe_sub=args.timeframe_sub,
        )
        decision = generate_signal(snap)
        payload = decision.to_contract_dict()

        action = payload["action"]["decision"]
        conflict = payload["risk"]["conflict_level"]
        phase = payload["market_state"]["phase"]
        sig_types = {item["type"] for item in payload["signals"]}

        action_counter[action] += 1
        conflict_counter[conflict] += 1

        primary_signal = _primary_reversal_signal(decision, action)
        allow_high_conflict_action = allow_high_conflict_reversal(primary_signal, decision.market_state)

        if conflict == "high" and action not in {"wait", "reduce"} and not allow_high_conflict_action:
            policy_violations["high_conflict_action"].append((iso_utc(asof), action, sorted(sig_types)))
        if phase == "transitional" and action != "wait" and not (sig_types & {"B3", "S3"}):
            policy_violations["transitional_without_b3s3"].append((iso_utc(asof), action, sorted(sig_types)))

        for sig in payload["signals"]:
            signal_counter[sig["type"]] += 1
            ok = True
            detail = ""
            check_name = ""

            if sig["type"] == "B1":
                check_name = "b1_new_low"
                ok, detail = _rule_b1(snap.bis_main)
            elif sig["type"] == "S1":
                check_name = "s1_new_high"
                ok, detail = _rule_s1(snap.bis_main)
            elif sig["type"] == "B2":
                check_name = "b2_need_b1_sub_confirm"
                ok, detail = _rule_b2(sig_types, snap.bis_sub)
            elif sig["type"] == "S2":
                check_name = "s2_need_s1_sub_confirm"
                ok, detail = _rule_s2(sig_types, snap.bis_sub)
            elif sig["type"] == "B3":
                check_name = "b3_leave_center"
                close = snap.bars_main[-1].close if snap.bars_main else 0.0
                last_bi = snap.bis_main[-1] if snap.bis_main else None
                ok, detail = _rule_b3(close, last_bi, snap.last_zhongshu_main)
            elif sig["type"] == "S3":
                check_name = "s3_leave_center"
                close = snap.bars_main[-1].close if snap.bars_main else 0.0
                last_bi = snap.bis_main[-1] if snap.bis_main else None
                ok, detail = _rule_s3(close, last_bi, snap.last_zhongshu_main)

            row = SignalAuditRow(
                asof=iso_utc(asof),
                signal_type=sig["type"],
                level=sig["level"],
                confidence=float(sig["confidence"]),
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
