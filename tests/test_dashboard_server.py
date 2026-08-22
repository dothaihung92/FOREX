"""End-to-end tests against a real server instance on a loopback port.

These exercise the HTTP layer the browser actually talks to, including the
refusals - a validation rule that works in isolation but is skipped by the
route is worth nothing.
"""
import json
import threading
import urllib.error
import urllib.request

import pytest

from gold_bot.config import load_config
from gold_bot.dashboard.feeds import CsvFeed
from gold_bot.dashboard.server import CONFIRM_HEADER, CONFIRM_VALUE, make_server

CONFIG = "config/config.yaml"


@pytest.fixture(scope="module")
def server():
    cfg = load_config(CONFIG)
    feed = CsvFeed(start_balance=5000.0)
    httpd = make_server(feed, cfg, host="127.0.0.1", port=0, allow_trading=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


def post(base, path, payload, confirm=True):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    if confirm:
        req.add_header(CONFIRM_HEADER, CONFIRM_VALUE)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_config_endpoint_reports_capabilities(server):
    cfg = get(server, "/api/config")
    assert "XAUUSD" in cfg["symbols"]
    assert cfg["trading_enabled"] is True
    assert cfg["max_risk_pct"] > 0


def test_candles_include_indicators_and_are_time_ordered(server):
    d = get(server, "/api/candles?symbol=XAUUSD&timeframe=M15&n=120")
    assert len(d["candles"]) == 120
    times = [c["time"] for c in d["candles"]]
    assert times == sorted(times)
    for c in d["candles"]:
        assert c["low"] <= c["open"] <= c["high"]
        assert c["low"] <= c["close"] <= c["high"]
    assert d["indicators"]["ema_fast"]
    assert d["indicators"]["rsi"]


def test_fx_prices_are_descaled_to_real_quotes(server):
    """The CSVs store EURUSD as 127435.0; served data must be ~1.27."""
    d = get(server, "/api/candles?symbol=EURUSD&timeframe=H1&n=50")
    assert 0.5 < d["last_price"] < 2.0


def test_unknown_symbol_and_timeframe_are_errors(server):
    for path in ("/api/candles?symbol=NOPE&timeframe=M5",
                 "/api/candles?symbol=XAUUSD&timeframe=M7"):
        with pytest.raises(urllib.error.HTTPError):
            get(server, path)


def test_order_requires_confirmation_header(server):
    code, body = post(server, "/api/order",
                      {"symbol": "XAUUSD", "side": "BUY", "lots": 0.02,
                       "sl": 1.0, "tp": 0}, confirm=False)
    assert code == 400
    assert "confirm" in body["error"].lower()


def test_order_without_stop_loss_is_refused(server):
    code, body = post(server, "/api/order",
                      {"symbol": "XAUUSD", "side": "BUY", "lots": 0.02,
                       "sl": 0, "tp": 0})
    assert code == 400
    assert "stop-loss is required" in body["error"]


def test_order_with_bad_side_is_refused(server):
    code, body = post(server, "/api/order",
                      {"symbol": "XAUUSD", "side": "HOLD", "lots": 0.02,
                       "sl": 1.0, "tp": 0})
    assert code == 400
    assert "BUY or SELL" in body["error"]


def test_malformed_json_returns_400_not_500(server):
    req = urllib.request.Request(
        server + "/api/order", data=b"{nope",
        headers={"Content-Type": "application/json"}, method="POST")
    req.add_header(CONFIRM_HEADER, CONFIRM_VALUE)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=30)
    assert e.value.code == 400


def test_order_lifecycle_and_double_close(server):
    price = get(server, "/api/candles?symbol=XAUUSD&timeframe=M5&n=2")["last_price"]
    code, body = post(server, "/api/order",
                      {"symbol": "XAUUSD", "side": "BUY", "lots": 0.02,
                       "sl": round(price - 10, 2), "tp": round(price + 20, 2)})
    assert code == 200, body
    ticket = body["ticket"]
    assert body["simulated"] is True

    positions = get(server, "/api/positions")["positions"]
    assert any(p["ticket"] == ticket for p in positions)

    code, body = post(server, "/api/close", {"ticket": ticket})
    assert code == 200 and body["ticket"] == ticket

    code, body = post(server, "/api/close", {"ticket": ticket})
    assert code == 502 and "no open position" in body["error"]


def test_suggest_lots_warns_when_min_lot_forces_extra_risk(server):
    price = get(server, "/api/candles?symbol=XAUUSD&timeframe=M5&n=2")["last_price"]
    code, body = post(server, "/api/suggest-lots",
                      {"symbol": "XAUUSD", "price": price,
                       "sl": price - 10, "risk_pct": 0.05}, confirm=False)
    assert code == 200
    assert body["risk_pct"] > body["requested_pct"]
    assert body["warning"] and "minimum lot" in body["warning"]


def test_public_bind_is_refused():
    cfg = load_config(CONFIG)
    with pytest.raises(ValueError, match="localhost"):
        make_server(CsvFeed(), cfg, host="0.0.0.0")


def test_read_only_server_refuses_orders():
    cfg = load_config(CONFIG)
    httpd = make_server(CsvFeed(), cfg, host="127.0.0.1", port=0, allow_trading=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert get(base, "/api/config")["trading_enabled"] is False
        code, body = post(base, "/api/order",
                          {"symbol": "XAUUSD", "side": "BUY", "lots": 0.02,
                           "sl": 1.0, "tp": 0})
        assert code == 400 and "disabled" in body["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
