'use strict';
/* Gold Bot dashboard front-end.
 *
 * Talks to the local server only. Every order goes through a confirmation
 * dialog that spells out the money at risk before anything is sent - the
 * one place a mis-click would be expensive.
 */

const CONFIRM_HEADER = 'X-Trade-Confirm';
const CONFIRM_VALUE = 'i-understand-this-places-a-real-order';

const state = {
  cfg: null,
  symbol: null,
  timeframe: 'M5',
  lastPrice: null,
  lastAtr: null,
  isLive: false,
  pending: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function post(path, payload, withConfirm) {
  const headers = { 'Content-Type': 'application/json' };
  if (withConfirm) headers[CONFIRM_HEADER] = CONFIRM_VALUE;
  return api(path, { method: 'POST', headers, body: JSON.stringify(payload) });
}

/* ------------------------------------------------------------- charts */
let chart, candleSeries, emaFast, emaSlow, bbUpper, bbLower;
let rsiChart, rsiSeries;

const CHART_THEME = {
  layout: { background: { color: '#161b24' }, textColor: '#8b98a9' },
  grid: { vertLines: { color: '#1d2430' }, horzLines: { color: '#1d2430' } },
  rightPriceScale: { borderColor: '#2a3140' },
  timeScale: { borderColor: '#2a3140', timeVisible: true },
  crosshair: { mode: 0 },
};

function initCharts() {
  // Degrade instead of dying: if the chart library is missing, the account
  // and position panels are still the parts you need to manage open risk.
  if (typeof LightweightCharts === 'undefined') {
    $('chart').innerHTML =
      '<div class="notice" style="margin:14px">Chart library failed to load. ' +
      'Account and positions below still work.</div>';
    $('rsi-chart').classList.add('hidden');
    return false;
  }
  chart = LightweightCharts.createChart($('chart'), {
    ...CHART_THEME, autoSize: true,
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350',
    borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  });
  emaFast = chart.addLineSeries({ color: '#4d8df7', lineWidth: 1, priceLineVisible: false });
  emaSlow = chart.addLineSeries({ color: '#e3b341', lineWidth: 1, priceLineVisible: false });
  bbUpper = chart.addLineSeries({ color: '#7c5cff', lineWidth: 1, priceLineVisible: false });
  bbLower = chart.addLineSeries({ color: '#7c5cff', lineWidth: 1, priceLineVisible: false });

  rsiChart = LightweightCharts.createChart($('rsi-chart'), {
    ...CHART_THEME, autoSize: true,
  });
  rsiSeries = rsiChart.addLineSeries({ color: '#8b98a9', lineWidth: 1 });
  // 30/70 guides, the levels the bot's pullback logic keys on.
  [30, 70].forEach((lvl) => {
    rsiSeries.createPriceLine({ price: lvl, color: '#2a3140', lineWidth: 1, axisLabelVisible: true });
  });

  // Keep the two panes scrolled together so RSI always matches the candles above.
  let syncing = false;
  const link = (a, b) => a.timeScale().subscribeVisibleLogicalRangeChange((r) => {
    if (syncing || !r) return;
    syncing = true;
    b.timeScale().setVisibleLogicalRange(r);
    syncing = false;
  });
  link(chart, rsiChart);
  link(rsiChart, chart);
  return true;
}

async function loadChart() {
  const q = `?symbol=${encodeURIComponent(state.symbol)}&timeframe=${state.timeframe}&n=600`;
  const data = await api('/api/candles' + q);
  // ATR and last price still drive the order panel even with no chart.
  state.lastPrice = data.last_price;
  const atrAll = (data.indicators || {}).atr || [];
  state.lastAtr = atrAll.length ? atrAll[atrAll.length - 1].value : null;
  $('last-price').textContent = fmtPrice(data.last_price);
  if (!chart) return;

  candleSeries.setData(data.candles);
  const ind = data.indicators || {};
  emaFast.setData($('show-ema').checked ? (ind.ema_fast || []) : []);
  emaSlow.setData($('show-ema').checked ? (ind.ema_slow || []) : []);
  bbUpper.setData($('show-bb').checked ? (ind.bb_upper || []) : []);
  bbLower.setData($('show-bb').checked ? (ind.bb_lower || []) : []);
  rsiSeries.setData(ind.rsi || []);
}

function fmtPrice(v) {
  if (v == null) return '—';
  return v >= 100 ? v.toFixed(2) : v.toFixed(5);
}

/* ---------------------------------------------------------- watchlist */
async function loadWatchlist() {
  const wl = $('watchlist');
  wl.innerHTML = '';
  for (const sym of state.cfg.symbols) {
    const card = document.createElement('div');
    card.className = 'wl-item' + (sym === state.symbol ? ' active' : '');
    card.innerHTML = `<div class="wl-sym">${sym}</div>
                      <div class="wl-price muted">…</div>
                      <div class="wl-chg muted">&nbsp;</div>`;
    card.onclick = () => { $('symbol').value = sym; changeSymbol(sym); };
    wl.appendChild(card);

    const tf = sym.startsWith('XAU') ? 'M5' : 'M15';
    api(`/api/candles?symbol=${sym}&timeframe=${tf}&n=60`).then((d) => {
      const c = d.candles;
      if (!c.length) return;
      const last = c[c.length - 1].close;
      const first = c[0].close;
      const pct = ((last - first) / first) * 100;
      card.querySelector('.wl-price').textContent = fmtPrice(last);
      card.querySelector('.wl-price').classList.remove('muted');
      const chg = card.querySelector('.wl-chg');
      chg.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
      chg.className = 'wl-chg ' + (pct >= 0 ? 'up' : 'down');
    }).catch(() => {});
  }
}

/* ------------------------------------------------------------ account */
async function loadAccount() {
  try {
    const a = await api('/api/account');
    state.isLive = a.live;
    $('conn-dot').className = 'dot ok';
    $('account-bar').innerHTML = `
      <span class="muted">Account</span> <b>${a.login}</b>
      <span class="muted">${a.server}</span>
      <span><span class="muted">Balance</span> <b>${a.balance} ${a.currency}</b></span>
      <span><span class="muted">Equity</span> <b>${a.equity}</b></span>
      <span><span class="muted">Free margin</span> <b>${a.margin_free}</b></span>
      <span><span class="muted">Leverage</span> <b>${a.leverage}</b></span>`;
    const badge = $('mode-badge');
    if (!state.cfg.trading_enabled) {
      badge.textContent = 'READ ONLY';
      badge.className = 'badge badge-sim';
    } else if (a.live) {
      badge.textContent = 'LIVE TRADING';
      badge.className = 'badge badge-live';
    } else {
      badge.textContent = 'SIMULATED';
      badge.className = 'badge badge-sim';
    }
  } catch (e) {
    $('conn-dot').className = 'dot bad';
    $('account-bar').innerHTML = `<span class="muted">disconnected: ${e.message}</span>`;
  }
}

async function loadPositions() {
  try {
    const { positions } = await api('/api/positions');
    const el = $('positions');
    if (!positions.length) {
      el.className = 'positions muted';
      el.textContent = 'none';
      return;
    }
    el.className = 'positions';
    const rows = positions.map((p) => `
      <tr>
        <td>${p.symbol}</td>
        <td class="${p.side === 'BUY' ? 'up' : 'down'}">${p.side}</td>
        <td>${p.lots}</td>
        <td class="${p.profit >= 0 ? 'up' : 'down'}">${p.profit}</td>
        <td><button class="ghost" data-ticket="${p.ticket}">Close</button></td>
      </tr>`).join('');
    el.innerHTML = `<table><tr><th>Symbol</th><th>Side</th><th>Lots</th>
                    <th>P/L</th><th></th></tr>${rows}</table>`;
    el.querySelectorAll('button[data-ticket]').forEach((b) => {
      b.onclick = () => confirmClose(Number(b.dataset.ticket));
    });
  } catch (e) {
    $('positions').textContent = e.message;
  }
}

/* -------------------------------------------------------------- order */
function fillFromAtr() {
  if (!state.lastAtr || !state.lastPrice) {
    return msg('No ATR available yet - load a chart first.', true);
  }
  // Direction is inferred from which side you are about to trade; default
  // to a long-shaped stop and let the user flip the numbers for a sell.
  const sl = state.lastPrice - state.cfg.atr_sl_mult * state.lastAtr;
  const tp = state.lastPrice + state.cfg.atr_tp_mult * state.lastAtr;
  $('sl').value = sl.toFixed(state.lastPrice >= 100 ? 2 : 5);
  $('tp').value = tp.toFixed(state.lastPrice >= 100 ? 2 : 5);
  msg(`Filled for a BUY using ATR ${state.lastAtr.toFixed(2)} ` +
      `(SL ${state.cfg.atr_sl_mult}x, TP ${state.cfg.atr_tp_mult}x). ` +
      `For a SELL, mirror them around the price.`, false);
  updateRisk();
}

async function calcLots() {
  const sl = parseFloat($('sl').value);
  if (!sl) return msg('Set a stop loss first.', true);
  try {
    const r = await post('/api/suggest-lots', {
      symbol: state.symbol, price: state.lastPrice, sl,
      risk_pct: parseFloat($('risk-pct').value),
    }, false);
    $('lots').value = r.lots;
    updateRisk();
    // A broker minimum that overshoots the requested risk is the single
    // most dangerous thing on a small account - surface it, never hide it.
    if (r.warning) {
      msg(r.warning, true);
      $('risk-readout').classList.add('risky');
    } else {
      msg(`Sized ${r.lots} lots - risking $${r.risk_usd} (${r.risk_pct}%).`, false);
      $('risk-readout').classList.remove('risky');
    }
  } catch (e) { msg(e.message, true); }
}

function updateRisk() {
  const sl = parseFloat($('sl').value);
  const lots = parseFloat($('lots').value);
  const box = $('risk-readout');
  if (!sl || !lots || !state.lastPrice) {
    box.className = 'readout muted';
    box.textContent = 'Set a stop loss to see risk.';
    return;
  }
  const contract = state.symbol.startsWith('XAU') ? 100 : 100000;
  const risk = Math.abs(state.lastPrice - sl) * contract * lots;
  box.className = 'readout';
  box.innerHTML = `Risk if stopped: <b>$${risk.toFixed(2)}</b> ` +
                  `<span class="muted">(${lots} lots, ` +
                  `${Math.abs(state.lastPrice - sl).toFixed(2)} away)</span>`;
  return risk;
}

function msg(text, isError) {
  const el = $('order-msg');
  el.textContent = text;
  el.className = 'msg ' + (isError ? 'err' : 'ok');
}

function confirmOrder(side) {
  const sl = parseFloat($('sl').value);
  const tp = parseFloat($('tp').value) || 0;
  const lots = parseFloat($('lots').value);
  if (!sl) return msg('A stop loss is required.', true);
  if (!lots) return msg('Set a lot size.', true);
  const risk = updateRisk();

  $('confirm-title').textContent = `Confirm ${side} ${state.symbol}`;
  $('confirm-body').innerHTML =
    (state.isLive
      ? `<div class="live-warning">This is a LIVE account. Sending this
         order will use real money.</div>` : '') +
    `<dl>
       <dt>Side</dt><dd>${side}</dd>
       <dt>Symbol</dt><dd>${state.symbol}</dd>
       <dt>Lots</dt><dd>${lots}</dd>
       <dt>Price now</dt><dd>${fmtPrice(state.lastPrice)}</dd>
       <dt>Stop loss</dt><dd>${fmtPrice(sl)}</dd>
       <dt>Take profit</dt><dd>${tp ? fmtPrice(tp) : 'none'}</dd>
       <dt>Risk if stopped</dt><dd>$${risk ? risk.toFixed(2) : '?'}</dd>
     </dl>`;
  state.pending = { kind: 'order', side, sl, tp, lots };
  $('confirm-backdrop').classList.remove('hidden');
}

function confirmClose(ticket) {
  $('confirm-title').textContent = 'Close position';
  $('confirm-body').innerHTML =
    (state.isLive ? `<div class="live-warning">This closes a real position.</div>` : '') +
    `<p>Close ticket <b>${ticket}</b> at market?</p>`;
  state.pending = { kind: 'close', ticket };
  $('confirm-backdrop').classList.remove('hidden');
}

async function runPending() {
  const p = state.pending;
  $('confirm-backdrop').classList.add('hidden');
  if (!p) return;
  state.pending = null;
  try {
    if (p.kind === 'order') {
      const r = await post('/api/order', {
        symbol: state.symbol, side: p.side, lots: p.lots, sl: p.sl, tp: p.tp,
      }, true);
      msg(`${p.side} sent - ticket ${r.ticket} at ${fmtPrice(r.price)}` +
          (r.simulated ? ' (simulated)' : ''), false);
    } else {
      const r = await post('/api/close', { ticket: p.ticket }, true);
      msg(`Closed ticket ${r.ticket}, P/L ${r.pnl ?? '—'}`, false);
    }
    loadAccount(); loadPositions();
  } catch (e) {
    msg(e.message, true);
  }
}

/* --------------------------------------------------------------- init */
function changeSymbol(sym) {
  state.symbol = sym;
  document.querySelectorAll('.wl-item').forEach((el) => {
    el.classList.toggle('active', el.querySelector('.wl-sym').textContent === sym);
  });
  loadChart().catch((e) => msg(e.message, true));
}

async function init() {
  initCharts();
  state.cfg = await api('/api/config');
  state.symbol = state.cfg.symbols[0];
  $('risk-pct').value = state.cfg.default_risk_pct;
  $('risk-pct').max = state.cfg.max_risk_pct;

  const symSel = $('symbol');
  state.cfg.symbols.forEach((s) => symSel.add(new Option(s, s)));
  symSel.value = state.symbol;
  const tfSel = $('timeframe');
  state.cfg.timeframes.forEach((t) => tfSel.add(new Option(t, t)));
  tfSel.value = state.timeframe;

  if (!state.cfg.trading_enabled) {
    $('trade-disabled').classList.remove('hidden');
    ['buy', 'sell'].forEach((id) => { $(id).disabled = true; });
  }

  symSel.onchange = () => changeSymbol(symSel.value);
  tfSel.onchange = () => { state.timeframe = tfSel.value; loadChart(); };
  $('refresh').onclick = () => { loadChart(); loadAccount(); loadPositions(); };
  $('show-ema').onchange = loadChart;
  $('show-bb').onchange = loadChart;
  $('atr-fill').onclick = fillFromAtr;
  $('calc-lots').onclick = calcLots;
  $('sl').oninput = updateRisk;
  $('lots').oninput = updateRisk;
  $('buy').onclick = () => confirmOrder('BUY');
  $('sell').onclick = () => confirmOrder('SELL');
  $('confirm-cancel').onclick = () => {
    state.pending = null;
    $('confirm-backdrop').classList.add('hidden');
  };
  $('confirm-ok').onclick = runPending;

  await loadChart();
  await loadWatchlist();
  await loadAccount();
  await loadPositions();

  setInterval(() => { loadChart().catch(() => {}); }, 15000);
  setInterval(loadAccount, 10000);
  setInterval(loadPositions, 10000);
}

init().catch((e) => {
  document.body.insertAdjacentHTML('afterbegin',
    `<div class="notice" style="margin:14px">Failed to start: ${e.message}</div>`);
});
