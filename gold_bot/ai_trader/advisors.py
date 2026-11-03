"""Advisors: things that look at a Context and propose a Decision.

`ClaudeAdvisor` is the language-model one. `RuleAdvisor` is a deterministic
control built from the same snapshot - the point of having it is to be able
to ask the only question that matters about an AI trader: does it do better
than simple rules fed the same information?

An advisor never raises. Any failure (network, refusal, truncation, bad
JSON) degrades to HOLD. That is safe by construction: an open position
already has its stop and target registered at the broker.
"""
from __future__ import annotations

from typing import Callable, Protocol

from .context import Context
from .schema import DECISION_JSON_SCHEMA, Decision, decision_from_json

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are the analysis engine of an automated XAUUSD (gold) intraday trading system.
Each call you receive a snapshot of the market built from CLOSED bars only (M5, M15, H1, H4 candles,
indicators, and confirmed swing support/resistance levels) plus the state of any open position.
You return exactly one decision as JSON. Your decision applies from the next bar.

HOW TO THINK
- Read the trend on every timeframe, locate price relative to the nearest support/resistance, and look for
  evidence of continuation or reversal (rejection at a level, momentum divergence, a break of structure).
- Trade only with confluence: higher-timeframe direction, a clear level to place the stop beyond, and a
  clear level to target. If any of those is missing, the answer is HOLD.
- HOLD is the correct answer most of the time. This system's edge comes from being selective - a handful of
  trades a week - and it was measured to get worse, not better, when it trades more. Do not look for a
  reason to act. A missed trade costs nothing; a forced one costs the stop.

ENTRIES (OPEN_BUY / OPEN_SELL)
- stop_loss and take_profit are absolute prices.
- Put the stop just beyond the structure that would prove you wrong (below support for a buy, above
  resistance for a sell). The system sizes the position FROM your stop distance, so a wider structural stop
  simply means a smaller lot at the same dollar risk. Never shrink a stop to make a trade look better.
- Put the target at the next opposing level. Reward:risk must clear the stated minimum after costs.
- confidence is your honest probability-style estimate in 0..1. Below the stated minimum the trade is
  discarded, so do not inflate it.

OPEN POSITIONS (ADJUST / CLOSE / HOLD)
- Protect profit: after roughly +1R move the stop to breakeven, then trail it behind new structure.
  Let winners run by extending the target, but the system allows that only once the stop is at breakeven.
- Cut a loser EARLY when its premise is invalidated (structure broken, reversal confirmed): use CLOSE.
- Never hope. The system will REFUSE any attempt to widen a stop, move it away from price, add to a
  losing position, or open a second position. Do not propose them. A trade that is wrong is exited, not waited out.
- For ADJUST put the new absolute price in stop_loss / take_profit, or 0 to leave that one unchanged.

OUTPUT
- Use 0 for any price field that does not apply. trend, reversal_risk, key_support and key_resistance
  describe your read of the market on every call, even when you HOLD.
- reasoning: at most three short sentences, written in Vietnamese."""


class Advisor(Protocol):
    def decide(self, ctx: Context) -> Decision: ...


# ---------------------------------------------------------------------- Claude
class ClaudeAdvisor:
    def __init__(self, client=None, model: str = DEFAULT_MODEL, effort: str = "medium",
                 max_tokens: int = 16000, use_fallbacks: bool = True):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_fallbacks = use_fallbacks
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.failures = 0

    def _request(self, prompt: str, with_fallbacks: bool):
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            # Thinking is always on for this model and is steered with effort.
            # No temperature/top_p (rejected), no prefill (rejected), no forced
            # tool_choice (rejected) - structured output carries the contract.
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": DECISION_JSON_SCHEMA}},
        )
        if with_fallbacks:
            # A safety classifier may decline a request; this lets the API
            # re-run it on a fallback model instead of failing the call.
            return self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)

    def decide(self, ctx: Context) -> Decision:
        prompt = ctx.to_prompt()
        try:
            try:
                resp = self._request(prompt, self.use_fallbacks)
            except Exception as exc:                       # noqa: BLE001
                # If only the fallback option was rejected, retry without it.
                if self.use_fallbacks and "fallback" in str(exc).lower():
                    self.use_fallbacks = False
                    resp = self._request(prompt, False)
                else:
                    raise
        except Exception as exc:                           # noqa: BLE001 - never break the loop
            self.failures += 1
            return Decision(reasoning=f"advisor unavailable: {type(exc).__name__}: {str(exc)[:160]}")

        self.calls += 1
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0

        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            details = getattr(resp, "stop_details", None)
            cat = getattr(details, "category", None)
            return Decision(reasoning=f"model declined to answer (category={cat})")
        if stop == "max_tokens":
            return Decision(reasoning="response truncated at max_tokens; discarded")

        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        d = decision_from_json(text)
        d.meta["model"] = getattr(resp, "model", self.model)
        return d


# ------------------------------------------------------------------------ rules
class RuleAdvisor:
    """Deterministic control. Trend-aligned shallow pullback, fixed ATR stop/target.

    It is NOT the validated strategy in `gold_bot.strategy` - it is a simple
    stand-in that reads the same snapshot, so an LLM can be compared against
    rules that see exactly what it sees.
    """

    def __init__(self, sl_atr: float = 1.8, tp_atr: float = 3.0, trail: bool = False):
        self.sl_atr, self.tp_atr, self.trail = sl_atr, tp_atr, trail

    def decide(self, ctx: Context) -> Decision:
        if ctx.position:
            return self._manage(ctx)
        m5, m15, h1 = (ctx.tf.get(k) for k in ("M5", "M15", "H1"))
        if not (m5 and m15 and h1):
            return Decision(reasoning="warming up")
        a = ctx.atr
        up = m5["trend"] == m15["trend"] == h1["trend"] == "up"
        dn = m5["trend"] == m15["trend"] == h1["trend"] == "down"
        near = -0.3 <= m5["dist_ema50_atr"] <= 0.8 if up else -0.8 <= m5["dist_ema50_atr"] <= 0.3
        rising = m5["macd_hist"] > m5["macd_hist_prev"]
        if up and near and rising and m5["rsi"] < 62:
            return Decision("OPEN_BUY", 0.70, ctx.price - self.sl_atr * a, ctx.price + self.tp_atr * a,
                            trend="up", reversal_risk="low", reasoning="rule: up-trend pullback")
        if dn and near and not rising and m5["rsi"] > 38:
            return Decision("OPEN_SELL", 0.70, ctx.price + self.sl_atr * a, ctx.price - self.tp_atr * a,
                            trend="down", reversal_risk="low", reasoning="rule: down-trend pullback")
        return Decision(reasoning="rule: no setup")

    def _manage(self, ctx: Context) -> Decision:
        p = ctx.position
        if not self.trail or p["r"] < 1.0 or p["sl_at_be"]:
            return Decision(reasoning="rule: leave to SL/TP")
        return Decision("ADJUST", 1.0, stop_loss=p["entry"], reasoning="rule: breakeven at +1R")


# ------------------------------------------------------------------------ tests
class ScriptedAdvisor:
    """Plays back a fixed list of decisions (or calls a function). For tests."""

    def __init__(self, script: "list[Decision] | Callable[[Context], Decision]"):
        self.script = script
        self.i = 0
        self.seen: list[Context] = []

    def decide(self, ctx: Context) -> Decision:
        self.seen.append(ctx)
        if callable(self.script):
            return self.script(ctx)
        d = self.script[self.i] if self.i < len(self.script) else Decision()
        self.i += 1
        return d
