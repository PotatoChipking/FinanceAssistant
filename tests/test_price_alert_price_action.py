import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.core.price_alert_engine import PriceAlertEngine
from src.models.market import MarketCode


class PriceActionAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = PriceAlertEngine()
        self.engine._get_price_action_cached = AsyncMock(
            return_value={
                "data_as_of": "2026-06-22",
                "score": 75,
                "signals": {
                    "breakout": True,
                    "pullback_confirm": False,
                    "structure_invalidated": False,
                },
                "levels": {"recent_breakout_level": 42.8, "stop_loss": 39.2, "target_price": 47.5},
            }
        )

    async def test_pa_score_and_boolean_conditions(self) -> None:
        matched, score = await self.engine._eval_condition(
            {"type": "pa_score", "op": ">=", "value": 70},
            {},
            MarketCode.CN,
            "600519",
        )
        breakout_matched, breakout = await self.engine._eval_condition(
            {"type": "pa_breakout", "op": "==", "value": 1},
            {},
            MarketCode.CN,
            "600519",
        )

        self.assertTrue(matched)
        self.assertEqual(score["actual"], 75)
        self.assertTrue(breakout_matched)
        self.assertEqual(breakout["actual"], 1)

    async def test_pa_rule_gets_stable_signal_key(self) -> None:
        rule = SimpleNamespace(
            stock=SimpleNamespace(market="CN", symbol="600519"),
            condition_group={
                "op": "and",
                "items": [
                    {"type": "pa_breakout", "op": "==", "value": 1},
                    {"type": "pa_score", "op": ">=", "value": 70},
                ],
            },
        )

        result = await self.engine.eval_rule(rule, {})

        self.assertTrue(result.matched)
        self.assertEqual(result.snapshot["signal_key"], "2026-06-22:pa_breakout,pa_score")
        self.assertEqual(result.snapshot["price_action"]["levels"]["stop_loss"], 39.2)
