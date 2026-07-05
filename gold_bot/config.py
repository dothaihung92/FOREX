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


@dataclass
class RiskConfig:
    risk_per_trade_pct: float
    max_trades_per_day: int
    max_daily_loss_pct: float
    max_concurrent_trades: int
    use_trailing_stop: bool
    trailing_atr_mult: float
    sizing_mode: str = "percent_risk"  # "percent_risk" or "equity_step"
    base_equity: float = 500.0
    base_lot: float = 0.01
    lot_step: float = 0.01
    equity_step_usd: float = 100.0
    min_lot: float = 0.01


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
