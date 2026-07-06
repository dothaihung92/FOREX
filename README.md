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

Current defaults (`min_trend_strength_pct: 0.0256` + `excluded_hours: [9]`)
give, full 5-year backtest:

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
