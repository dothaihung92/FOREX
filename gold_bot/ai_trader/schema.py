"""The contract between the advisor (an LLM or a rule set) and the engine.

Numeric fields are plain numbers with 0 meaning "not applicable" rather than
nullable types, so the JSON schema stays within the subset structured outputs
handle reliably.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

ACTIONS = ("HOLD", "OPEN_BUY", "OPEN_SELL", "ADJUST", "CLOSE")


@dataclass
class Decision:
    action: str = "HOLD"
    confidence: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trend: str = "unknown"
    reversal_risk: str = "unknown"
    key_support: float = 0.0
    key_resistance: float = 0.0
    reasoning: str = ""
    # Bookkeeping, filled by the advisor, never trusted for anything.
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action, "confidence": self.confidence,
            "stop_loss": self.stop_loss, "take_profit": self.take_profit,
            "trend": self.trend, "reversal_risk": self.reversal_risk,
            "key_support": self.key_support, "key_resistance": self.key_resistance,
            "reasoning": self.reasoning,
        }


HOLD = Decision(action="HOLD", reasoning="no action")


def _num(value) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x and abs(x) != float("inf") else 0.0   # NaN / inf -> 0


def decision_from_dict(raw: dict) -> Decision:
    """Parse untrusted model output. Anything malformed becomes a safe HOLD."""
    if not isinstance(raw, dict):
        return Decision(reasoning="unparseable: not an object")
    action = str(raw.get("action", "HOLD")).upper().strip()
    if action not in ACTIONS:
        return Decision(reasoning=f"unknown action {action!r}")
    return Decision(
        action=action,
        confidence=min(max(_num(raw.get("confidence")), 0.0), 1.0),
        stop_loss=_num(raw.get("stop_loss")),
        take_profit=_num(raw.get("take_profit")),
        trend=str(raw.get("trend", "unknown"))[:16],
        reversal_risk=str(raw.get("reversal_risk", "unknown"))[:16],
        key_support=_num(raw.get("key_support")),
        key_resistance=_num(raw.get("key_resistance")),
        reasoning=str(raw.get("reasoning", ""))[:600],
    )


def decision_from_json(text: str) -> Decision:
    try:
        return decision_from_dict(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        return Decision(reasoning="unparseable: invalid JSON")


DECISION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "confidence": {"type": "number"},
        "stop_loss": {"type": "number"},
        "take_profit": {"type": "number"},
        "trend": {"type": "string", "enum": ["up", "down", "range"]},
        "reversal_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "key_support": {"type": "number"},
        "key_resistance": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "confidence", "stop_loss", "take_profit", "trend",
                 "reversal_risk", "key_support", "key_resistance", "reasoning"],
    "additionalProperties": False,
}
