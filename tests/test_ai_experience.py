"""The experience study is only meaningful if it cannot see the future."""
import numpy as np
import pandas as pd
import pytest

from gold_bot.ai_trader.experience import FEATURES, compute_features, label_trades, orient


def series(n=4200, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = 2000 + np.cumsum(rng.normal(0, 0.6, n))
    open_ = np.concatenate([[2000.0], close[:-1]])
    w = np.abs(rng.normal(0, 0.25, n))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + w,
                         "low": np.minimum(open_, close) - w, "close": close}, index=idx)


def test_features_do_not_change_when_future_bars_are_added():
    df = series()
    short = compute_features(df.iloc[:3000])[FEATURES].iloc[2500:3000].to_numpy()
    long_ = compute_features(df)[FEATURES].iloc[2500:3000].to_numpy()
    assert np.allclose(short, long_, equal_nan=True)


def test_a_short_is_the_mirror_image_of_a_long():
    f = compute_features(series()).dropna().iloc[:5]
    L, S = orient(f, 1), orient(f, -1)
    cols = {k: i for i, k in enumerate(FEATURES)}
    assert np.allclose(S[:, cols["tr15"]], -L[:, cols["tr15"]])
    assert np.allclose(S[:, cols["rsi5"]], 100 - L[:, cols["rsi5"]])
    assert np.allclose(S[:, cols["hi50"]], L[:, cols["lo50"]])      # room above <-> room below


def flat_then(path, atr_unit=1.0):
    """Quiet bars (so ATR is known), then the scripted path."""
    n0 = 60
    idx = pd.date_range("2024-01-01", periods=n0 + len(path), freq="5min", tz="UTC")
    mid = np.concatenate([2000 + 0.5 * np.sin(np.arange(n0)), np.asarray(path, float)])
    o = np.concatenate([[mid[0]], mid[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, mid) + 0.5,
                         "low": np.minimum(o, mid) - 0.5, "close": mid}, index=idx), n0 - 1


def test_a_clean_run_to_the_target_is_a_win_and_a_crash_is_a_loss():
    up, i = flat_then([2000 + k for k in range(1, 40)])
    R, J = label_trades(up, np.array([i]), 1, sl_atr=1.8, tp_atr=3.0)
    assert R[0] > 0.5 and J[0] > i
    down, i = flat_then([2000 - k for k in range(1, 40)])
    R, J = label_trades(down, np.array([i]), 1, sl_atr=1.8, tp_atr=3.0)
    assert R[0] < -0.9                                          # stopped out, plus costs


def test_costs_make_the_same_move_worth_less():
    up, i = flat_then([2000 + k for k in range(1, 40)])
    free, _ = label_trades(up, np.array([i]), 1, spread=0.0, slip=0.0)
    paid, _ = label_trades(up, np.array([i]), 1, spread=0.25, slip=0.05)
    assert paid[0] < free[0]


def test_an_example_is_only_known_after_it_resolves():
    up, i = flat_then([2000 + k for k in range(1, 40)])
    _, J = label_trades(up, np.array([i]), 1)
    assert J[0] > i                      # the store must not offer it before bar J
