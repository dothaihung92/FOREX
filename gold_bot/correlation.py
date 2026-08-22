"""USD-strength confirmation for gold entries.

Gold is quoted in dollars, so a large part of any XAUUSD move is really a
dollar move. Measured on this project's own data, that relationship is
strong and its direction matches the conventional wisdom exactly - but
only at macro timeframes:

    correlation of returns with gold (2020-2022 overlap window)
    timeframe   EURUSD   AUDUSD   USDCHF   USD basket
    M15          +0.004   +0.007   -0.015       -0.008
    H1           -0.017   -0.019   -0.005       +0.017
    H4           +0.150   +0.155   -0.215       -0.204
    D1           +0.404   +0.439   -0.512       -0.542
    W1           +0.410   +0.540   -0.679       -0.628

Bar-to-bar confirmation on M5/M15 is therefore worthless - there is no
information at that scale. The only defensible use is as a slower REGIME
gate: measure the dollar's drift over hours, and require it to lean the
right way before taking a gold entry.

**This filter is NOT validated and ships disabled.** The repo's FX history
ends 2022-03-04 while gold M5 runs to 2025-08, leaving a ~1.5-year overlap
containing just 34 baseline trades. In that window the best variant looked
excellent (PF 1.46) but a randomisation test put it at p=0.069 across 20
variants tried - about what one expects from noise alone - and the effect
appeared at a 12h lookback while vanishing at 4h, 24h and 72h, which a
real effect would not do. See README "Gold/FX correlation filter".

Enable it only after re-validating on FX data that covers 2022-2025.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Sign convention: +1 means a rising quote implies a STRONGER dollar.
# EURUSD/GBPUSD/AUDUSD are quoted with USD second, so a rise = weaker USD.
USD_SIGN = {
    "EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
    "USDJPY": +1, "USDCHF": +1, "USDCAD": +1,
}


def usd_strength_index(closes: dict[str, pd.Series]) -> pd.Series:
    """Equal-weighted log USD index from whatever pairs are supplied.

    Each pair is oriented by USD_SIGN so that the result always rises when
    the dollar strengthens. Pairs not in USD_SIGN are ignored rather than
    guessed at - a wrong sign would invert the whole filter.
    """
    parts = [USD_SIGN[s] * np.log(ser.astype(float))
             for s, ser in closes.items() if s in USD_SIGN]
    if not parts:
        raise ValueError("no recognised USD pairs supplied")
    return pd.concat(parts, axis=1).mean(axis=1)


def align_without_lookahead(source: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Value of `source` as of the last observation STRICTLY before each
    target timestamp.

    The strictness matters. FX and gold bars stamped with the same time
    close at the same moment, so allowing an exact match would let the
    filter read a price the strategy could not have known yet - a silent
    lookahead that inflates every backtest built on it.
    """
    left = pd.DataFrame({"t": target_index})
    right = pd.DataFrame({"t": source.index, "v": source.to_numpy()})
    merged = pd.merge_asof(
        left.sort_values("t"), right.sort_values("t"),
        on="t", direction="backward", allow_exact_matches=False,
    )
    return pd.Series(merged["v"].to_numpy(), index=target_index)


def usd_confirmation(
    closes: dict[str, pd.Series],
    target_index: pd.DatetimeIndex,
    lookback_bars: int,
) -> tuple[pd.Series, pd.Series]:
    """Return (long_ok, short_ok) boolean masks aligned to `target_index`.

    A gold long is permitted while the dollar has been weakening over the
    lookback, a short while it has been strengthening. Bars with no FX
    history yet return False on both sides: an unknown dollar is not a
    reason to trade, so the filter fails closed rather than open.
    """
    idx = usd_strength_index(closes)
    aligned = align_without_lookahead(idx, target_index)
    drift = aligned - aligned.shift(lookback_bars)
    long_ok = (drift < 0).fillna(False)
    short_ok = (drift > 0).fillna(False)
    return long_ok, short_ok
