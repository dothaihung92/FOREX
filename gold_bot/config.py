"""Load and expose bot configuration from config/config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class SessionWindow:
    name: str
    start: str
    end: str


@dataclass
class StrategyConfig:
    ema_fast: int
    ema_slow: int
    rsi_period: int
    rsi_oversold: float
    rsi_overbought: float
    rsi_pullback_level: float
    atr_period: int
    atr_sl_mult: float
    atr_tp_mult: float
    htf_timeframe: str
    htf_ema_period: int
    adx_period: int
    adx_threshold: float
    bb_period: int
    bb_std_mult: float
    mr_atr_sl_mult: float
    mr_atr_tp_mult: float
    enable_mean_reversion: bool = False
    min_trend_strength_pct: float = 0.0  # skip trend entries when EMA fast/slow gap is below this % of price
    excluded_hours: list[int] = field(default_factory=list)  # skip entries starting in these UTC hours
    require_htf2_confirmation: bool = False  # 3rd timeframe (htf2) trend must also agree - see README "Confluence filters"
    htf2_timeframe: str = "H1"
    htf2_ema_period: int = 100
    require_atr_expansion: bool = False  # ATR must be above its own rolling mean (volatility expanding, not contracting)
    atr_expansion_period: int = 20
    # USD-strength confirmation from other FX pairs - EXPERIMENTAL, off by
    # default and NOT validated. See gold_bot/correlation.py and README
    # "Gold/FX correlation filter" before enabling.
    require_usd_confirmation: bool = False
    usd_pairs: list[str] = field(default_factory=lambda: ["EURUSD", "AUDUSD", "USDCHF"])
    usd_lookback_hours: float = 12.0


@dataclass
class RiskConfig:
    risk_per_trade_pct: float
    max_trades_per_day: int
    max_daily_loss_pct: float
    max_concurrent_trades: int
    use_trailing_stop: bool
    trailing_atr_mult: float
    sizing_mode: str = "percent_risk"  # "percent_risk" | "equity_step" | "fixed_capital_percent_risk" | "fixed_lot"
    base_equity: float = 500.0
    base_lot: float = 0.01
    lot_step: float = 0.01
    equity_step_usd: float = 100.0
    min_lot: float = 0.01

    # DCA/grid mode (opt-in, sizing_mode="dca_grid"): hold through sideways
    # moves, add another leg every dca_step_price adverse move, exit all
    # legs on trend reversal OR the dca_hard_stop_pct catastrophic stop -
    # whichever comes first. See README "DCA/grid mode" for why the hard
    # stop is not optional: without it, a single strong adverse move has
    # no loss cap at all (tested: worst historical open loss on real data
    # reached -65.6% of capital before the trend-reversal exit fired).
    dca_step_price: float = 2.0
    dca_leg_risk_pct: float = 2.0
    dca_max_legs: int = 30
    dca_hard_stop_pct: float = 15.0


@dataclass
class BacktestConfig:
    initial_balance: float
    spread_points: float
    commission_per_lot: float
    slippage_points: float


@dataclass
class BotConfig:
    symbol: str
    timeframe: str
    mt5: dict[str, Any]
    strategy: StrategyConfig
    sessions: list[SessionWindow]
    risk: RiskConfig
    backtest: BacktestConfig
    logging: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(path: str = "config/config.yaml") -> BotConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    mt5_cfg = dict(raw["mt5"])
    # Allow overriding credentials via environment variables so secrets
    # never need to be committed to config.yaml.
    mt5_cfg["login"] = int(os.environ.get("MT5_LOGIN", mt5_cfg.get("login", 0)) or 0)
    mt5_cfg["password"] = os.environ.get("MT5_PASSWORD", mt5_cfg.get("password", ""))
    mt5_cfg["server"] = os.environ.get("MT5_SERVER", mt5_cfg.get("server", ""))

    return BotConfig(
        symbol=raw["symbol"],
        timeframe=raw["timeframe"],
        mt5=mt5_cfg,
        strategy=StrategyConfig(**raw["strategy"]),
        sessions=[SessionWindow(**s) for s in raw["sessions"]],
        risk=RiskConfig(**raw["risk"]),
        backtest=BacktestConfig(**raw["backtest"]),
        logging=raw["logging"],
        raw=raw,
    )
