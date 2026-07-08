# Gold (XAUUSD) M5 Trading Bot

A trend-pullback trading bot for XAUUSD on the 5-minute chart, built around
MetaTrader 5, with a bar-by-bar backtester that shares its risk-management
code with the live trader so simulated and real behaviour never drift apart.

## Disclaimer

No trading strategy is guaranteed to be profitable. Gold on M5 is noisy and
spread/slippage costs are real. Backtest results here use synthetic data for
smoke-testing the engine only — **you must backtest on real historical
XAUUSD M5 data and forward-test on a demo account before ever risking real
money.** Past backtest performance never guarantees future results.

## Native MQL5 Expert Advisor (paste directly into MT5)

`mql5/GoldBot_FixedCapitalRisk.mq5` is the **recommended** native MetaTrader
5 EA port of the strategy (trend filter, RSI pullback, MACD confirmation,
session filter, trend-strength filter, hour-9-UTC exclusion, ATR
stop/target/trailing, `fixed_capital_percent_risk` sizing) - no Python
needed, just open in MetaEditor, compile, and attach to an XAUUSD M5 chart.
Sizing is anchored to a fixed `InpBaseEquity` ($500 by default) and
`InpRiskPercentPerTrade` (2% by default), never the live account equity -
see "Fixed-capital sizing" above for why.

`mql5/GoldBot_LotStep_0.05.mq5`, `_0.06.mq5`, and `_0.08.mq5` are an older
port using `equity_step` sizing (lot compounds off accumulated profit).
**Not recommended** - a real MT5 Strategy Tester run on this exact sizing
hit 100% drawdown because early wins inflated the lot size right before a
losing streak hit it. Kept in the repo for reference only; use
`GoldBot_FixedCapitalRisk.mq5` instead. The three legacy files are
identical except for `InpBaseLot`/`InpLotStep`/`InpMinLot`.

**Read this before using any of them:**
- These files were written to match the validated Python logic but have
  **not been compiled or run in MetaTrader** (no Windows/MT5 in the
  authoring environment). Compile in MetaEditor, run in Strategy Tester
  over real XAUUSD M5 history, and forward-test on a demo account before
  ever attaching to a real account - same rule as any new EA, doubly so
  here.
- Set `InpBrokerUtcOffsetHours` to your broker server's offset from UTC
  (varies by broker, commonly GMT+2 or GMT+3) - the session and hour
  filters are computed in UTC and need this to line up correctly.
- **The 0.06 and 0.08 lot-step files are here because they were
  requested, not because they're recommended.** Backtested on the same
  5-year data: 0.05/$100 gives +1,755.84% with -34.58% drawdown; 0.06
  gives +5,486.25% with -48.63% drawdown; 0.08 gives +12,897.97% with
  -62.19% drawdown. Past a lot-step of roughly 0.02-0.03, this is compound
  growth outrunning what any retail account can actually execute - by the
  time equity reaches the tens of thousands, the required lot size per
  trade implies notional exposure and order sizes real market liquidity
  and broker margin limits won't support, and the backtest doesn't model
  that ceiling. `config/config.yaml`'s default (0.02) is the tested,
  reasoned choice; 0.05+ is included on request but not endorsed.

## Strategy

See the docstring in `gold_bot/strategy.py` for full rationale. Summary:

1. **Trend filter (two timeframes)** — price must be above/below a slow EMA
   on M5 *and* the higher timeframe (default M15) must agree, to filter out
   most chop.
2. **Pullback trigger** — RSI must have recently dipped toward oversold
   (uptrend) or overbought (downtrend), then cross back through a mid-level,
   i.e. trade the resumption of the trend after a shallow pullback rather
   than blindly trading every EMA cross.
3. **Momentum confirmation** — MACD histogram must be turning back in the
   trend direction on the signal bar.
4. **Session filter** — entries only fire during London / New York session
   windows (configurable, UTC), since gold's real momentum is concentrated
   there; outside these windows M5 moves are mostly spread/noise.
5. **Trend strength filter** (`min_trend_strength_pct`) — skip entries when
   the EMA fast/slow gap (as % of price) is too small, i.e. right at an EMA
   cross where the "trend" isn't really established yet. Found by analyzing
   264 backtested trades for what actually separated winners from losers
   (see "Data-driven trend-strength filter" below) and validated on both the
   flat 2020-2023 period and the trending 2023-2025 period independently -
   this is the single biggest improvement found in this project, bigger than
   any parameter-grid tuning round.

Exits: ATR-based stop loss and take profit, with an optional ATR chandelier
trailing stop. All of these multipliers, EMA/RSI periods, and session times
are configurable in `config/config.yaml`.

### Data-driven trend-strength filter (the actual answer to "why so many losses")

Rather than guessing at another indicator, the losing trades themselves were
analyzed (264 trades from the tuned baseline: hour of day, day of week,
month, ADX at entry, ATR/volatility at entry, EMA-fast/slow gap, MACD
histogram magnitude) to find what actually separated winners from losers.
Three candidate filters came out of that analysis; each was backtested on
real data before adopting anything:

- **Skip Monday entries** (worst day, 25.6% win rate vs. 41-54% Tue-Fri):
  PF 1.45 -> 1.60, +61.62% -> +78.99%, but a small enough sample (only
  Mondays) to be more suspect as noise.
- **Skip lowest-ATR-quartile entries** ("dead market" filter): no real
  effect (PF 1.45 -> 1.46) - discarded.
- **Skip lowest-quartile EMA-gap entries** (require the trend to be a real
  gap, not right at the cross): PF 1.45 -> **2.10**, +61.62% ->
  **+109.39%**, drawdown *improved* -14.13% -> -8.79%.

The EMA-gap filter was the clear winner and was validated on the train/test
split used earlier in this project (2020-08 to 2023-08 / 2023-08 to
2025-08), computing the threshold from train data only to rule out
lookahead:

| Period | Baseline PF | Baseline return | + Filter PF | + Filter return |
|---|---|---|---|---|
| Train (flat/choppy 2020-2023) | 1.09 | +5.82% | **1.35** | **+14.52%** |
| Test (trending 2023-2025) | 1.87 | +53.21% | **2.47** | **+43.99%** |

This is the first change in the whole project that improved *both* the
choppy period and the trending period independently, rather than trading
one off against the other - strong evidence it isn't overfit to a single
regime. It's now the default (`min_trend_strength_pct: 0.0256` in
`config/config.yaml`). Full 5-year backtest with current defaults:

| Metric | Before this filter | After |
|---|---|---|
| Trades | 264 | 173 |
| Win rate | 44.7% | 49.71% |
| Profit factor | 1.45 | **2.10** |
| Total return (equity_step, $500) | +61.62% | **+109.39%** |
| Max drawdown | -14.13% | **-8.79%** |

### Second round on the filtered trade set - one more real filter, one overfit trap avoided

Ran the same losing-trade analysis again on the new 173-trade set (after
the EMA-gap filter) to check for further gains. Three more candidates,
each backtested before deciding:

- **Skip hour=9 (UTC) entries**: this hour sits inside the London session
  window but had a 20% win rate (n=10) vs. 35-58% for other hours -
  plausibly because common EU/UK scheduled data releases (PMI, ZEW, etc.)
  land around 09:00-09:30 UTC and whipsaw price right as the
  pullback/momentum signal fires. PF 2.10 -> **2.35**, +109.39% ->
  **+127.78%**, drawdown -8.79% -> -8.6%. **Adopted** as
  `excluded_hours: [9]` in `config/config.yaml`.
- **Skip lowest-ATR-quartile entries** (retested on the filtered set): no
  real effect (PF 2.10 -> 2.12) - discarded again.
- **Skip weak calendar months** (June/February/December had the worst win
  rates in this dataset): looked great in isolation (PF 2.10 -> 2.44) and
  even better combined with the other two filters (PF 3.18, +197.24%) -
  **deliberately NOT adopted**. With only 5 years of data, each calendar
  month has just 5 observations - nowhere near enough to distinguish a
  real seasonal effect from noise in one particular historical window,
  unlike the EMA-gap and hour-of-day filters which had much larger samples
  and a plausible mechanism. Flagging a good-looking backtest result as
  probably fake is exactly the discipline this project has tried to keep -
  don't add this filter without many more years of data confirming it.

`min_trend_strength_pct: 0.0256` + `excluded_hours: [9]` (both still in
`config/config.yaml`, but predating the confluence filters and
fixed-capital sizing added later - see "Confluence filters" and
"Fixed-capital sizing" below for what's actually in the current default)
give, full 5-year backtest with the then-current `equity_step` sizing:

| Metric | Value |
|---|---|
| Trades | 163 |
| Win rate | 51.53% |
| Profit factor | **2.35** |
| Total return (equity_step, $500) | **+127.78%** |
| Max drawdown | -8.6% |

### Mean-reversion experiment (disabled by default - read before enabling)

`gold_bot/strategy.py` also contains a Bollinger Band mean-reversion rule
set, gated behind `enable_mean_reversion: false` in `config/config.yaml`,
intended to trade the flat/choppy stretches (like 2020-2023, see below)
where the trend-pullback rules above lose money. **It is off by default
because it tested net negative on the real 5-year dataset in two different
tunings** (basic band-touch-and-return, and a stricter version with a
stable-ADX requirement, minimum band-width filter, and exit at the band
midpoint instead of a fixed ATR target). Both had a higher win rate than
the trend strategy (50-52%) but a worse profit factor (0.85-0.93), because
average losses were larger than average wins - mean-reversion stops have
to survive a further band-extension before reverting, while the target
(back to the band edge/midpoint) is comparatively close. Enabling it as
implemented would have turned the combined +22.12% / 5yr result into
**-44.32%**. The code is left in place, disabled, for further
experimentation (candidates: statistical z-score entries instead of raw
band touches, volatility-regime-aware position sizing, or simply
accepting the trend strategy sits out choppy markets rather than losing
money trying to trade them) - do not flip it on without re-running
`scripts/run_backtest.py` on real data first.

### More trades vs. higher win rate - tested, and they trade off against each other

A natural next ask is "add more indicators to catch more setups *and* win
more of them." Tested two concrete versions of that on the real 5-year
dataset (not merged into the codebase since both lost to the baseline):

- **Stochastic oscillator as an additional pullback trigger** (fire a
  trend entry when %K crosses %D from oversold/overbought, alongside the
  existing RSI trigger): trade count went 264 -> 1,747, but profit factor
  dropped from 1.45 to 0.95 and the 5-year result flipped from +61.62% to
  **-35.96%** with an -86.11% drawdown. Stochastic %K/%D crosses are too
  frequent/noisy on M5 to use as a standalone trigger.
- **ADX-rising filter** (only take a trend entry while ADX is
  increasing, i.e. the trend is strengthening rather than fading): win
  rate improved 44.7% -> 50.94% and profit factor improved 1.45 -> 1.51,
  confirming the filter does select higher-quality setups - but trade
  count collapsed 264 -> 53, so the 5-year total return dropped from
  +61.62% to +11.44% despite the better per-trade quality. Too few
  compounding opportunities.

The pattern across every experiment in this README (mean-reversion, ADX
gating on trend entries, Stochastic, ADX-rising) is consistent: loosening
entry criteria to get more trades consistently destroys quality faster
than the extra volume compensates, and tightening criteria to raise win
rate consistently removes more trades than the quality gain compensates
for. For this strategy structure, asset, and timeframe, the current
config is the best point found after this search - "just add another
indicator" is not a free lever here, each addition needs the same
real-data backtest scrutiny before being trusted.

### Other "pro trader" price-action methods - all tested, all worse

Tested several well-known gold day-trading price-action methods
standalone on the real 5-year dataset, using the same ATR-based
stop/target/trailing risk management as the main strategy for a fair
comparison (not merged into the codebase - all lost badly):

| Method | Profit factor | 5yr result |
|---|---|---|
| Current strategy (baseline) | 1.45 | +61.62% |
| Opening Range Breakout (London, 07:00-07:25 UTC) | 0.62 | account wiped out |
| ORB + HTF trend filter | 0.64 | account wiped out |
| Previous-day high/low breakout (continuation) | 0.76 | account wiped out |
| PDH/PDL breakout + HTF trend filter | 0.69 | account wiped out |
| PDH/PDL fade (reversal) | 0.84 | account wiped out |
| Liquidity sweep / stop-hunt reversal (20-bar swing) | 0.80 | account wiped out |
| Liquidity sweep + HTF trend filter | 0.77 | account wiped out |
| VWAP bounce (daily-anchored) | 0.74 | account wiped out |

### "Catch the top/bottom" reversal methods - all tested, all worse still

A second sweep specifically of the popular top/bottom-picking reversal
methods, run through the identical engine, risk management
(`fixed_capital_percent_risk` 2% of $500), ATR stop/target/trailing, and
London/NY session filter as the main strategy:

| Method | Trades | Win % | Profit factor |
|---|---|---|---|
| **Main trend-pullback strategy (baseline)** | **163** | **51.5%** | **1.69** |
| RSI 70/30 cross-back reversal | 2,928 | 35.9% | 0.77 |
| RSI 80/20 cross-back reversal (stricter) | 564 | 34.6% | 0.71 |
| Bollinger(20,2) band fade | 4,653 | 34.8% | 0.75 |
| Pin bar (wick >60%) at 20-bar high/low | 3,187 | 35.1% | 0.72 |
| Engulfing candle at 20-bar high/low | 2,489 | 36.1% | 0.79 |
| RSI divergence at 50-bar price extreme | 3,244 | 36.0% | 0.75 |
| Stochastic 80/20 cross-back reversal | 4,783 | 34.1% | 0.73 |
| Double top/bottom retest + rejection candle | 4,633 | 34.6% | 0.72 |

Every reversal method landed in a remarkably tight band: 34-36% win rate,
profit factor 0.71-0.79, all of which wipe the account many times over
across the 5-year test. Even making the extreme stricter (RSI 80/20
instead of 70/30 - fewer, "higher-conviction" signals) did not lift the
win rate at all. The structural reason is simple: gold on M5 trends hard,
so "price at a local extreme" is far more often trend continuation than a
top/bottom, and a counter-trend entry with a fixed-multiple ATR stop gets
run over. Any method marketed as picking gold tops with a high win rate
should be assumed false until it survives this exact test.

A follow-up idea - use the top/bottom signals not as entries but as an
**early-exit filter** on the main strategy (close a long early when RSI
hits overbought, or when price closes beyond the upper Bollinger band, on
the theory that the extreme marks the end of the move) - was also tested
and also failed, just mildly instead of catastrophically:

| Exit rule added to main strategy | PF | Return/5yr | Max DD | Early exits |
|---|---|---|---|---|
| None (baseline) | **1.69** | **+55.8%** | **-12.7%** | 0 |
| RSI ≥65 / ≤35 | 1.65 | +51.9% | -14.2% | 26 |
| RSI ≥70 / ≤30 | 1.62 | +49.5% | -14.6% | 8 |
| RSI ≥75 / ≤25 | 1.64 | +51.3% | -13.6% | 4 |
| RSI ≥80 / ≤20 | 1.66 | +53.3% | -13.6% | 1 |
| Close beyond Bollinger(20,2) | 1.60 | +47.6% | -14.0% | 29 |

Every variant reduced both return and profit factor AND slightly worsened
drawdown - the extremes the filter exits on are more often mid-trend
strength (which the ATR trailing stop would have ridden further) than
actual tops. The trailing stop already is the well-calibrated exit; adding
a reversal-based exit on top only cuts winners short. Consistent with the
entry-side sweep above: on gold M5, "price at an extreme" is not
usable information, in either direction, at entry or at exit.

Adding the same HTF-trend filter used by the main strategy to ORB,
liquidity-sweep, and PDH/PDL breakout did not rescue any of them (profit
factor stayed under 1). This is a useful negative result: it shows the
edge in this codebase isn't just "have a trend filter," it's the specific
combination already tuned here (RSI shallow-pullback entry + MACD
momentum confirmation + wide ATR stop + tight ATR trailing + session
filter). Swapping in a different, individually-reputable entry pattern
while keeping everything else the same does not transfer that edge.

While stress-testing these (all of them lose fast enough to draw the
account toward zero), a real gap was found and fixed in
`gold_bot/risk_manager.py`: `RiskManager.can_open_trade` did not check for
depleted/negative equity, so a sufficiently bad strategy under
`equity_step` sizing (which floors at `min_lot` regardless of equity)
could keep opening new trades even after the account was wiped out,
never converging to a stop. It now refuses to open any new trade once
equity reaches zero or below.

### Loss post-mortem - reviewing every losing trade like a trading journal

After the confluence filters were adopted, all 131 trades of the current
default config were logged with their full entry context (hour, weekday,
direction, session, RSI/ADX/trend-strength/ATR-regime at entry, distance
from EMA) and winners were compared against losers - the way a
professional reviews a trading journal to fix recurring mistakes.

Patterns found in the losers, each then validated on the train/test
split (the post-mortem sample is only 131 trades, so every "pattern" had
to prove itself out-of-sample before being trusted):

| Candidate fix from the loss review | Train | Test | Verdict |
|---|---|---|---|
| Skip hour 7 UTC (16 trades, 37.5% win, -$17) | PF 1.51→1.64, DD -10.0→-7.9 | PF 2.99→3.58, DD -3.3→-2.5 | **ADOPTED** |
| Skip London entirely, NY-only | PF up, net up | PF up, **net down** ($190→$164) | rejected |
| Skip Monday (28.6% win) | PF up, net up | **PF down** (2.99→2.77) | rejected |
| Skip trend_strength > 0.12 ("late" entries) | **net down** | PF up | rejected |
| Require high-ATR regime (rolling percentile) | PF up | **PF/net down** | rejected |

Entry-timing refinements were tested the same way - could entries be
"more precise" by waiting for a better price?

| Entry timing variant | Full-period result | Verdict |
|---|---|---|
| Enter on signal close (current) | PF 2.01, +$286 | baseline |
| Wait for next-bar confirmation | train much better, test worse (PF 2.99→2.28) | rejected - inconsistent |
| Limit order -0.25 ATR retracement | PF 1.58, +$152 | rejected |
| Limit order -0.40 ATR retracement | PF 1.39, +$95 | rejected |
| Wait for EMA-fast retest (12 bars) | PF 1.07, +$17 | rejected |

The retracement results are worth internalizing: every "get a better
price" variant made things *worse*, because the strongest moves - the
ones producing the big winners - never come back to fill a limit order.
Waiting for a discount systematically filters you OUT of the best trades
and INTO the marginal ones. Chasing entry-price perfection is a losing
refinement for a trend-following entry.

The one adopted fix - excluding hour 7 UTC (London open, first-hour
whipsaw) alongside the previously-validated hour 9 (EU/UK data releases)
- moves the full 5-year default result to:

| Metric | Before (hour 9 only) | After (hours 7+9) |
|---|---|---|
| Trades | 131 | 115 |
| Win rate | 54.96% | 57.39% |
| Profit factor | 2.01 | **2.26** |
| Total return | +57.21% | **+60.62%** |
| Max drawdown | -9.97% | **-7.90%** |

(Confirmed identical in both the numpy sweep engine and
`gold_bot/backtester.py`.) Note it improves *net dollars* as well as
quality metrics - the 16 excluded trades were net losers, not just
low-quality winners.

### Multi-trigger experiment - trying to raise trade count without losing quality

131→115 trades in 5 years (~2/month) prompted the obvious question: can
more entries be added without destroying the edge? Instead of loosening
the existing entry, five ADDITIONAL independent triggers were tested
*inside the full validated filter stack* (M5+M15+H1 trend alignment,
session, hour exclusions, trend-strength, ATR expansion) - the idea being
each trigger catches a different kind of entry within the same
high-quality context:

| Trigger (inside the same filter stack) | Trades | Win % | PF | Net/5yr |
|---|---|---|---|---|
| T1 RSI pullback cross (current entry) | 115 | 57.4% | **2.26** | **+$303** |
| T2 MACD histogram sign flip | 959 | 36.4% | 0.87 | -$396 |
| T3 Stochastic K/D cross | 825 | 37.7% | 0.92 | -$215 |
| T4 price reclaims EMA-fast | 887 | 39.2% | 1.02 | +$43 |
| T5 relaxed T1 (lookback 12, threshold +10) | 469 | 41.8% | 1.08 | +$109 |
| T6 inside-bar breakout | 1,051 | 35.7% | 0.86 | -$476 |

Every union of triggers was also tested (T1+T2, T1+T4, T1+T2+T3, ... on
full/train/test): **all of them collapse the result** - the best union
(T1+T4) manages PF 1.03 and +$67, because T1's 115 good trades get
drowned in ~800 mediocre ones. Even T5, which is just the current entry
with slightly relaxed thresholds, drops PF from 2.26 to 1.08 while
quadrupling trades.

Two conclusions worth keeping:
1. **The filter stack is not the edge.** The exact pullback-resume
   sequence of the current entry (recent oversold → RSI crosses back with
   MACD confirming) is the edge; the filters only refine it. Running
   *other* textbook triggers through the same filters produces
   noise-level results (PF 0.86-1.08), which is why "just add more
   setups" fails.
2. **~2 trades/month at PF 2.26 is what a real after-cost edge on gold M5
   looks like.** The honest ways to get more out of it are raising risk
   per trade within the documented drawdown tradeoffs (see the
   fixed-capital risk table), not adding lower-quality entries - every
   variant of "more trades" tested in this project has ended at PF ≤ 1.1.

### R:R ratio and pyramiding - one adopted, one rejected

Two more pro-trader ideas were tested: (1) widen the reward:risk ratio to
5:3 (~1.67, vs. the previous 2.5:2.0 ATR = 1.25) so winners pay more per
trade, and (2) pyramiding ("nhồi lệnh") - add a second/third position
when a trade is moving favorably, on the theory that a confirmed trend
deserves more size. Both were cross-checked against
`gold_bot/backtester.py` (exact match) before trusting results.

**R:R ratio 5:3** - several ATR magnitudes at that ratio, single position:

| SL / TP (ATR) | Full PF | Full net | Train PF | Test PF |
|---|---|---|---|---|
| 2.0 / 2.5 (previous, ratio 1.25) | 2.26 | +$303 | 1.64 | 3.58 |
| 1.2 / 2.0 (ratio 1.67) | 1.62 | +$298 | 1.11 | 2.71 |
| **1.8 / 3.0 (ratio 1.67, adopted)** | **2.28** | **+$348** | **1.78** | 3.28 |
| 2.4 / 4.0 (ratio 1.67) | 2.07 | +$223 | 1.53 | 3.29 |
| 3.0 / 5.0 (ratio 1.67) | 2.24 | +$199 | 1.68 | 3.51 |

`1.8/3.0` improves PF and net dollars on both the full period and train
independently (1.64→1.78), and while test PF eased slightly (3.58→3.28,
still excellent), test net dollars actually improved ($198→$207) since
the wider stop lets fewer trades get shaken out early. Adopted as the new
`atr_sl_mult`/`atr_tp_mult` default. The other magnitudes at the same
ratio are worse or inconsistent, confirming this isn't just "any 5:3
works" - the absolute ATR distance matters as much as the ratio.

**Pyramiding** - add a leg (each risking a smaller % of the fixed $500 so
total risk stays capped) when price extends further in favor, moving
earlier legs' stops to breakeven once a new leg opens:

| Variant | Full PF | Full net | Train PF | Test PF |
|---|---|---|---|---|
| Baseline: 1 leg, 2% risk | 2.26 | +$303 | 1.64 | 3.58 |
| 2 legs, 1% each, add at +1.0 ATR | 1.50 | +$129 | 1.16 | 2.03 |
| 3 legs, 0.67% each, add at +1.0 ATR | 1.53 | +$107 | 1.15 | 2.14 |
| 2 legs, 1% each, add at +1.5 ATR | 1.49 | +$103 | 1.32 | 1.70 |

Every pyramiding variant **cuts profit factor and net profit roughly in
half in every period** - full, train, and test all agree, no exceptions.
The reason is the same one found in the multi-trigger experiment: adding
a leg requires more trades to fire, and the additional trades are lower
quality than the original entry (win rate on the pyramid legs drags the
blended win rate from 57% down to 45-48%). The tight 1.0-ATR trailing
stop that already lets winners run is doing the "let it ride" job
pyramiding is meant to do - stacking more entries on top just adds noise.
**Rejected**, not adopted.

### TradingView indicator sweep - 12 popular indicators tested as confirmation filters

Every indicator implemented from scratch (matching TradingView's standard
formulas) that hadn't already been tried in this project - SuperTrend,
Ichimoku Cloud (price vs. Kumo + Tenkan/Kijun cross), Parabolic SAR, CCI,
Williams %R, Hull Moving Average slope, Vortex Indicator, Keltner
Channel position, Donchian breakout, Awesome Oscillator, and DMI (+DI/-DI)
- was layered as a confirmation filter on top of the full validated entry
(same method that found H1+ATR-expansion worked earlier):

| Indicator filter | Trades | Win % | PF | Return/5yr | Max DD |
|---|---|---|---|---|---|
| **None (baseline)** | **115** | **57.4%** | **2.28** | **+69.6%** | **-9.2%** |
| SuperTrend agrees | 28 | 57.1% | 2.40 | +20.5% | -5.4% |
| Ichimoku (price vs. cloud) | 59 | 54.2% | 1.92 | +28.7% | -7.4% |
| Ichimoku Tenkan/Kijun cross | 8 | 50.0% | 1.29 | +1.6% | -5.2% |
| Parabolic SAR agrees | 90 | 54.4% | 1.98 | +44.8% | -9.7% |
| CCI > 0 / < 0 | 53 | 47.2% | 1.36 | +11.1% | -8.5% |
| CCI not extreme (±150) | 108 | 57.4% | 2.17 | +60.6% | -9.8% |
| Williams %R not extreme | 115 | 57.4% | 2.28 | +69.6% | -9.2% |
| Hull MA slope agrees | 52 | 51.9% | 1.72 | +20.0% | -8.4% |
| Vortex VI+/VI- agrees | 19 | 52.6% | 1.95 | +10.9% | -3.7% |
| Keltner Channel mid position | 113 | 57.5% | 2.28 | +68.3% | -9.2% |
| Awesome Oscillator agrees | 3 | 100% | ∞ | +8.6% | -1.2% |
| DMI (+DI/-DI) agrees | 60 | 55.0% | 2.17 | +36.1% | -8.9% |

None beat the baseline on a risk-adjusted basis after accounting for
sample size. **SuperTrend** looked like the standout (PF 2.40 > 2.28) and
was checked on train/test the way every other candidate in this project
has been:

| | Trades | PF | Return |
|---|---|---|---|
| Train, baseline | 67 | 1.78 | +28.2% |
| Train, +SuperTrend | 19 | 2.42 | +13.9% |
| Test, baseline | 48 | 3.28 | +41.3% |
| Test, +SuperTrend | 9 | 2.37 | +6.6% |

**Rejected.** The higher PF is real but comes entirely from throwing away
trades - 9 trades in a 2-year test window is far too small to trust (the
same failure mode as the earlier-rejected "ADX rising" filter), and net
profit collapses in both periods despite the prettier PF. Williams %R and
Keltner-mid barely filtered anything (115→115 and 113 trades) - not
useful, just redundant with conditions the entry already implies. CCI,
Ichimoku, Parabolic SAR, Hull MA, Vortex, and DMI all reduce both trade
count and profit factor together - straightforward net negatives, no
train/test check needed to reject them.

**Conclusion, consistent with every other filter search in this
project:** classic TradingView trend/momentum indicators mostly measure
the same underlying trend the current EMA/RSI/MACD stack already
captures, so requiring them to "agree" just prunes trades without adding
real information - and the ones that prune enough to look good on PF do
so by shrinking the sample past the point of being trustworthy.

### Trailing-stop tuning and partial profit-taking - both confirm current settings are already near-optimal

Two more exit-side ideas were tested against the 1.8/3.0 SL/TP default:

**Trailing-stop ATR multiplier** (currently 1.0):

| trail_mult | Full PF | Train PF | Test PF |
|---|---|---|---|
| 0.7 (tighter) | 1.71 | 1.23 | 2.55 |
| 0.85 | 2.00 | 1.44 | 3.14 |
| **1.0 (current)** | **2.28** | **1.78** | **3.28** |
| 1.2 | 2.06 | 1.63 | 2.90 |
| 1.5 (looser) | 1.65 | 1.39 | 2.15 |

1.0 is the peak in all three periods independently - tighter cuts winners
short before they run, looser gives back too much on the way down.
Already optimal; not changed.

**Partial profit-taking** (scale out 50% at N× initial risk, move the
remaining 50%'s stop to breakeven, let it trail) - the mirror image of
pyramiding: locks in profit and *reduces* risk instead of adding it:

| Scale-out level | Full PF | Full net | Train PF | Test PF |
|---|---|---|---|---|
| None (current) | **2.28** | **+$348** | **1.78** | 3.28 |
| 50% at 0.5R | 1.37 | +$91 | 1.11 | 1.90 |
| 50% at 0.75R | 1.85 | +$219 | 1.40 | 2.76 |
| 50% at 1.0R | 2.03 | +$276 | 1.47 | 3.17 |
| 50% at 1.5R | 2.14 | +$308 | 1.57 | 3.26 |

Scaling out gets closer to the baseline as the trigger level rises (1.5R
is close but still below baseline on every metric), never beats it. The
1.0-ATR trailing stop is already capturing full winners better than any
scale-out variant that gives up half the position early. Confirms: don't
fix what isn't broken - the current exit logic is a local optimum among
everything tested here.

### Multi-pair test - the edge does not transfer to FX pairs

The identical framework (same entry, same filter stack, timeframes mapped
one step up: signals on M15, HTF M15→H1, HTF2 H1→H4) was run on 10 years
(2012-2022) of real M15 data for 11 FX pairs plus XAUUSD-M15 as the
reference row, $500 fixed capital, 2%/trade, per-pair typical retail
spreads. Data: public ejtraderLabs/historical-data dataset
(`data/*_m15.csv`), MT5 server timestamps normalized to UTC, integer
price scaling normalized per pair.

| Pair | Trades | Win % | PF | Net/10yr | Train PF | Test PF | Verdict |
|---|---|---|---|---|---|---|---|
| AUDUSD | 100 | 41.0% | 1.10 | +$29 | 1.22 | 0.92 | fail (test loses) |
| XAUUSD M15 (reference) | 145 | 35.9% | 1.03 | +$13 | 1.01 | 1.08 | breakeven |
| GBPJPY | 115 | 33.0% | 0.82 | -$76 | 0.79 | 0.89 | fail |
| AUDJPY | 78 | 35.9% | 0.79 | -$57 | 0.58 | 1.28 | fail |
| USDCHF | 151 | 33.1% | 0.68 | -$193 | 0.86 | 0.47 | fail |
| EURGBP | 172 | 32.6% | 0.64 | -$249 | 0.78 | 0.41 | fail |
| USDCAD | 195 | 32.3% | 0.62 | -$291 | 0.60 | 0.66 | fail |
| GBPUSD | 148 | 27.7% | 0.59 | -$247 | 0.58 | 0.62 | fail |
| USDJPY | 119 | 31.9% | 0.55 | -$200 | 0.58 | 0.52 | fail |
| EURUSD | 129 | 27.9% | 0.54 | -$234 | 0.65 | 0.44 | fail |
| EURJPY | 97 | 32.0% | 0.53 | -$163 | 0.61 | 0.41 | fail |
| EURCHF | 113 | 25.7% | 0.50 | -$247 | 0.32 | 0.92 | fail |

To rule out "the gold-tuned filters are killing the FX pairs," the sweep
was repeated with the core strategy only (no trend-strength threshold, no
hour exclusions, no H1 confirmation, no ATR expansion): **every symbol got
worse**, including XAUUSD-M15 itself (1.03 → 0.78). The filters help
everywhere; the core edge simply doesn't exist outside gold.

Conclusions:
1. **No FX pair is suitable for this strategy.** The best (AUDUSD)
   is marginal on the full period and loses in the test split. EURUSD -
   the most-traded pair in the world - is one of the worst (PF 0.54, 27.9%
   win rate), consistent with the well-known tendency of FX majors to
   mean-revert intraday, which is fatal for a trend-pullback entry.
2. **Even gold itself is only breakeven on M15 with M5-tuned
   parameters** - the edge is specific to the symbol AND the timeframe
   (and possibly the 2020-2025 regime; the M15 test covers 2012-2022).
   "It works on gold M5" does not mean "it works on gold."
3. Caveats: FX results use approximate constant quote→USD conversion
   rates and assumed typical retail spreads; MT5 server-time normalization
   ignores DST (±1h on session boundaries). None of these approximations
   is remotely large enough to flip PF 0.5-0.8 into profitability.

### Scalping methods - all tested with real costs, 9 of 10 lose money

A sweep of scalping-style methods (small ATR targets, max hold 1 hour, up
to 10 trades/day) on the same real 5-year M5 data, $500 fixed capital, 1%
risk per trade, real costs (25-point spread + 5-point slippage per side).
The backtest engine for this sweep was cross-checked against
`gold_bot/backtester.py` on an identical config (exact match: 131 trades,
PF 1.29, $687.50 final) before any result below was trusted.

| Method (TP/SL in ATR, hold ≤ 12 bars) | Trades | Win % | PF | Net/5yr | Max consec. losses | Worst month |
|---|---|---|---|---|---|---|
| Main-signal entries, TP 0.5/SL 1.0 | 137 | 66.4% | 0.88 | -$29 | 4 | -$21 |
| Main-signal entries, TP 1.0/SL 1.0 | 136 | 54.4% | 1.10 | +$31 | 5 | -$21 |
| EMA5/20 cross scalp, TP 0.5 | 2,426 | 56.3% | 0.58 | -$2,277 | 7 | -$104 |
| EMA5/20 cross scalp, TP 1.0 | 2,389 | 43.3% | 0.71 | -$1,982 | 13 | -$117 |
| Momentum burst (body>1.2 ATR), TP 0.5 | 3,769 | 55.0% | 0.55 | -$3,942 | 11 | -$163 |
| 12-bar breakout scalp, TP 0.5 | 8,017 | 55.7% | 0.57 | -$7,878 | 12 | -$240 |
| 12-bar breakout scalp, TP 1.0 | 6,929 | 42.9% | 0.70 | -$6,073 | 16 | -$224 |
| EMA20 touch-and-go, TP 0.75 | 7,027 | 49.5% | 0.68 | -$5,877 | 11 | -$219 |
| Stochastic 30/70 trend scalp, TP 0.5 | 2,200 | 57.1% | 0.61 | -$1,923 | 7 | -$115 |
| Stochastic trend scalp, TP 1.0 | 2,166 | 44.1% | 0.74 | -$1,633 | 16 | -$95 |
| **Main strategy (not scalping, baseline)** | **131** | **55.0%** | **2.01** | **+$286** | 7 | -$30 |

(Net figures below -$500 mean the fixed-lot simulation kept trading past
the point a real $500 account would already have been wiped out — a real
account dies at the first -100%.)

The one "profitable" row (+$31 over five years, PF 1.10) is not an
independent scalping method — it is the main strategy's own entries with
the winners cut short at 1 ATR, which destroys 89% of the profit the same
entries produce with the full exit logic (+$286). Every genuinely
scalping-frequency method (2,000-8,000 trades) lost badly despite several
having win rates above 55%.

The structural reason is arithmetic, not tuning: round-trip cost here is
0.35 in price terms (spread 0.25 + slippage 2×0.05) against a mean M5
ATR of ~1.5. A 0.5-ATR take-profit is ~0.76 of price movement, so costs
consume ~46% of every winner, while losers pay the same toll. High win
rates cannot overcome an average loss roughly twice the average net win —
which is exactly the shape every row above shows (avg loss ≈ $5.15 vs.
net win ≈ $2-3 after costs at TP 0.5). Scalping ads showing high win
rates are exploiting precisely this blind spot: win rate is the number
they show, cost-adjusted expectancy is the number that empties the
account. On M1 the same arithmetic gets ~2x worse (ATR shrinks ~√5 while
spread stays fixed), so no M1 test is needed to know it's worse - the
cost share per trade rises above 100% of the median winner.

### Confluence filters - stacking independent signals for higher-quality entries

Rather than swap the entry pattern (which the sweeps above show doesn't
transfer the edge), a set of *additional* confirmation filters were tested
on top of the existing entry, requiring several independent signals to
agree before firing - the idea being fewer, higher-conviction trades
rather than more trades:

| Extra filter added to main strategy | Trades | Win % | PF | Return/5yr | Max DD |
|---|---|---|---|---|---|
| None (baseline) | 163 | 51.5% | 1.69 | +55.8% | -12.7% |
| 3rd timeframe (H1) trend alignment | 137 | 54.0% | 1.91 | +55.6% | -9.97% |
| ADX ≥ threshold (trending regime required) | 129 | 50.4% | 1.67 | +41.8% | -8.42% |
| ADX rising | 35 | 62.9% | 2.80 | +25.3% | -3.89% |
| Confirmation candle (signal bar closes in trend direction) | 163 | 51.5% | 1.69 | +55.8% | -12.7% |
| ATR expanding (volatility above its own 20-bar average) | 154 | 53.3% | 1.82 | +60.2% | -10.8% |
| RSI(21) agrees with direction | 154 | 52.0% | 1.73 | +55.3% | -11.4% |
| Not overextended from EMA-fast (≤1 ATR) | 130 | 50.8% | 1.52 | +35.2% | -14.7% |
| HTF(M15) RSI not against direction | 162 | 51.2% | 1.65 | +52.7% | -12.7% |
| **H1 alignment + ATR expanding (adopted)** | **131** | **55.0%** | **2.01** | **+57.2%** | **-9.97%** |

Two individually-promising results were set aside rather than adopted:
"ADX rising" alone hits PF 2.80 but only fires 35 times in 5 years - too
small a sample to trust without a lot more data (a handful of lucky
trades can produce a PF that high by chance). "Confirmation candle" did
literally nothing (identical numbers to baseline) because the RSI
cross-up/cross-down condition already implies the signal bar closed in
the trend direction most of the time - it's a redundant filter, not a
real confirmation.

**H1 alignment + ATR expanding** was the strongest genuine combination and
was validated the same way every other filter in this project has been -
independently on the train (2020-08 to 2023-08) and test (2023-08 to
2025-08) regimes, not just the full 5-year blend:

| Regime | Baseline PF | +Filter PF | Baseline Max DD | +Filter Max DD |
|---|---|---|---|---|
| Train (2020-2023) | 1.29 | **1.51** | -12.7% | **-9.97%** |
| Test (2023-2025) | 2.55 | **2.99** | -5.25% | **-3.25%** |

Both profit factor and max drawdown improved in *both* regimes
independently, not just on average - the bar this project requires before
trusting a filter (see the trend-strength and hour-exclusion filters
earlier, and contrast with the rejected calendar-seasonality filter).
This is now `require_htf2_confirmation: true` and `require_atr_expansion:
true` in `config/config.yaml` (both default to `false` in
`StrategyConfig` so existing configs are unaffected unless explicitly
opted in). The cost is fewer trades (131 vs. 163 over 5 years, roughly
20% fewer) in exchange for a meaningfully better win rate, profit factor,
and drawdown - a reasonable trade for a small ($500) account where
drawdown recovery matters more than trade frequency.

## Risk management

`gold_bot/risk_manager.py` centralizes every risk rule so backtest and live
trading use identical logic:

- `max_trades_per_day` and `max_concurrent_trades` caps.
- `max_daily_loss_pct` circuit breaker — once tripped, no new trades open
  until the next calendar day (UTC).
- Optional ATR-based trailing stop that only ever moves in the trade's
  favor.
- Position sizing, controlled by `risk.sizing_mode`:
  - **`fixed_capital_percent_risk`** (current default) — same ATR-normalized
    lot math as `percent_risk` below, but the reference capital is always
    the fixed `base_equity` (e.g. $500), never the live/floating equity.
    Profit and loss are still tracked (`self.equity`, shown in reports,
    used for the daily-loss circuit breaker via `base_equity` too), they
    just never feed back into the lot-size calculation. See "Fixed-capital
    sizing" below for why this replaced `equity_step` as the default.
  - **`percent_risk`** (the textbook compounding approach) — lot size
    computed so a stop-out risks exactly `risk_per_trade_pct` of *current*
    equity. Risk is normalized automatically against the stop distance
    (wider ATR stop → smaller lot), but because it compounds, a winning
    streak inflates the lot size right before a losing streak hits it —
    same failure mode as `equity_step` below, just risk-normalized instead
    of milestone-based.
  - **`equity_step`** (legacy default, kept for backwards compatibility) —
    start at `base_lot` while equity is at `base_equity` ($500); add
    `lot_step` for every `equity_step_usd` ($100) of profit above that,
    remove `lot_step` for every $100 of loss, never going below `min_lot`.
    **This does not normalize risk against the stop distance** — the lot
    is fixed by the equity milestone alone, so the dollar risk of a given
    trade moves with ATR/volatility at entry time instead of staying
    constant. It also **compounds**: lot size grows off accumulated profit,
    which is what caused a real MT5 Strategy Tester account to blow up —
    see "Fixed-capital sizing" below. Not recommended for small accounts.
  - **`fixed_lot`** — a literal constant lot (`base_lot`), ignoring both
    equity and stop distance entirely. Simplest option, but dollar risk per
    trade is uncontrolled when ATR widens (a volatile entry risks more $
    than a calm one, with no normalization at all).

### Fixed-capital sizing (why the default changed from `equity_step`)

A real MT5 Strategy Tester run on `GoldBot_LotStep_0.06.mq5` (compounding
`equity_step` sizing, $500 starting balance) hit 100.26% max drawdown and
stopped trading after only 30 trades, roughly a year into an 8-year test
window. The cause wasn't a code bug: early wins pushed `equity_step`'s lot
size up (it compounds by design), and when a losing streak followed
(-$365.52 across 7 trades) it hit at the now-larger lot size, wiping the
account. Any *aggregate* backtest drawdown number (e.g. -8.6% in the table
below) is an average across the whole test — it says nothing about whether
one specific bad stretch, hitting after a specific run of wins, can drive
equity to zero. A real account can't survive that; a backtest average
doesn't warn you about it.

The fix: **stop computing lot size from floating/accumulated equity.**
`fixed_capital_percent_risk` and `fixed_lot` both anchor position size to a
capital number that never moves (`base_equity`), no matter how much the
account has actually won or lost. Profit/loss is still tracked and
reported — it just can't inflate the next trade's risk.

**Note:** the table below predates the confluence filters, hour-7
exclusion, and 5:3 R:R ratio adopted later in this README (it used the
163-trade signal set, not the current 115-trade one) - kept for the
sizing-mode comparison it makes (fixed-capital vs. equity_step vs.
fixed_lot), which still holds. See "Increasing profit further" below for
an up-to-date risk/trade table on the actual current default.

Full 5-year real-data comparison, same signals/filters, `fixed_capital_percent_risk`:

| Base capital | risk/trade | Trades | Win % | PF | Return/5yr | Max DD | Final equity |
|---|---|---|---|---|---|---|---|
| $500 | 1% | 163 | 51.5% | 1.66 | +27.4% | -6.6% | $637 |
| $500 | 2% (**default**) | 163 | 51.5% | 1.69 | +55.8% | -12.7% | $779 |
| $500 | 3% | 163 | 51.5% | 1.68 | +81.9% | -19.8% | $909 |
| $500 | 5% | 161 | 52.2% | 1.72 | +142.1% | -28.2% | $1,210 |
| $500 | 8% | 159 | 52.8% | 1.77 | +238.2% | -39.9% | $1,691 |
| $500 | 10% | 159 | 52.8% | 1.77 | +298.6% | -49.5% | $1,993 |
| $1000 | 1% | 163 | 51.5% | 1.69 | +27.9% | -6.4% | $1,279 |
| $1000 | 2% | 163 | 51.5% | 1.71 | +58.1% | -12.6% | $1,581 |
| $1000 | 3% | 162 | 51.9% | 1.71 | +85.9% | -19.3% | $1,859 |
| $1000 | 5% | 161 | 52.2% | 1.74 | +145.0% | -28.1% | $2,450 |
| $1000 | 8% | 159 | 52.8% | 1.77 | +239.3% | -39.7% | $3,393 |
| $1000 | 10% | 159 | 52.8% | 1.78 | +301.6% | -49.2% | $4,016 |

`$500`- and `$1000`-base results are near-identical in % terms at the same
`risk_per_trade_pct`, as expected — this mode is scale-invariant since it
never looks at live equity.

For reference, `fixed_lot` (no ATR/equity normalization at all) on $500,
and the old `equity_step 0.02/$100` (compounding) at the same $500 base:

| Sizing | Trades | Win % | PF | Return/5yr | Max DD | Final equity |
|---|---|---|---|---|---|---|
| equity_step 0.02/$100 (legacy default, compounds) | 163 | 51.5% | **2.35** | **+127.8%** | -8.6% | $1,139 |
| fixed_lot 0.02 | 163 | 51.5% | 1.97 | +56.5% | -8.6% | $782 |
| fixed_capital_percent_risk 2% (new default) | 163 | 51.5% | 1.69 | +55.8% | -12.7% | $779 |

**The honest tradeoff**: `equity_step`'s headline PF (2.35) and return
(+127.8%) look better than the fixed-capital numbers because compounding
lets winners size up — but that's exactly the mechanism that turned one bad
losing streak into a total account wipeout on a real MT5 run. The
fixed-capital numbers (PF ~1.7, +55.8%/5yr at 2%/trade) are lower but
represent risk that stays bounded to a percentage of a number that never
changes — a losing streak costs the same dollars whether it happens on
day 1 or year 4. If you want higher return and are willing to accept the
real risk of a compounding blowup, `percent_risk` or `equity_step` are
still available; they are not recommended for accounts under ~$2,000 where
a single bad streak is catastrophic rather than a paper loss.

**The actual current `config/config.yaml` default** combines this sizing
with the confluence filters from "Confluence filters" above
(`require_htf2_confirmation` + `require_atr_expansion`, both `true`),
which was not yet the case in the sizing comparison table above (that
table used only the entry logic, not the confluence filters). With both
turned on:

| Metric | Value |
|---|---|
| Trades | 115 |
| Win rate | 57.39% |
| Profit factor | **2.28** |
| Total return (fixed_capital_percent_risk 2%, $500) | +69.57% / 5 years ($500 → $848) |
| Max drawdown | -9.23% |

(These figures include the hour-7 exclusion from the loss post-mortem and
the 5:3 R:R ratio from "R:R ratio and pyramiding" below.)

### $500 account backtest (legacy `equity_step` numbers - superseded above)

These numbers are kept for history; the current default is
`fixed_capital_percent_risk` (see "Fixed-capital sizing" above) since
`equity_step` is the compounding mode responsible for a real MT5 account
blowup. With `backtest.initial_balance: 500`, the `equity_step` sizing
below, and both the trend-strength and hour-of-day filters described
earlier, the full 5-year real-data backtest gives:

| Metric | Value |
|---|---|
| Trades | 163 |
| Win rate | 51.53% |
| Profit factor | 2.35 |
| Total return | +127.78% / 5 years ($500 → $1,138.90) |
| Max drawdown | -8.6% |

(The `equity_step` vs. `percent_risk` sizing comparison table below predates
both filters - it was run on the original 264-trade signal set to choose
the sizing parameters. The filters change which trades fire, not how lots
are sized, so the sizing conclusion - `equity_step 0.02/$100` wins on
risk-adjusted terms - still holds, but the absolute numbers below are from
before the filters and are superseded by the table above.)

This sizing choice came from grid-searching the `equity_step` parameters
themselves (`base_lot`/`lot_step` of 0.01 vs 0.02, `equity_step_usd` of 50
vs 100) alongside `percent_risk` at several risk levels, all on the same
$500 starting balance:

| Sizing | Return / 5yr | Max DD | Final equity |
|---|---|---|---|
| equity_step 0.01/$100 | +20.35% | -7.4% | $602 |
| equity_step 0.01/$50 | +30.81% | -7.74% | $654 |
| **equity_step 0.02/$100 (current default)** | **+61.62%** | **-14.13%** | **$808** |
| equity_step 0.02/$50 | +57.96% | -19.76% | $790 |
| percent_risk 2%/trade | +48.05% | -22.05% | $740 |
| percent_risk 3%/trade | +70.52% | -30.91% | $853 |
| percent_risk 5%/trade | +126.77% | -45.39% | $1,134 |

`equity_step 0.02/$100` had the best profit factor (1.45 at the time) and a
better return-to-drawdown ratio than any `percent_risk` level tested, which
is why it's the default - but as always, past backtest performance doesn't
guarantee this ordering holds on future data.

### Updated risk/return ceiling after the trend-strength filter

The "why not just raise risk_per_trade_pct" table further down was run
before the trend-strength filter existed. With the filter, the achievable
monthly-compounded-return ceiling roughly doubled - re-running the same
`percent_risk` sweep on a $1,000 account:

| risk/trade | Annualized monthly rate | Max drawdown |
|---|---|---|
| 1% | 0.39%/mo | -6.4% |
| 3% | 1.12%/mo | -18.3% |
| 5% | 1.89%/mo | -25.9% |
| 8% | 2.99%/mo | -35.5% |
| 10% | 3.58%/mo | -42.4% |
| 12% | 4.10%/mo | -48.7% |
| 15% | 4.76%/mo | -57.5% |
| 20% | 5.57%/mo | -68.6% |
| 25% | 5.99%/mo | -77.7% |
| 30% (near the ceiling) | 6.01%/mo | -85.7% |

The peak achievable monthly rate is now ~6%/month (vs. ~3%/month before
this filter) but plateaus there - 25% and 30% risk give almost the same
monthly rate while drawdown gets dramatically worse, the same
diminishing-then-negative pattern as before, just shifted up. **10%/month
is still not achievable at any risk level even with this improved
strategy** - responsible risk levels (3-8%/trade) give 1-3%/month with
18-36% drawdown, which is still a large improvement over the pre-filter
numbers at the same risk level.

Two caveats specific to a small account: (1) XAUUSD's contract size means
even `min_lot=0.02` is 2 oz — at ~$3,300/oz that's ~$6,600 of notional
exposure against $500 of capital, so check your broker's margin
requirement and leverage before going live, this is not "safe" just
because the position sizing is small in lot terms. (2) with such a small
account, `max_daily_loss_pct` and `max_trades_per_day` matter more than
usual — a string of losses is a bigger percentage swing on $500 than on
$10,000, so don't disable those circuit breakers.

## Increasing profit further - what was tried and what actually works

Every profit lever a trader would reasonably try has now been tested on
real data in this project. Summary, from most to least effective:

**1. Risk per trade — the only lever that reliably scales profit, on the
current default config (115 trades, 5:3 R:R, hours 7+9 excluded):**

| risk/trade | Win % | PF | Return/5yr | Max DD | Final ($500) | Avg $/month |
|---|---|---|---|---|---|---|
| 1% | 57.4% | 2.14 | +32.1% | -5.4% | $661 | $2.68 |
| **2% (current default)** | **57.4%** | **2.28** | **+69.6%** | **-9.2%** | **$848** | **$5.80** |
| 3% | 57.4% | 2.28 | +103.5% | -14.5% | $1,017 | $8.62 |
| 5% | 57.4% | 2.29 | +173.9% | -24.0% | $1,369 | $14.49 |
| 8% | 57.9% | 2.25 | +271.7% | -37.9% | $1,858 | $22.64 |

PF is essentially flat from 1-8% (2.14 to 2.29) because `fixed_capital_
percent_risk` is scale-invariant - raising risk% doesn't change which
trades win or lose, only their size. This means **the risk% dial is the
one lever that's honestly "free" upside**, at the direct, disclosed cost
of proportionally larger drawdown. Above 5% the DD (-24% and climbing)
starts to matter psychologically even though the math hasn't broken -
that's a personal risk-tolerance choice, not a strategy quality one.

**2. Everything else tested, and why it didn't help:**

| Approach | Result | Why |
|---|---|---|
| More entry triggers (MACD flip, Stochastic, breakout, etc.) | PF collapses to 0.86-1.08 | Alternative triggers have no real edge; diluting 115 good trades with hundreds of mediocre ones |
| Trading other FX pairs | All fail (best AUDUSD fails test split) | The pullback-resume edge doesn't exist on mean-reverting FX majors |
| Scalping (small targets, high frequency) | 9 of 10 variants lose | Spread+slippage cost eats ~46% of a small ATR target |
| Pyramiding (add legs to winners) | PF roughly halves in every period | Added legs are lower quality than the original entry |
| Wider TP without matching SL widening | Worse | Only the *ratio* held at 5:3 mattered; the current 1.8/3.0 magnitude is a genuine local optimum, not just "bigger TP" |
| Partial profit-taking (scale out early) | Never beats no-scale-out | The existing 1.0-ATR trailing stop already captures full winners better |
| Trailing-stop retuning | 1.0 ATR already optimal | Confirmed by direct sweep, both tighter and looser are worse |
| Compounding (`equity_step`/`percent_risk` sizing) | Higher backtest PF, but... | ...this is the exact mechanism that wiped a real MT5 account - rejected on realized evidence, not backtest looks |

**Bottom line**: the strategy's edge is what it is - about 23 trades/year
at 57% win rate and 2.28 profit factor. That edge is now fully harvested;
every attempt to extract more from it (more trades, bigger targets,
tighter/looser exits, other markets, added positions) either does nothing
or actively makes it worse. The only remaining honest dial is risk per
trade, which is a straight trade of more $ upside for more $ drawdown at
a fixed win rate - not a "free" improvement, but not a guess either since
the whole curve above is measured, not estimated.

## Project layout

```
config/config.yaml       Strategy, risk, session, and MT5 connection settings
gold_bot/
  indicators.py          EMA, RSI, ATR, MACD (vectorized, pandas)
  strategy.py             Signal generation (trend + pullback + session filter)
  risk_manager.py         Position sizing / daily limits / trailing stop
  backtester.py            Bar-by-bar backtest engine + performance summary
  mt5_connector.py         Thin wrapper over the MetaTrader5 package (Windows only)
  live_trader.py            Polling loop tying strategy + risk + MT5 together
scripts/
  run_backtest.py         CLI: backtest over a historical OHLC CSV
  run_live.py               CLI: run the live/paper trading loop
tests/                    pytest unit tests (indicators, strategy, risk manager)
data/XAUUSD_M5_demo_sample.csv   Synthetic OHLC data for smoke-testing only
```

## Setup

```bash
pip install -r requirements.txt
```

`MetaTrader5` only installs/works on Windows (it talks to a local MT5
terminal process). On Linux/macOS you can still develop the strategy and run
backtests — just skip live trading.

## Backtesting

Provide a CSV with columns `time,open,high,low,close` (time parseable as
UTC), for example real XAUUSD M5 history exported from your broker/MT5:

```bash
python scripts/run_backtest.py --data data/XAUUSD_M5_demo_sample.csv --config config/config.yaml
python scripts/run_backtest.py --data data/your_real_data.csv --plot equity.png
```

Output includes trade count, win rate, profit factor, total return, and max
drawdown.

### Real backtest results (honest numbers, read before trusting this bot)

`data/XAUUSD_M5_real.csv` is 5 years of real XAUUSD M5 data (2020-08-21 to
2025-08-01, 350,903 bars), pulled from the public dataset in
[ilahuerta-IA/backtrader-pullback-window-xauusd](https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd).

Parameters were tuned with `scripts/optimize.py` using a train/test split
(train: 2020-08 to 2023-08, test: 2023-08 to 2025-08) to reduce overfitting.
**Important caveat:** every single parameter combination tested lost money
on the train split. The reason: the train period was essentially flat for
gold (+0.4% net over 3 years, choppy), while the test period was a strong
uptrend (+71.2%). A trend-pullback strategy structurally needs a trend to
profit from — no amount of parameter tuning fixes that, it can only reduce
how much a choppy period costs you.

A second round of tuning (`scripts/optimize.py --rank-by win_rate_pct`, adding
`atr_tp_mult`, `rsi_oversold/overbought`, and `trailing_atr_mult` to the grid)
found that a **tighter trailing stop matters more than the take-profit
distance** — most winners exit via the trailing stop long before reaching a
far take-profit, so shrinking `atr_tp_mult` from 3.0 to 2.5 changed nothing,
while tightening `trailing_atr_mult` from 1.2 to 1.0 locked in profit sooner
on essentially the same set of trades. Current strategy defaults
(`ema_slow=100`, `rsi_pullback_level=50`, `atr_sl_mult=2.0`, `atr_tp_mult=2.5`,
`trailing_atr_mult=1.0`) generate the same 264 trades / 44.7% win rate no
matter which position-sizing mode is used (sizing only changes lot size,
not entry/exit signals). With `percent_risk` sizing at 1%/trade and a
$10,000 starting balance, that's:

| Metric | Value |
|---|---|
| Trades | 264 (~53/year) |
| Win rate | 44.7% |
| Profit factor | 1.28 |
| Total return | +22.12% / 5 years |
| Max drawdown | -11.78% |

(See "$500 account backtest" above for the same signals under the
`equity_step` sizing that's the current config default — same trades,
different lot sizing, so a different profit factor/drawdown since the
$-per-lot ratio isn't identical between the two modes.)

That is a real edge, but a modest one (~4%/year before broker commissions),
concentrated almost entirely in the 2024-2025 trending period — 2020-2023
was flat-to-losing. **Do not expect this to profit in a sideways/choppy
gold market.** Win rate stays under 45% because the strategy is designed to
cut losers quickly and let a minority of trend trades run — chasing a
higher win rate directly (e.g. tighter take-profit) was tried in the second
optimization round and did not improve profit factor or return, since it
just converts winning trend trades into smaller wins without reducing the
loss count. Re-run `scripts/optimize.py` periodically as new data comes in,
and never skip the demo-account forward-test step before going live.

### Why not just raise `risk_per_trade_pct` for bigger returns?

Position sizing is risk-based (`gold_bot/risk_manager.py`), so return and
drawdown both scale with `risk_per_trade_pct` roughly together. On the same
5-year dataset:

| risk/trade | Annualized return | Max drawdown |
|---|---|---|
| 1% (default) | ~4%/yr | -11.8% |
| 2% | ~7.9%/yr | -22.2% |
| 3% | ~12.0%/yr | -30.6% |
| 5% | ~18.0%/yr | -45.7% |
| 8% | ~29.2%/yr | -61.9% |
| 12% | ~40.7%/yr | **-77.4%** |

There is no setting that gives 50%/yr without a historical drawdown well
past 80% — i.e. a near-certain account blowup the first time markets don't
cooperate. Increasing `risk_per_trade_pct` is a real lever if you
deliberately want more volatility for more expected return, but pick a
number using this table, not a target return in isolation.

### Is 10%/month achievable? (asked directly - tested directly)

The table above predates the current strategy (5:3 R:R, hour 7/9
exclusion, H1+ATR confluence) and only shows the 5-year *average*
annualized rate, which hides how lumpy monthly results actually are with
only ~23 trades/year (~2/month). Re-run on $1000 with the current
strategy, looking at the real month-by-month distribution (61 calendar
months) instead of just the average:

| risk/trade | Avg $/mo | Median $/mo | Worst month | Best month | Months hitting ≥10% | Max DD (5yr) |
|---|---|---|---|---|---|---|
| 2% (current default) | +1.14% | +0.73% | -7.1% | +8.3% | **0%** | -10.1% |
| 5% | +2.80% | +1.82% | -18.0% | +21.2% | 6.6% | -24.1% |
| 8% | +4.45% | +3.08% | -29.5% | +34.3% | 31.1% | -37.4% |
| 10% | +5.61% | +3.78% | -36.2% | +42.7% | 34.4% | -45.8% |
| 15% | +8.44% | +5.61% | -53.7% | +63.9% | 45.9% | -65.3% |
| 20% | +11.25% | +7.57% | -71.9% | +86.3% | 49.2% | -82.9% |
| 30% | +16.82% | +11.35% | -108.2%* | +128.3% | 52.5% | -113.9%* |

(*Max drawdown/worst-month figures below -100% mean a real account would
already be at zero or negative and stopped out by the broker well before
reaching that number - the simulation keeps computing past the point a
live account would have died.)

**Answer: no, not sustainably, at any risk level.** At the current sane
default (2%), average is +1.14%/month and the single best month in 5
years only hit +8.3% - 10% was never reached even once. Pushing risk to
20%/trade gets the *average* month above 10% (+11.25%), but look at the
other columns: the *median* month is only +7.57% (most months still miss
the target), 51% of months are below +10%, and the worst month is -71.9%
with -82.9% max drawdown - i.e. the strategy would have been wiped out
(or margin-called by the broker) at least once in that same 5-year
window. The "average" is dragged up by a handful of huge lucky months
while typical months and bad stretches would have already ended the
account. There is no risk% where "consistent 10%/month" and "survives the
next 5 years" are both true on this strategy's real trade frequency and
win rate - the math (23 trades/year × 57% win rate × PF 2.28) simply
doesn't generate that return without also generating account-ending
variance.

## Live / paper trading (Windows + MT5 required)

1. Install and log into the MT5 terminal for your broker.
2. Set credentials via environment variables (preferred over editing
   `config.yaml` directly):

   ```bash
   set MT5_LOGIN=12345678
   set MT5_PASSWORD=your-password
   set MT5_SERVER=YourBroker-Demo
   ```

3. Run:

   ```bash
   python scripts/run_live.py --config config/config.yaml
   ```

Start on a **demo account** first. The loop polls every `--poll-seconds`
(default 15s), evaluates the strategy once per newly closed M5 bar, trails
stops on open positions, and opens new trades only when the risk manager
allows it.

## Tests

```bash
pytest tests/ -v
```

## Tuning

All strategy/risk/session parameters live in `config/config.yaml` — no code
changes needed to experiment with EMA periods, RSI levels, ATR multipliers,
risk per trade, or session windows. Re-run the backtest after every change
on real historical data before touching a live/demo account.
