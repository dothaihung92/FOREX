import numpy as np
import pandas as pd
import pytest

from gold_bot.correlation import (
    align_without_lookahead,
    usd_confirmation,
    usd_strength_index,
)


def _idx(n, freq="15min", start="2024-01-01"):
    return pd.date_range(start, periods=n, freq=freq, tz="UTC")


def test_usd_index_rises_when_dollar_strengthens():
    i = _idx(10)
    # EURUSD falling = dollar strengthening; USDCHF rising = same.
    closes = {
        "EURUSD": pd.Series(np.linspace(1.10, 1.05, 10), index=i),
        "USDCHF": pd.Series(np.linspace(0.90, 0.95, 10), index=i),
    }
    idx = usd_strength_index(closes)
    assert idx.iloc[-1] > idx.iloc[0]


def test_usd_index_falls_when_dollar_weakens():
    i = _idx(10)
    closes = {
        "EURUSD": pd.Series(np.linspace(1.05, 1.10, 10), index=i),
        "AUDUSD": pd.Series(np.linspace(0.65, 0.70, 10), index=i),
    }
    assert usd_strength_index(closes).iloc[-1] < usd_strength_index(closes).iloc[0]


def test_unknown_pairs_are_ignored_not_guessed():
    i = _idx(5)
    closes = {
        "EURUSD": pd.Series(np.linspace(1.10, 1.05, 5), index=i),
        "XAUXAG": pd.Series(np.linspace(50, 90, 5), index=i),
    }
    only_eur = usd_strength_index({"EURUSD": closes["EURUSD"]})
    pd.testing.assert_series_equal(usd_strength_index(closes), only_eur)


def test_no_recognised_pairs_raises():
    i = _idx(3)
    with pytest.raises(ValueError):
        usd_strength_index({"XAUXAG": pd.Series([1.0, 2.0, 3.0], index=i)})


def test_alignment_never_reads_the_current_bar():
    """A same-stamped FX bar closes at the same instant as the gold bar, so
    it must NOT be visible - that would be lookahead."""
    src_i = _idx(4, freq="15min")
    src = pd.Series([1.0, 2.0, 3.0, 4.0], index=src_i)
    got = align_without_lookahead(src, src_i)
    assert pd.isna(got.iloc[0])           # nothing published before the first bar
    assert got.iloc[1] == 1.0             # sees only the previous bar
    assert got.iloc[3] == 3.0


def test_alignment_holds_last_value_between_updates():
    src = pd.Series([1.0, 2.0], index=_idx(2, freq="1h"))
    target = _idx(8, freq="15min")
    got = align_without_lookahead(src, target)
    assert got.iloc[1] == 1.0             # 00:15 sees the 00:00 FX bar
    assert got.iloc[4] == 1.0             # 01:00 still sees 00:00, not the 01:00 bar
    assert got.iloc[5] == 2.0             # 01:15 finally sees it


def test_confirmation_allows_longs_only_while_dollar_weakens():
    i = _idx(40, freq="5min")
    closes = {"EURUSD": pd.Series(np.linspace(1.05, 1.10, 40), index=i)}  # USD weakening
    long_ok, short_ok = usd_confirmation(closes, i, lookback_bars=4)
    assert long_ok.iloc[-1]
    assert not short_ok.iloc[-1]


def test_confirmation_allows_shorts_only_while_dollar_strengthens():
    i = _idx(40, freq="5min")
    closes = {"EURUSD": pd.Series(np.linspace(1.10, 1.05, 40), index=i)}
    long_ok, short_ok = usd_confirmation(closes, i, lookback_bars=4)
    assert short_ok.iloc[-1]
    assert not long_ok.iloc[-1]


def test_filter_fails_closed_when_history_is_missing():
    """Before enough FX history exists, both directions must be blocked -
    an unknown dollar is not a reason to trade."""
    i = _idx(20, freq="5min")
    closes = {"EURUSD": pd.Series(np.linspace(1.05, 1.10, 20), index=i)}
    long_ok, short_ok = usd_confirmation(closes, i, lookback_bars=8)
    assert not long_ok.iloc[0] and not short_ok.iloc[0]
    assert not long_ok.iloc[5] and not short_ok.iloc[5]


def test_gold_index_outside_fx_range_is_blocked_both_ways():
    fx_i = _idx(10, freq="15min", start="2024-01-01")
    closes = {"EURUSD": pd.Series(np.linspace(1.05, 1.10, 10), index=fx_i)}
    later = _idx(5, freq="5min", start="2025-06-01")
    long_ok, short_ok = usd_confirmation(closes, later, lookback_bars=2)
    # No FX data anywhere near these bars: drift is undefined, so no trades.
    assert not long_ok.any() and not short_ok.any()
