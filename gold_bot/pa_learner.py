"""Price-action pattern memory: a trend-following bot that learns which candle
patterns reach their target and switches off the ones that keep losing.

How it works
------------
1. Signal bars are complete bars of a higher timeframe (M15 by default),
   built from M5 data. Only bars in the direction of the trend are
   candidates (EMA50 vs EMA200 on the signal timeframe, with slope).
2. Each candidate is encoded as a discrete PATTERN, always in the
   direction of the trend (a short is the mirror image of a long):
     - shape of the signal candle (pin bar, strong/weak with-trend body,
       strong/weak counter-trend body, tiny)
     - colour/size of the previous candle
     - engulfing or not
     - breakout of the last 10 bars or not
     - where the candle sits relative to EMA20 (pullback touched / near / far)
     - whether H4 agrees with the trend
3. Stop beyond the structure (low of the last two candles for a long),
   clamped to 0.7-3 ATR; target at a fixed multiple of the stop.
4. MEMORY: for every pattern the bot keeps the number of resolved
   occurrences and the sum of their results in R. It learns from every
   candidate it sees ("shadow trades"), not only the ones it took, because
   the outcome of a skipped setup is just as observable. A result enters
   memory only once that setup has actually reached its stop or target -
   never before - so nothing is learned from the future.
5. DECISION: the expected R of a pattern is its average result shrunk
   towards the overall average (a pattern seen 5 times is not trusted like
   one seen 300 times). The bot trades a pattern only when that estimate
   clears a threshold and it has been seen enough times.

Losing setups lower their pattern's score; enough of them switch the
pattern off. That is the "learn from losses" mechanism, and it is the same
one an MQL5 EA can run with two arrays and a file.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import atr, ema

N_C0, N_C1, N_LOC = 6, 3, 3
N_PATTERNS = N_C0 * N_C1 * 2 * 2 * N_LOC * 2          # 432
C0_NAMES = ["tiny", "pin", "strong_with", "weak_with", "strong_against", "weak_against"]
C1_NAMES = ["with", "against", "small"]
LOC_NAMES = ["touched_ema20", "near_ema20", "far_from_ema20"]


@dataclass
class PAConfig:
    tf_rule: str = "15min"
    tf_bars: int = 3                  # M5 bars per signal bar
    hours: tuple = tuple(range(7, 21))
    rr: float = 2.0                   # target = rr x stop distance
    sl_min_atr: float = 0.7
    sl_max_atr: float = 3.0
    sl_buffer_atr: float = 0.1
    spread: float = 0.25
    slippage: float = 0.05
    horizon_m5: int = 576             # give up after 2 days (marked to market)
    # learning
    prior_k: float = 30.0             # strength of the pull towards the global mean
    min_seen: int = 30
    threshold_r: float = 0.10         # trade only if expected R exceeds this


def decode(code: int) -> str:
    h4 = code % 2; code //= 2
    loc = code % N_LOC; code //= N_LOC
    bo = code % 2; code //= 2
    eng = code % 2; code //= 2
    c1 = code % N_C1; code //= N_C1
    c0 = code
    return (f"{C0_NAMES[c0]} after {C1_NAMES[c1]}{' +engulf' if eng else ''}"
            f"{' +breakout' if bo else ''} | {LOC_NAMES[loc]} | H4 {'agrees' if h4 else 'disagrees'}")


# ----------------------------------------------------------------------- bars
def signal_bars(m5: pd.DataFrame, cfg: PAConfig) -> pd.DataFrame:
    """Complete signal-timeframe bars plus the M5 row at which each one closes."""
    pos = pd.Series(np.arange(len(m5)), index=m5.index)
    g = m5.resample(cfg.tf_rule, label="left", closed="left")
    b = g.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    b["n"] = g["close"].count()
    b["m5_last"] = pos.resample(cfg.tf_rule, label="left", closed="left").max()
    b = b[b["n"] >= cfg.tf_bars].dropna()
    b["m5_last"] = b["m5_last"].astype(np.int64)
    return b.drop(columns="n")


def _h4_trend_at(m5: pd.DataFrame, when: pd.DatetimeIndex) -> np.ndarray:
    g = m5.resample("4h", label="left", closed="left")
    h4 = g.agg({"close": "last"})
    h4["n"] = g["close"].count()
    h4 = h4[h4["n"] >= 48].dropna()
    e = ema(h4["close"], 50)
    tr = np.sign(e.diff()).fillna(0.0)
    avail = pd.DataFrame({"end": h4.index + pd.Timedelta("4h"), "tr": tr.values})
    q = pd.DataFrame({"t": when})
    m = pd.merge_asof(q, avail, left_on="t", right_on="end", direction="backward")
    return m["tr"].fillna(0.0).to_numpy()


# --------------------------------------------------------------------- encode
def build_candidates(m5: pd.DataFrame, cfg: PAConfig) -> pd.DataFrame:
    b = signal_bars(m5, cfg)
    O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = atr(b, 14).to_numpy()
    e20, e50, e200 = (ema(b["close"], p).to_numpy() for p in (20, 50, 200))
    slope = np.concatenate([[0.0], np.diff(e50)])
    trend = np.where((e50 > e200) & (slope > 0), 1, np.where((e50 < e200) & (slope < 0), -1, 0))
    close_time = b.index + pd.Timedelta(cfg.tf_rule)
    h4 = _h4_trend_at(m5, close_time)
    hour = b.index.hour

    rows = []
    for i in range(12, len(b)):
        d = trend[i]
        if d == 0 or not np.isfinite(a[i]) or a[i] <= 0 or hour[i] not in cfg.hours:
            continue
        # orient: a short is the mirror image of a long
        if d == 1:
            o0, h0, l0, c0 = O[i], H[i], L[i], C[i]
            o1, c1 = O[i - 1], C[i - 1]
            hh = H[i - 10:i].max(); ema20 = e20[i]; lo2 = min(L[i], L[i - 1])
        else:
            o0, h0, l0, c0 = -O[i], -L[i], -H[i], -C[i]
            o1, c1 = -O[i - 1], -C[i - 1]
            hh = -L[i - 10:i].min(); ema20 = -e20[i]; lo2 = -max(H[i], H[i - 1])
        A = a[i]
        rng = h0 - l0
        body = c0 - o0
        br = abs(body) / rng if rng > 0 else 0.0
        lw = (min(o0, c0) - l0) / rng if rng > 0 else 0.0
        if rng < 0.3 * A:
            k0 = 0
        elif lw >= 0.55 and (c0 - l0) >= 0.6 * rng:
            k0 = 1
        elif body > 0 and br >= 0.6:
            k0 = 2
        elif body > 0:
            k0 = 3
        elif br >= 0.6:
            k0 = 4
        else:
            k0 = 5
        rng1 = abs(H[i - 1] - L[i - 1])
        b1 = c1 - o1
        k1 = 2 if rng1 <= 0 or abs(b1) / rng1 < 0.25 else (0 if b1 > 0 else 1)
        eng = int(body > 0 and b1 < 0 and c0 >= max(o1, c1) and o0 <= min(o1, c1))
        bo = int(c0 > hh)
        gap = (l0 - ema20) / A
        loc = 0 if gap <= 0 else (1 if gap <= 1.0 else 2)
        agree = int(h4[i] == d)
        code = ((((k0 * N_C1 + k1) * 2 + eng) * 2 + bo) * N_LOC + loc) * 2 + agree

        sl_dist = (c0 - (lo2 - cfg.sl_buffer_atr * A))
        sl_dist = min(max(sl_dist, cfg.sl_min_atr * A), cfg.sl_max_atr * A)
        rows.append((b["m5_last"].iloc[i], d, code, sl_dist, close_time[i]))
    return pd.DataFrame(rows, columns=["m5_idx", "dir", "code", "sl_dist", "time"])


# -------------------------------------------------------------------- outcome
def resolve(m5: pd.DataFrame, cand: pd.DataFrame, cfg: PAConfig) -> pd.DataFrame:
    """Walk every candidate forward on M5 with full costs. Adds R and the M5 index
    at which the outcome became known."""
    o = m5["open"].to_numpy(); h = m5["high"].to_numpy(); l = m5["low"].to_numpy(); c = m5["close"].to_numpy()
    n = len(c)
    half, slip = cfg.spread / 2, cfg.slippage
    R = np.full(len(cand), np.nan); J = np.full(len(cand), -1, dtype=np.int64)
    for k, (i, d, sd) in enumerate(zip(cand["m5_idx"].to_numpy(), cand["dir"].to_numpy(), cand["sl_dist"].to_numpy())):
        if i + 1 >= n:
            continue
        entry = c[i] + d * (half + slip)
        sl, tp = entry - d * sd, entry + d * cfg.rr * sd
        px, j = None, i
        for j in range(i + 1, min(i + 1 + cfg.horizon_m5, n)):
            if d == 1:
                bo_, bh, bl = o[j] - half, h[j] - half, l[j] - half
                if bo_ <= sl: px = bo_ - slip; break
                if bo_ >= tp: px = bo_; break
                if bl <= sl: px = sl - slip; break
                if bh >= tp: px = tp - slip; break
            else:
                ao, ah, al = o[j] + half, h[j] + half, l[j] + half
                if ao >= sl: px = ao + slip; break
                if ao <= tp: px = ao; break
                if ah >= sl: px = sl + slip; break
                if al <= tp: px = tp + slip; break
        if px is None:
            j = min(i + cfg.horizon_m5, n - 1)
            px = c[j] - d * (half + slip)
        R[k] = d * (px - entry) / sd
        J[k] = j
    out = cand.copy()
    out["R"], out["resolved"] = R, J
    return out[np.isfinite(out["R"])].reset_index(drop=True)


# ------------------------------------------------------------------- learning
class PatternMemory:
    def __init__(self, cfg: PAConfig):
        self.cfg = cfg
        self.n = np.zeros(N_PATTERNS)
        self.s = np.zeros(N_PATTERNS)

    def learn(self, code: int, r: float) -> None:
        self.n[code] += 1
        self.s[code] += r

    def expected_r(self, code: int) -> float:
        tot_n = self.n.sum()
        g = self.s.sum() / tot_n if tot_n else 0.0
        k = self.cfg.prior_k
        return (self.s[code] + k * g) / (self.n[code] + k)

    def allows(self, code: int) -> bool:
        return self.n[code] >= self.cfg.min_seen and self.expected_r(code) > self.cfg.threshold_r


def run_online(res: pd.DataFrame, cfg: PAConfig, warmup_until=None):
    """Replay in time order. Memory is updated only from setups already resolved.

    Returns (taken trades, memory). One position at a time.
    """
    mem = PatternMemory(cfg)
    pending: list = []                       # (resolved_idx, code, R)
    taken = []
    busy_until = -1
    for row in res.itertuples(index=False):
        while pending and pending[0][0] <= row.m5_idx:
            _, code, r = heapq.heappop(pending)
            mem.learn(code, r)
        live = warmup_until is None or row.time >= warmup_until
        if live and row.m5_idx > busy_until and mem.allows(row.code):
            taken.append((row.time, row.code, row.dir, row.R, mem.expected_r(row.code), mem.n[row.code]))
            busy_until = row.resolved
        heapq.heappush(pending, (row.resolved, row.code, row.R))
    t = pd.DataFrame(taken, columns=["time", "code", "dir", "R", "expected", "seen"])
    return t, mem
