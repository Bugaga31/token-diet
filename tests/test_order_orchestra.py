"""Тесты order_orchestra: валидация, PAPER/LIVE-gate, аудит, bracket."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from token_diet.order_orchestra import (  # noqa: E402
    ExecutedOrder,
    OrderIntent,
    OrderOrchestra,
    basket_confirm_token,
    paper_broker,
    plan_stop_bracket,
)


def _broker_log(log: list):
    def execute(intent):
        log.append(intent)
        return ExecutedOrder(intent, "PAPER", "FILLED", price=100.0)
    return execute


class TestValidation:
    def test_valid_intent_no_errors(self):
        i = OrderIntent("SBER", "BUY", quantity=10, limit_price=100, stop_loss=95)
        assert i.validate() == []

    def test_garbage_rejected(self):
        bad = [
            OrderIntent("", "BUY", 1),
            OrderIntent("A", "HOLD", 1),
            OrderIntent("A", "BUY", 0),
            OrderIntent("A", "BUY", 1, limit_price=-5),
        ]
        assert all(b.validate() for b in bad)

    def test_stop_above_buy_entry_rejected(self):
        i = OrderIntent("A", "BUY", quantity=1, limit_price=100, stop_loss=105)
        assert any("стоп" in e for e in i.validate())


class TestPaperMode:
    def test_paper_default_executes_without_broker_side_effects(self):
        orch = OrderOrchestra(broker=paper_broker({"SBER": 300.0}))
        res = orch.run([OrderIntent("SBER", "BUY", quantity=5)])
        assert res.mode == "PAPER"
        assert res.filled == 1
        assert res.orders[0].price == 300.0

    def test_invalid_intent_never_reaches_broker(self):
        log = []
        orch = OrderOrchestra(broker=_broker_log(log))
        res = orch.run([OrderIntent("", "BUY", 1)])
        assert log == [] and len(res.rejected_intents) == 1


class TestLiveGate:
    def _intents(self):
        return [OrderIntent("SBER", "BUY", quantity=2, limit_price=300)]

    def test_live_without_token_blocked(self):
        log = []
        orch = OrderOrchestra(broker=_broker_log(log))
        res = orch.run(self._intents(), live=True)
        assert log == []
        assert all(o.status == "REJECTED" for o in res.orders)
        assert "не совпало" in res.orders[0].detail

    def test_live_with_wrong_token_blocked(self):
        orch = OrderOrchestra(broker=paper_broker({"SBER": 300}))
        res = orch.run(self._intents(), live=True, confirm_token="deadbeef0000")
        assert res.filled == 0

    def test_live_with_exact_token_executes(self):
        broker_calls = []
        orch = OrderOrchestra(broker=_broker_log(broker_calls))
        token = basket_confirm_token(self._intents())
        res = orch.run(self._intents(), live=True, confirm_token=token)
        assert res.mode == "LIVE" and res.filled == 1 and len(broker_calls) == 1

    def test_token_depends_on_basket_content(self):
        a = [OrderIntent("SBER", "BUY", 10)]
        b = [OrderIntent("SBER", "BUY", 11)]
        assert basket_confirm_token(a) != basket_confirm_token(b)


class TestAuditAndLimits:
    def test_audit_written(self, tmp_path):
        path = tmp_path / "audit" / "log.jsonl"
        orch = OrderOrchestra(
            broker=paper_broker({"S": 1}), audit_path=path,
        )
        orch.run([OrderIntent("S", "BUY", 1)])
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        rec = json.loads(lines[0])
        assert rec["mode"] == "PAPER" and rec["filled"] == 1

    def test_max_orders_limit(self):
        orch = OrderOrchestra(broker=paper_broker({"X": 1}), max_orders_per_run=3)
        res = orch.run([OrderIntent("X", "BUY", 1) for _ in range(7)])
        assert res.filled == 3 and res.rejected_intents


class TestStopBracket:
    def test_bracket_risk_reward(self):
        intents = plan_stop_bracket("GAZP", entry=130.0, atr_value=4.0,
                                    atr_multiplier=2.5, rr=2.0)
        (i,) = intents
        assert i.limit_price == 130.0
        assert i.stop_loss == 120.0          # 130 − 2.5×4
        assert i.take_profit == 150.0        # 130 + 2×10
        assert i.stop_loss < i.limit_price < i.take_profit
