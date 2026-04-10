from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_trader.chan.config import get_chan_config
from ai_trader.chan.core.buy_sell_points import decide_action, generate_signals
from ai_trader.chan.core.center import build_zhongshus_from_bis, classify_center_relation
from ai_trader.chan.core.segment import build_segments
from ai_trader.chan.engine import suppress_seen_signal_events
from ai_trader.types import Action, Bi, DataQuality, MarketState, Risk, Signal, SignalDecision, Zhongshu


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "chan_oracle_cases.json"


class ChanOracleCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def _t(self, i: int) -> datetime:
        return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * i)

    def _mk_bi(self, payload: dict) -> Bi:
        idx = int(payload["idx"])
        return Bi(
            direction=payload["direction"],  # type: ignore[arg-type]
            start_index=idx,
            end_index=idx + 1,
            start_price=float(payload["start"]),
            end_price=float(payload["end"]),
            event_time=self._t(idx + 1),
            available_time=self._t(idx + 1),
        )

    def _mk_zhongshu(self, payload: dict) -> Zhongshu:
        time_idx = int(payload.get("time_idx", payload["end_index"]))
        return Zhongshu(
            zd=float(payload["zd"]),
            zg=float(payload["zg"]),
            gg=float(payload.get("gg", payload["zg"])),
            dd=float(payload.get("dd", payload["zd"])),
            g=float(payload.get("g", payload["zg"])),
            d=float(payload.get("d", payload["zd"])),
            start_index=int(payload["start_index"]),
            end_index=int(payload["end_index"]),
            event_time=self._t(time_idx),
            available_time=self._t(time_idx),
        )

    def _mk_signal(self, payload: dict) -> Signal:
        event_idx = int(payload["event_idx"])
        available_idx = int(payload["available_idx"])
        signal_type = str(payload["type"])
        return Signal(
            type=signal_type,  # type: ignore[arg-type]
            level=payload.get("level", "main"),  # type: ignore[arg-type]
            trigger=f"{signal_type} trigger",
            invalid_if=f"{signal_type} invalid",
            confidence=float(payload["confidence"]),
            event_time=self._t(event_idx),
            available_time=self._t(available_idx),
            invalid_price=(
                float(payload["invalid_price"])
                if payload.get("invalid_price") is not None
                else None
            ),
            anchor_center_start_index=payload.get("center_start"),
            anchor_center_end_index=payload.get("center_end"),
        )

    def _mk_signal_decision(self, signal_payload: dict) -> SignalDecision:
        signal = self._mk_signal(
            {
                "type": signal_payload["type"],
                "confidence": float(signal_payload.get("confidence", 0.68)),
                "event_idx": signal_payload["event_idx"],
                "available_idx": signal_payload["available_idx"],
                "center_start": signal_payload["center_start"],
                "center_end": signal_payload["center_end"],
                "invalid_price": signal_payload.get("invalid_price"),
            }
        )
        action_decision = "sell" if str(signal.type).startswith("S") else "buy"
        trend_type = "up" if action_decision == "sell" else "down"
        return SignalDecision(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            data_quality=DataQuality(status="ok", notes=""),
            market_state=MarketState(trend_type=trend_type),
            signals=[signal],
            action=Action(decision=action_decision, reason=action_decision),
            risk=Risk(conflict_level="low", notes=""),
            cn_summary=action_decision,
        )

    def test_oracle_cases(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"], lesson=case["lesson"]):
                getattr(self, f"_run_{case['runner']}")(case)

    def _run_build_zhongshus_from_bis(self, case: dict) -> None:
        bis = [self._mk_bi(item) for item in case["input"]["bis"]]
        zhongshus = build_zhongshus_from_bis(bis)
        expected = case["expected"]
        self.assertEqual(len(zhongshus), expected["count"], case["rule"])
        if expected["count"] == 0:
            return
        first = expected["first"]
        self.assertAlmostEqual(zhongshus[0].zd, first["zd"], msg=case["rule"])
        self.assertAlmostEqual(zhongshus[0].zg, first["zg"], msg=case["rule"])
        self.assertEqual(zhongshus[0].start_index, first["start_index"], case["rule"])
        self.assertEqual(zhongshus[0].end_index, first["end_index"], case["rule"])

    def _run_classify_center_relation(self, case: dict) -> None:
        prev = self._mk_zhongshu(case["input"]["prev"])
        cur = self._mk_zhongshu(case["input"]["cur"])
        relation = classify_center_relation(prev, cur)
        self.assertEqual(relation, case["expected"]["relation"], case["rule"])

    def _run_build_segments(self, case: dict) -> None:
        bis = [self._mk_bi(item) for item in case["input"]["bis"]]
        segments = build_segments(bis)
        self.assertEqual(len(segments), case["expected"]["count"], case["rule"])

    def _run_generate_signals(self, case: dict) -> None:
        zhongshu = self._mk_zhongshu(case["input"]["zhongshu"])
        bis_context = [self._mk_bi(item) for item in case["input"].get("bis_context", [])]
        signals = generate_signals(
            divergence_candidates=[],
            bis_sub=[],
            segments_sub=[],
            zhongshus_sub=[],
            zhongshu_main=zhongshu,
            market_state=MarketState(trend_type=case["input"]["market_trend"]),
            macd_missing=False,
            missing_macd_penalty=0.10,
            transitional_confidence_cap=0.60,
            bis_context=bis_context,
        )
        signal_types = {item.type for item in signals}
        for item in case["expected"].get("present", []):
            self.assertIn(item, signal_types, case["rule"])
        for item in case["expected"].get("absent", []):
            self.assertNotIn(item, signal_types, case["rule"])

    def _run_decide_action(self, case: dict) -> None:
        cfg = get_chan_config(case["input"]["chan_mode"])
        signals = [self._mk_signal(item) for item in case["input"]["signals"]]
        market_state = MarketState(
            trend_type=case["input"]["market_state"]["trend_type"],
            phase=case["input"]["market_state"]["phase"],
        )
        action, _ = decide_action(
            signals=signals,
            market_state=market_state,
            conflict_level=case["input"]["conflict_level"],
            min_confidence=float(case["input"]["min_confidence"]),
            chan_config=cfg,
        )
        self.assertEqual(action.decision, case["expected"]["decision"], case["rule"])
        self.assertIn(case["expected"]["reason_contains"], action.reason, case["rule"])

    def _run_suppress_seen_signal_events(self, case: dict) -> None:
        cfg = get_chan_config(case["input"]["chan_mode"])
        seen_signal_keys: set[tuple] = set()
        guards: dict[tuple, dict[str, object]] = {}
        first = suppress_seen_signal_events(
            self._mk_signal_decision(case["input"]["first"]),
            seen_signal_keys,
            cfg,
            float(case["input"]["min_confidence"]),
            active_turning_guards=guards,
            asof_low=case["input"].get("first_asof_low"),
            asof_high=case["input"].get("first_asof_high"),
        )
        second = suppress_seen_signal_events(
            self._mk_signal_decision(case["input"]["second"]),
            seen_signal_keys,
            cfg,
            float(case["input"]["min_confidence"]),
            active_turning_guards=guards,
            asof_low=case["input"].get("second_asof_low"),
            asof_high=case["input"].get("second_asof_high"),
        )
        self.assertEqual(first.action.decision, case["expected"]["first_decision"], case["rule"])
        self.assertEqual(second.action.decision, case["expected"]["second_decision"], case["rule"])
        self.assertEqual(len(second.signals), case["expected"]["second_signal_count"], case["rule"])


if __name__ == "__main__":
    unittest.main()
