"""The pattern memory must never learn from an outcome before it is known."""
import pandas as pd
import pytest

from gold_bot.pa_learner import N_PATTERNS, PAConfig, PatternMemory, decode, run_online


def test_every_pattern_code_decodes():
    assert N_PATTERNS == 432
    assert "pin" in decode(1 * 72) or decode(0)
    for c in range(N_PATTERNS):
        assert isinstance(decode(c), str)


def test_few_observations_are_pulled_towards_the_global_average():
    m = PatternMemory(PAConfig(prior_k=30))
    for _ in range(200):
        m.learn(1, -0.2)
    for _ in range(3):
        m.learn(2, +2.0)                    # three lucky wins
    assert m.expected_r(2) < 0.2            # not trusted like a long record
    assert not m.allows(2)                  # and not traded: seen too rarely


def _rows(spec):
    t0 = pd.Timestamp("2022-01-01", tz="UTC")
    return pd.DataFrame([{"m5_idx": i, "dir": 1, "code": code, "sl_dist": 1.0, "R": r, "resolved": res,
                          "time": t0 + pd.Timedelta(minutes=5 * i)} for i, code, r, res in spec])


def test_an_outcome_is_not_used_before_it_resolves():
    cfg = PAConfig(prior_k=1, min_seen=2, threshold_r=0.1)
    # code 7 wins big twice, but those wins only resolve at bar 1000.
    spec = [(0, 7, 3.0, 1000), (1, 7, 3.0, 1000)]
    spec += [(i, 3, -0.5, i + 1) for i in range(2, 40)]      # background losers resolving quickly
    spec += [(50, 7, -1.0, 60)]                               # code 7 again, before bar 1000
    taken, mem = run_online(_rows(spec), cfg)
    assert 7 not in set(taken.code)                           # its wins were not known yet


def test_once_resolved_the_memory_uses_it():
    cfg = PAConfig(prior_k=1, min_seen=2, threshold_r=0.1)
    spec = [(0, 7, 3.0, 5), (1, 7, 3.0, 6)]
    spec += [(i, 3, -0.5, i + 1) for i in range(10, 40)]
    spec += [(50, 7, 1.0, 60)]
    taken, _ = run_online(_rows(spec), cfg)
    assert list(taken.code) == [7]
