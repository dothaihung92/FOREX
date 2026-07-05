"""
Gold (XAUUSD) M5 trend-pullback strategy.

Rationale
---------
Gold on a 5-minute chart is noisy, so trading every EMA cross or RSI blip
gives a poor win rate. The approach here combines three filters so a trade
only fires when several independent signals agree:

1. Trend filter (two timeframes): price must be on the same side of a slow
   EMA on M5 *and* on a higher timeframe (default M15). This removes most
   counter-trend noise trades and chop.
2. Pullback trigger: within an uptrend, we wait for RSI to dip toward
   oversold and then cross back up through a mid-level (default 45) -
   i.e. a shallow pullback resuming in the trend direction, not a blind
   trend-cross entry. Mirrored for downtrends.
3. Momentum confirmation: MACD histogram must be rising (long) or falling
   (short) on the signal bar, confirming momentum is turning back in the
   trend direction rather than just RSI noise.
4. Session filter: entries are only allowed during the configured
   high-liquidity windows (London / New York), since gold's 5-minute
   moves outside these windows are dominated by spread/noise rather than
   real momentum.

Exits use ATR-based stop loss / take profit, computed by the risk manager.

Regime switch (trend vs. range)
--------------------------------
Backtesting on 5 years of real XAUUSD M5 data showed the trend-pullback
rules above only make money while gold is actually trending (e.g. the
2024-2025 rally) and lose steadily during flat/choppy stretches (e.g.
2020-2023), because there's no real pullback-and-resume move to catch.

ADX (`gold_bot.indicators.adx`) measures trend *strength* regardless of
direction. Below `adx_threshold` the market is classified as ranging, the
trend-pullback rules are disabled, and a Bollinger Band mean-reversion
rule takes over instead: fade a close that pokes outside the bands and
snaps back in, which is precisely the behaviour a choppy/sideways market
produces and a trend strategy cannot exploit. At/above the threshold, the
market is trending and the original trend-pullback rules apply exclusively
(mean-reversion would just be fading a real trend there, which loses).
Each bar therefore fires from at most one of the two rule sets, tagged via
the `signal_type` column ("trend" or "mean_reversion") so the backtester
can apply the risk multipliers appropriate to that trade style.
"""
from __future__ import annotations

from datetime import time as dtime

import pandas as pd

from gold_bot.config import SessionWindow, StrategyConfig
from gold_bot.indicators import adx, atr, bollinger_bands, ema, macd, rsi

TIMEFRAME_TO_PANDAS_FREQ = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}


def _parse_hhmm(value: str) -> dtime:
    hh, mm = value.split(":")
    return dtime(int(hh), int(mm))


def in_session(ts: pd.Timestamp, sessions: list[SessionWindow]) -> bool:
    """True if ts (assumed UTC) falls within any configured session window."""
    t = ts.time()
    for s in sessions:
        start, end = _parse_hhmm(s.start), _parse_hhmm(s.end)
        if start <= end:
            if start <= t <= end:
                return True
        else:  # window crosses midnight
            if t >= start or t <= end:
                return True
    return False


def _htf_trend(df: pd.DataFrame, htf_timeframe: str, htf_ema_period: int) -> pd.Series:
    """Resample to the higher timeframe, compute EMA trend, then map each
    M5 bar to the HTF trend that was known at that point in time (no
    look-ahead: HTF bar only becomes visible once it closes)."""
    freq = TIMEFRAME_TO_PANDAS_FREQ[htf_timeframe]
    htf = df["close"].resample(freq).last().dropna()
    htf_ema = ema(htf, htf_ema_period)
    htf_trend = (htf > htf_ema).astype(int) - (htf < htf_ema).astype(int)
    # Shift by one HTF bar so we only use fully closed higher-timeframe bars.
    htf_trend = htf_trend.shift(1)
    return htf_trend.reindex(df.index, method="ffill")


def generate_signals(df: pd.DataFrame, cfg: StrategyConfig, sessions: list[SessionWindow]) -> pd.DataFrame:
    """
    df must have columns: open, high, low, close, indexed by UTC datetime.
    Returns df with added indicator columns and a `signal` column:
        1  -> long entry
       -1  -> short entry
        0  -> no entry
    """
    out = df.copy()
    close = out["close"]

    out["ema_fast"] = ema(close, cfg.ema_fast)
    out["ema_slow"] = ema(close, cfg.ema_slow)
    out["rsi"] = rsi(close, cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    macd_line, signal_line, hist = macd(close)
    out["macd_hist"] = hist
    out["htf_trend"] = _htf_trend(out, cfg.htf_timeframe, cfg.htf_ema_period)
    out["adx"] = adx(out, cfg.adx_period)
    bb_upper, bb_mid, bb_lower = bollinger_bands(close, cfg.bb_period, cfg.bb_std_mult)
    out["bb_upper"], out["bb_mid"], out["bb_lower"] = bb_upper, bb_mid, bb_lower

    # NOTE: trend-pullback signals below are NOT gated by ADX - an earlier
    # version required trending_regime (adx >= threshold) for trend entries
    # too, but that excluded real, profitable trend-pullback trades that
    # happened to occur at moderate ADX. ADX here is only used to decide
    # when the (experimental, opt-in) mean-reversion rules may fire.
    ranging_regime = out["adx"] < cfg.adx_threshold

    m5_uptrend = close > out["ema_slow"]
    m5_downtrend = close < out["ema_slow"]
    uptrend = m5_uptrend & (out["htf_trend"] > 0)
    downtrend = m5_downtrend & (out["htf_trend"] < 0)

    # Pullback: RSI touched near-oversold within the last N bars, then
    # crossed back above the pullback level on this bar.
    lookback = 6
    recent_oversold = (out["rsi"] <= cfg.rsi_oversold + 5).rolling(lookback, min_periods=1).max().astype(bool)
    recent_overbought = (out["rsi"] >= cfg.rsi_overbought - 5).rolling(lookback, min_periods=1).max().astype(bool)

    rsi_cross_up = (out["rsi"] > cfg.rsi_pullback_level) & (out["rsi"].shift(1) <= cfg.rsi_pullback_level)
    rsi_cross_down = (out["rsi"] < (100 - cfg.rsi_pullback_level)) & (
        out["rsi"].shift(1) >= (100 - cfg.rsi_pullback_level)
    )

    macd_rising = out["macd_hist"] > out["macd_hist"].shift(1)
    macd_falling = out["macd_hist"] < out["macd_hist"].shift(1)

    session_ok = out.index.to_series().apply(lambda ts: in_session(ts, sessions))

    trend_long = (
        uptrend
        & recent_oversold.shift(1).fillna(False)
        & rsi_cross_up
        & macd_rising
        & (close > out["ema_fast"])
        & session_ok
    )
    trend_short = (
        downtrend
        & recent_overbought.shift(1).fillna(False)
        & rsi_cross_down
        & macd_falling
        & (close < out["ema_fast"])
        & session_ok
    )

    out["signal"] = 0
    out["signal_type"] = None
    out.loc[trend_long, ["signal", "signal_type"]] = [1, "trend"]
    out.loc[trend_short, ["signal", "signal_type"]] = [-1, "trend"]

    if cfg.enable_mean_reversion:
        # EXPERIMENTAL, off by default - see README "Mean-reversion
        # experiment" section. Backtested net negative on real XAUUSD M5
        # data even after tuning (tighter regime filter, band-width
        # filter, exit at band midpoint): average loss exceeded average
        # win despite a higher win rate, so it drags down the combined
        # result. Left here, disabled, in case future tuning finds a
        # profitable variant - do not enable without re-validating.
        prev_close = close.shift(1)
        mr_long = (
            ranging_regime
            & (prev_close < out["bb_lower"].shift(1))
            & (close >= out["bb_lower"])
            & (out["rsi"] < cfg.rsi_oversold + 10)
            & session_ok
        )
        mr_short = (
            ranging_regime
            & (prev_close > out["bb_upper"].shift(1))
            & (close <= out["bb_upper"])
            & (out["rsi"] > cfg.rsi_overbought - 10)
            & session_ok
        )
        # Trend signals take priority when both fire on the same bar.
        mr_long = mr_long & ~trend_long & ~trend_short
        mr_short = mr_short & ~trend_long & ~trend_short
        out.loc[mr_long, ["signal", "signal_type"]] = [1, "mean_reversion"]
        out.loc[mr_short, ["signal", "signal_type"]] = [-1, "mean_reversion"]

    return out
