"""Localhost web dashboard: multi-pair charts plus manual order entry.

Built on the standard library's HTTP server rather than a framework - this
serves one user on one machine, and the project keeps its dependency list
short on purpose.

Security posture, since this thing can move real money:

* Binds 127.0.0.1 only. Never pass a public bind address; the API has no
  authentication because it assumes it is unreachable from the network.
* Trading is off unless the backend was constructed with it enabled AND
  the request carries the confirmation token.
* Every order passes gold_bot.dashboard.orders.validate first.
* Credentials are read from config/env by the caller and never appear in
  URLs, responses or logs.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from gold_bot.dashboard.feeds import BaseFeed, FeedError, TIMEFRAME_MINUTES
from gold_bot.dashboard.orders import (
    MAX_RISK_PCT_PER_ORDER,
    OrderRejected,
    OrderRequest,
    risk_usd,
    suggest_lots,
    validate,
)
from gold_bot.indicators import atr, bollinger_bands, ema, rsi

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("gold_bot.dashboard")

# Sent by the browser on every state-changing request. This is not a
# security boundary against a network attacker - binding to localhost is -
# it stops a stray page in another tab from POSTing an order at your
# account through the browser.
CONFIRM_HEADER = "X-Trade-Confirm"
CONFIRM_VALUE = "i-understand-this-places-a-real-order"


def _indicator_payload(df: pd.DataFrame, cfg) -> dict:
    """Chart overlays: the same indicators the bot itself trades on, so the
    picture on screen matches the logic making decisions."""
    close = df["close"]
    out: dict[str, list] = {}
    if len(df) < 5:
        return out
    ef = ema(close, cfg.ema_fast)
    es = ema(close, cfg.ema_slow)
    r = rsi(close, cfg.rsi_period)
    a = atr(df, cfg.atr_period)
    bu, bm, bl = bollinger_bands(close, cfg.bb_period, cfg.bb_std_mult)
    times = [int(t.timestamp()) for t in df.index]

    def series(vals):
        return [{"time": t, "value": float(v)}
                for t, v in zip(times, vals) if v == v and np.isfinite(v)]

    out["ema_fast"] = series(ef)
    out["ema_slow"] = series(es)
    out["bb_upper"] = series(bu)
    out["bb_lower"] = series(bl)
    out["rsi"] = series(r)
    out["atr"] = series(a)
    return out


class DashboardState:
    """Everything the handler needs, so the handler stays a thin router."""

    def __init__(self, feed: BaseFeed, cfg, allow_trading: bool):
        self.feed = feed
        self.cfg = cfg
        self.allow_trading = bool(allow_trading) and feed.can_trade
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    state: DashboardState = None       # injected by make_server
    server_version = "GoldBotDashboard/1.0"

    # ---------------------------------------------------------- plumbing
    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _error(self, code: int, message: str):
        self._json(code, {"error": message})

    # -------------------------------------------------------------- GET
    def do_GET(self):
        url = urlparse(self.path)
        route, params = url.path, parse_qs(url.query)
        try:
            if route in ("/", "/index.html"):
                return self._static("index.html", "text/html; charset=utf-8")
            if route == "/app.js":
                return self._static("app.js", "application/javascript; charset=utf-8")
            if route == "/style.css":
                return self._static("style.css", "text/css; charset=utf-8")
            if route == "/vendor/lightweight-charts.js":
                return self._static("lightweight-charts.standalone.production.js",
                                    "application/javascript; charset=utf-8")
            if route == "/api/config":
                return self._api_config()
            if route == "/api/candles":
                return self._api_candles(params)
            if route == "/api/account":
                return self._api_account()
            if route == "/api/positions":
                return self._api_positions()
            return self._error(404, "not found")
        except FeedError as exc:
            return self._error(502, str(exc))
        except Exception as exc:                       # noqa: BLE001
            log.exception("GET %s failed", route)
            return self._error(500, f"{type(exc).__name__}: {exc}")

    def _static(self, name: str, ctype: str):
        path = STATIC_DIR / name
        if not path.exists():
            return self._error(404, f"missing asset {name}")
        return self._send(200, path.read_bytes(), ctype)

    def _api_config(self):
        s = self.state
        self._json(200, {
            "symbols": s.feed.symbols(),
            "timeframes": list(TIMEFRAME_MINUTES),
            "trading_enabled": s.allow_trading,
            "max_risk_pct": MAX_RISK_PCT_PER_ORDER,
            "default_risk_pct": s.cfg.risk.risk_per_trade_pct,
            "atr_sl_mult": s.cfg.strategy.atr_sl_mult,
            "atr_tp_mult": s.cfg.strategy.atr_tp_mult,
        })

    def _api_candles(self, params):
        symbol = (params.get("symbol") or ["XAUUSD"])[0]
        timeframe = (params.get("timeframe") or ["M5"])[0]
        try:
            n = min(5000, max(50, int((params.get("n") or ["600"])[0])))
        except ValueError:
            return self._error(400, "n must be an integer")
        df = self.state.feed.candles(symbol, timeframe, n)
        if df.empty:
            return self._error(404, f"no candles for {symbol} {timeframe}")
        candles = [
            {"time": int(t.timestamp()), "open": float(o), "high": float(h),
             "low": float(l), "close": float(c)}
            for t, o, h, l, c in zip(df.index, df["open"], df["high"],
                                     df["low"], df["close"])
        ]
        self._json(200, {
            "symbol": symbol, "timeframe": timeframe,
            "candles": candles,
            "indicators": _indicator_payload(df, self.state.cfg.strategy),
            "last_price": float(df["close"].iloc[-1]),
        })

    def _api_account(self):
        self._json(200, self.state.feed.account().as_dict())

    def _api_positions(self):
        self._json(200, {"positions": [p.as_dict() for p in self.state.feed.positions()]})

    # ------------------------------------------------------------- POST
    def do_POST(self):
        route = urlparse(self.path).path
        try:
            payload = self._read_json()
        except ValueError as exc:
            return self._error(400, str(exc))
        try:
            if route == "/api/suggest-lots":
                return self._api_suggest(payload)
            if route == "/api/order":
                return self._api_order(payload)
            if route == "/api/close":
                return self._api_close(payload)
            return self._error(404, "not found")
        except OrderRejected as exc:
            return self._error(400, str(exc))
        except FeedError as exc:
            return self._error(502, str(exc))
        except Exception as exc:                       # noqa: BLE001
            log.exception("POST %s failed", route)
            return self._error(500, f"{type(exc).__name__}: {exc}")

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length")
        if length <= 0:
            return {}
        if length > 64_000:
            raise ValueError("request body too large")
        try:
            return json.loads(self.rfile.read(length).decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    def _require_confirmation(self):
        if not self.state.allow_trading:
            raise OrderRejected(
                "trading is disabled on this dashboard session - restart with "
                "--live-trading to enable it"
            )
        if self.headers.get(CONFIRM_HEADER) != CONFIRM_VALUE:
            raise OrderRejected("missing trade confirmation header")

    def _api_suggest(self, payload):
        """Lot size for a given stop and risk %, so the user is not doing
        this arithmetic by hand at the moment of entry."""
        symbol = str(payload.get("symbol", ""))
        price = float(payload.get("price") or 0)
        sl = float(payload.get("sl") or 0)
        risk_pct = float(payload.get("risk_pct") or self.state.cfg.risk.risk_per_trade_pct)
        risk_pct = min(risk_pct, MAX_RISK_PCT_PER_ORDER)
        equity = self.state.feed.account().equity
        min_lot = self.state.cfg.risk.min_lot
        lots, actual_pct = suggest_lots(symbol, price, sl, equity, risk_pct,
                                        min_lot=min_lot,
                                        lot_step=self.state.cfg.risk.lot_step)
        req = OrderRequest(symbol=symbol, direction=1 if sl < price else -1,
                           lots=lots, sl=sl, tp=0.0, price=price)
        # When the broker's minimum lot exceeds what the requested risk
        # allows, say so loudly - silently doubling someone's risk is how
        # small accounts die.
        warning = None
        if lots <= min_lot and actual_pct > risk_pct * 1.05:
            warning = (
                f"Broker minimum lot ({min_lot}) forces {actual_pct:.1f}% risk, "
                f"not the {risk_pct:.1f}% you asked for. Widen the account or "
                f"use a tighter stop."
            )
        return self._json(200, {
            "lots": lots, "equity": round(equity, 2),
            "risk_usd": round(risk_usd(req), 2),
            "risk_pct": round(actual_pct, 2),
            "requested_pct": risk_pct,
            "warning": warning,
        })

    def _api_order(self, payload):
        self._require_confirmation()
        s = self.state
        symbol = str(payload.get("symbol", ""))
        side = str(payload.get("side", "")).upper()
        if side not in ("BUY", "SELL"):
            raise OrderRejected("side must be BUY or SELL")
        direction = 1 if side == "BUY" else -1
        try:
            lots = float(payload.get("lots"))
            sl = float(payload.get("sl") or 0)
            tp = float(payload.get("tp") or 0)
        except (TypeError, ValueError):
            raise OrderRejected("lots, sl and tp must be numbers")

        with s.lock:
            df = s.feed.candles(symbol, "M5" if symbol.startswith("XAU") else "M15", 2)
            price = float(df["close"].iloc[-1])
            account = s.feed.account()
            req = OrderRequest(symbol=symbol, direction=direction, lots=lots,
                               sl=sl, tp=tp, price=price)
            validate(req, account.equity, s.feed.symbols(),
                     min_lot=s.cfg.risk.min_lot, lot_step=s.cfg.risk.lot_step)
            open_count = len(s.feed.positions())
            if open_count >= s.cfg.risk.max_concurrent_trades:
                raise OrderRejected(
                    f"max_concurrent_trades ({s.cfg.risk.max_concurrent_trades}) "
                    f"already open"
                )
            result = s.feed.place_order(symbol, direction, lots, sl, tp)

        log.info("order placed: %s %s %.2f lots sl=%.5f tp=%.5f -> ticket %s",
                 side, symbol, lots, sl, tp, result.get("ticket"))
        result.update({"risk_usd": round(risk_usd(req), 2), "price_at_send": price})
        return self._json(200, result)

    def _api_close(self, payload):
        self._require_confirmation()
        try:
            ticket = int(payload.get("ticket"))
        except (TypeError, ValueError):
            raise OrderRejected("ticket must be an integer")
        with self.state.lock:
            result = self.state.feed.close_position(ticket)
        log.info("position closed: ticket %s", ticket)
        return self._json(200, result)


def make_server(feed: BaseFeed, cfg, host: str = "127.0.0.1", port: int = 8787,
                allow_trading: bool = False) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            f"refusing to bind {host!r}: this dashboard has no authentication "
            "and can place orders, so it must stay on localhost"
        )
    state = DashboardState(feed, cfg, allow_trading)
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer((host, port), handler)
