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

Exits: ATR-based stop loss and take profit, with an optional ATR chandelier
trailing stop. All of these multipliers, EMA/RSI periods, and session times
are configurable in `config/config.yaml`.

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

## Risk management

`gold_bot/risk_manager.py` centralizes every risk rule so backtest and live
trading use identical logic:

- `max_trades_per_day` and `max_concurrent_trades` caps.
- `max_daily_loss_pct` circuit breaker — once tripped, no new trades open
  until the next calendar day (UTC).
- Optional ATR-based trailing stop that only ever moves in the trade's
  favor.
- Position sizing, controlled by `risk.sizing_mode`:
  - **`percent_risk`** (the textbook approach) — lot size computed so a
    stop-out risks exactly `risk_per_trade_pct` of current equity. Risk is
    normalized automatically: a wider ATR stop gets a smaller lot, a
    tighter stop a bigger one, so dollar risk per trade stays constant.
  - **`equity_step`** (current default, matches a $500-account request) —
    start at `base_lot` while equity is at `base_equity` ($500); add
    `lot_step` for every `equity_step_usd` ($100) of profit above that,
    remove `lot_step` for every $100 of loss, never going below `min_lot`.
    **This does not normalize risk against the stop distance** — the lot
    is fixed by the equity milestone alone, so the dollar risk of a given
    trade moves with ATR/volatility at entry time instead of staying
    constant. `base_lot`/`lot_step`/`min_lot` were grid-searched on the
    real 5-year dataset (see table below) and set to 0.02 - if gold's
    price or volatility regime changes a lot from what's in
    `data/XAUUSD_M5_real.csv`, re-run the backtest before trusting the
    same lot-per-$100 step still risks a sane percentage of your account.

### $500 account backtest (current config defaults)

With `backtest.initial_balance: 500` and the `equity_step` sizing above,
the full 5-year real-data backtest gives:

| Metric | Value |
|---|---|
| Trades | 264 |
| Win rate | 44.7% |
| Profit factor | 1.45 |
| Total return | +61.62% / 5 years ($500 → $808.12) |
| Max drawdown | -14.13% |

This came from grid-searching the `equity_step` parameters themselves
(`base_lot`/`lot_step` of 0.01 vs 0.02, `equity_step_usd` of 50 vs 100)
alongside `percent_risk` at several risk levels, all on the same $500
starting balance and the same 264 trades:

| Sizing | Return / 5yr | Max DD | Final equity |
|---|---|---|---|
| equity_step 0.01/$100 | +20.35% | -7.4% | $602 |
| equity_step 0.01/$50 | +30.81% | -7.74% | $654 |
| **equity_step 0.02/$100 (current default)** | **+61.62%** | **-14.13%** | **$808** |
| equity_step 0.02/$50 | +57.96% | -19.76% | $790 |
| percent_risk 2%/trade | +48.05% | -22.05% | $740 |
| percent_risk 3%/trade | +70.52% | -30.91% | $853 |
| percent_risk 5%/trade | +126.77% | -45.39% | $1,134 |

`equity_step 0.02/$100` had the best profit factor (1.45) and a better
return-to-drawdown ratio than any `percent_risk` level tested, which is
why it's the default - but as always, past backtest performance doesn't
guarantee this ordering holds on future data.

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
