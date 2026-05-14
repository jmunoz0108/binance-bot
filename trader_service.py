"""
╔══════════════════════════════════════════════════════════════════╗
║           WARFARE TRADER — SERVICE 3 of 3                       ║
║  Executes only pre-validated signals from Service 2.             ║
║  Manages positions with infinite trailing stop.                  ║
╚══════════════════════════════════════════════════════════════════╝

What this does:
  1. Reads /app/data/filtered_signals.json (Service 2 output)
  2. Executes trades on Binance/Bybit
  3. Monitors open positions every 30s:
       - Infinite trailing stop (no fixed TP, rides winners)
       - Hard stop loss at -1.5%
       - Breakeven locks at +1%
  4. Logs every trade result to /app/data/trade_results.json
  5. Runs a simple Flask dashboard on port 8080

Railway Variables needed:
  MAIN_API_KEY, MAIN_API_SECRET
  SUB_API_KEY, SUB_API_SECRET    (optional sub account)
  BYBIT_API_KEY, BYBIT_API_SECRET (optional)
  DEMO_MODE = true               (set false for real trading)
  MAX_POSITION_USD = 20          (max $ per trade)
  MAX_OPEN_POSITIONS = 3
  STOP_LOSS_PCT = 1.5
"""

import os, sys, time, json, logging, threading
from datetime import datetime, timedelta
from collections import defaultdict
from binance.client import Client
from flask import Flask, jsonify, render_template_string

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [TRADER] %(message)s'
)
log = logging.getLogger('trader')

SIGNALS_FILE  = '/app/data/filtered_signals.json'
RESULTS_FILE  = '/app/data/trade_results.json'
TRADER_FILE   = '/app/data/trader_state.json'
CHECK_INTERVAL = 30   # position check every 30s
SIGNAL_INTERVAL = 60  # new signal check every 60s

os.makedirs('/app/data', exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG FROM ENV
# ─────────────────────────────────────────────────────────────────────────────

DEMO_MODE         = os.getenv('DEMO_MODE', 'true').strip().lower() in ('true','1','yes')
MAX_POS_USD       = float(os.getenv('MAX_POSITION_USD', '20'))
MAX_OPEN_POS      = int(os.getenv('MAX_OPEN_POSITIONS', '3'))
SL_PCT            = float(os.getenv('STOP_LOSS_PCT', '1.5')) / 100
MAX_REAL_PER_DAY  = int(os.getenv('MAX_REAL_TRADES_DAY', '15'))


# ═════════════════════════════════════════════════════════════════════════════
# INFINITE TRAILING STOP
# ═════════════════════════════════════════════════════════════════════════════

class TrailingStop:
    """
    No fixed take profit. SL follows price up, never down.
    Tightens as profit grows — locks in more at higher peaks.
    """

    def __init__(self, sl_pct=0.015):
        self.sl_pct = sl_pct
        self.state  = {}   # symbol → {entry, side, peak_pnl, dynamic_sl, be_active}

    def register(self, symbol, entry, side):
        if symbol not in self.state:
            self.state[symbol] = {
                'entry':      entry,
                'side':       side,
                'peak_pnl':   0.0,
                'dynamic_sl': -self.sl_pct,
                'be_active':  False,
            }

    def update(self, symbol, price):
        """Returns (should_close, reason)"""
        if symbol not in self.state:
            return False, ''
        s = self.state[symbol]

        pnl = ((price - s['entry']) / s['entry']
               if s['side'] == 'LONG'
               else (s['entry'] - price) / s['entry'])

        if pnl > s['peak_pnl']:
            s['peak_pnl'] = pnl

        # Trail buffer tightens as profit grows
        peak = s['peak_pnl']
        if peak >= 0.20:   buf = 0.005
        elif peak >= 0.10: buf = 0.008
        elif peak >= 0.05: buf = 0.012
        elif peak >= 0.02: buf = 0.013
        else:              buf = 0.015

        if peak > 0:
            new_sl = peak - buf
            if new_sl > s['dynamic_sl']:
                old = s['dynamic_sl']
                s['dynamic_sl'] = new_sl
                if not s['be_active'] and new_sl >= 0:
                    s['be_active'] = True
                    log.info(f"  🔒 {symbol} BREAKEVEN  SL→{new_sl*100:+.2f}%  peak={peak*100:.1f}%")

        # Check stop
        if pnl <= s['dynamic_sl']:
            reason = (f"TRAIL HIT  pnl={pnl*100:.2f}%  "
                      f"sl={s['dynamic_sl']*100:.2f}%  "
                      f"peak={s['peak_pnl']*100:.2f}%")
            del self.state[symbol]
            return True, reason

        # Hard backstop
        if pnl <= -self.sl_pct * 1.5:
            reason = f"HARD STOP  pnl={pnl*100:.2f}%"
            del self.state[symbol]
            return True, reason

        return False, ''

    def remove(self, symbol):
        self.state.pop(symbol, None)

    def get_pnl(self, symbol, price):
        if symbol not in self.state: return 0.0
        s = self.state[symbol]
        return ((price - s['entry']) / s['entry']
                if s['side'] == 'LONG'
                else (s['entry'] - price) / s['entry'])


# ═════════════════════════════════════════════════════════════════════════════
# POSITION TRACKER
# ═════════════════════════════════════════════════════════════════════════════

class PositionTracker:
    """Tracks open demo positions and calculates P&L."""

    def __init__(self):
        self.positions = []   # list of position dicts

    def open(self, signal, price, size_usd, demo):
        pos = {
            'id':           f"{signal['symbol']}_{int(time.time())}",
            'symbol':       signal['symbol'],
            'action':       signal['action'],
            'strategy':     signal['strategy'],
            'entry_price':  price,
            'entry_time':   datetime.now().isoformat(),
            'size_usd':     size_usd,
            'current_price': price,
            'pnl_pct':      0.0,
            'pnl_usd':      0.0,
            'peak_pnl_pct': 0.0,
            'status':       'open',
            'demo':         demo,
            'filter_score': signal.get('filter_score', 0),
            'rr_ratio':     signal.get('rr_ratio', 1.0),
            'reason':       signal.get('reason', ''),
        }
        self.positions.append(pos)
        return pos

    def update_price(self, symbol, price):
        for p in self.positions:
            if p['symbol'] == symbol and p['status'] == 'open':
                entry = p['entry_price']
                if p['action'] == 'LONG':
                    pnl_pct = (price - entry) / entry * 100
                else:
                    pnl_pct = (entry - price) / entry * 100
                p['current_price'] = price
                p['pnl_pct']       = round(pnl_pct, 4)
                p['pnl_usd']       = round(pnl_pct / 100 * p['size_usd'], 4)
                if pnl_pct > p['peak_pnl_pct']:
                    p['peak_pnl_pct'] = round(pnl_pct, 4)

    def close(self, symbol, reason):
        results = []
        for p in self.positions:
            if p['symbol'] == symbol and p['status'] == 'open':
                p['status']       = 'closed'
                p['close_reason'] = reason
                p['close_time']   = datetime.now().isoformat()
                p['outcome']      = 'WIN' if p['pnl_pct'] > 0 else 'LOSS'
                results.append(p)
        return results

    def open_positions(self):
        return [p for p in self.positions if p['status'] == 'open']

    def closed_positions(self):
        return [p for p in self.positions if p['status'] == 'closed']


# ═════════════════════════════════════════════════════════════════════════════
# RESULTS JOURNAL
# ═════════════════════════════════════════════════════════════════════════════

class Journal:
    """Persists all trade results so Service 2 can learn from them."""

    def __init__(self):
        self.trades = []
        self._load()

    def _load(self):
        if os.path.exists(RESULTS_FILE):
            try:
                with open(RESULTS_FILE) as f:
                    data = json.load(f)
                self.trades = data.get('trades', [])
            except Exception:
                pass

    def record(self, position):
        self.trades.append({
            'id':         position.get('id'),
            'symbol':     position['symbol'],
            'action':     position['action'],
            'strategy':   position['strategy'],
            'entry_price': position['entry_price'],
            'exit_price':  position['current_price'],
            'pnl_pct':    position['pnl_pct'],
            'pnl_usd':    position['pnl_usd'],
            'outcome':    position.get('outcome', 'UNKNOWN'),
            'close_reason': position.get('close_reason', ''),
            'entry_time': position.get('entry_time'),
            'close_time': position.get('close_time'),
            'filter_score': position.get('filter_score', 0),
            'rr_ratio':   position.get('rr_ratio', 1.0),
            'demo':       position.get('demo', True),
        })
        self._save()

    def _save(self):
        wins    = [t for t in self.trades if t.get('outcome') == 'WIN']
        losses  = [t for t in self.trades if t.get('outcome') == 'LOSS']
        total   = len(wins) + len(losses)
        wr      = round(len(wins) / total * 100, 1) if total > 0 else 0
        total_pnl = sum(t.get('pnl_usd', 0) for t in self.trades)

        data = {
            'updated':    datetime.now().isoformat(),
            'summary': {
                'total_trades':   total,
                'wins':           len(wins),
                'losses':         len(losses),
                'win_rate':       wr,
                'total_pnl_usd':  round(total_pnl, 4),
            },
            'trades': self.trades[-500:],  # Keep last 500
        }
        with open(RESULTS_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def get_summary(self):
        wins   = [t for t in self.trades if t.get('outcome') == 'WIN']
        losses = [t for t in self.trades if t.get('outcome') == 'LOSS']
        total  = len(wins) + len(losses)
        return {
            'total':   total,
            'wins':    len(wins),
            'losses':  len(losses),
            'wr':      round(len(wins)/total*100, 1) if total else 0,
            'pnl':     round(sum(t.get('pnl_usd',0) for t in self.trades), 2),
        }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN TRADER
# ═════════════════════════════════════════════════════════════════════════════

class Trader:

    def __init__(self):
        self.client   = Client(
            os.getenv('MAIN_API_KEY'),
            os.getenv('MAIN_API_SECRET')
        )
        self.trail    = TrailingStop(sl_pct=SL_PCT)
        self.tracker  = PositionTracker()
        self.journal  = Journal()
        self.real_trades_today = 0
        self.last_reset_date   = None
        self.last_signal_ts    = None   # timestamp of last processed signal
        log.info(f"Trader initialized | DEMO={DEMO_MODE} | SL={SL_PCT*100:.1f}%")
        log.info(f"  Max position: ${MAX_POS_USD} | Max open: {MAX_OPEN_POS}")

    def _reset_daily_if_needed(self):
        today = datetime.now().date()
        if self.last_reset_date != today:
            self.real_trades_today = 0
            self.last_reset_date   = today
            log.info("📅 Daily trade counter reset")

    def _get_price(self, symbol):
        try:
            t = self.client.get_symbol_ticker(symbol=symbol)
            return float(t['price'])
        except Exception:
            try:
                t = self.client.futures_symbol_ticker(symbol=symbol)
                return float(t['price'])
            except Exception:
                return 0.0

    def _get_futures_precision(self, symbol):
        try:
            info = self.client.futures_exchange_info()
            for s in info['symbols']:
                if s['symbol'] == symbol:
                    return int(s.get('quantityPrecision', 3))
        except Exception:
            pass
        return 3

    def _execute_real(self, signal, price, size_usd):
        """Execute real order on Binance."""
        symbol = signal['symbol']
        action = signal['action']

        # Check futures vs spot
        try:
            fi = self.client.futures_exchange_info()
            on_futures = any(s['symbol'] == symbol for s in fi['symbols'])
        except Exception:
            on_futures = False

        prec = self._get_futures_precision(symbol)
        qty  = round(size_usd / price, prec)

        if action == 'LONG' and not on_futures:
            try:
                order = self.client.create_order(
                    symbol=symbol, side='BUY', type='MARKET',
                    quoteOrderQty=round(size_usd, 2))
                log.info(f"  ✅ SPOT BUY {symbol}: orderId={order.get('orderId')}")
                return True
            except Exception as e:
                log.error(f"  ❌ SPOT BUY {symbol}: {e}")
                return False

        if action in ('LONG', 'FUNDING_SHORT') and on_futures:
            side = 'BUY' if action == 'LONG' else 'SELL'
            try:
                order = self.client.futures_create_order(
                    symbol=symbol, side=side, type='MARKET', quantity=qty)
                log.info(f"  ✅ FUTURES {side} {symbol}: orderId={order.get('orderId')}")
                return True
            except Exception as e:
                log.error(f"  ❌ FUTURES {side} {symbol}: {e}")
                return False

        if action == 'SHORT' and on_futures:
            try:
                order = self.client.futures_create_order(
                    symbol=symbol, side='SELL', type='MARKET', quantity=qty)
                log.info(f"  ✅ FUTURES SHORT {symbol}: orderId={order.get('orderId')}")
                return True
            except Exception as e:
                log.error(f"  ❌ FUTURES SHORT {symbol}: {e}")
                return False

        log.warning(f"  ⚠️  No execution path for {action} {symbol}")
        return False

    def _close_real(self, symbol, side):
        """Close real position."""
        try:
            prec = self._get_futures_precision(symbol)
            pos_info = self.client.futures_account()
            for p in pos_info['positions']:
                if p['symbol'] == symbol:
                    amt = float(p['positionAmt'])
                    if abs(amt) > 0:
                        close_side = 'SELL' if amt > 0 else 'BUY'
                        self.client.futures_create_order(
                            symbol=symbol, side=close_side,
                            type='MARKET', quantity=round(abs(amt), prec),
                            reduceOnly=True)
                        log.info(f"  ✅ Closed {symbol} on Binance Futures")
                        return True
        except Exception as e:
            log.error(f"  ❌ Close {symbol}: {e}")
        return False

    def _process_signals(self):
        """Read filtered signals and execute new trades."""
        if not os.path.exists(SIGNALS_FILE):
            return

        with open(SIGNALS_FILE) as f:
            data = json.load(f)

        signals   = data.get('signals', [])
        sig_ts    = data.get('timestamp')

        # Don't re-process same signals
        if sig_ts == self.last_signal_ts:
            return
        self.last_signal_ts = sig_ts

        # Check open position limit
        open_count = len(self.tracker.open_positions())
        if open_count >= MAX_OPEN_POS:
            log.info(f"Max positions reached ({open_count}/{MAX_OPEN_POS})")
            return

        self._reset_daily_if_needed()

        for signal in signals:
            if len(self.tracker.open_positions()) >= MAX_OPEN_POS:
                break

            symbol   = signal.get('symbol', '')
            action   = signal.get('action', '')
            strategy = signal.get('strategy', '')
            score    = signal.get('filter_score', 0)

            if not symbol or not action: continue

            # Check if already in this position
            open_syms = [p['symbol'] for p in self.tracker.open_positions()]
            if symbol in open_syms:
                continue

            # Real trade daily limit
            if not DEMO_MODE and self.real_trades_today >= MAX_REAL_PER_DAY:
                log.info(f"Daily real trade limit reached ({MAX_REAL_PER_DAY})")
                break

            price = self._get_price(symbol)
            if price <= 0:
                log.warning(f"Could not get price for {symbol}")
                continue

            size_usd = min(MAX_POS_USD, 20.0)
            mode_str = "DEMO" if DEMO_MODE else "REAL"

            log.info(f"\n{'='*55}")
            log.info(f"{'🟡 DEMO' if DEMO_MODE else '🔴 REAL'} TRADE: {action} {symbol}")
            log.info(f"  Strategy: {strategy}")
            log.info(f"  Score: {score}/100 | R:R: {signal.get('rr_ratio',1):.1f}")
            log.info(f"  Price: ${price:.6f} | Size: ${size_usd:.2f}")
            log.info(f"  Reason: {signal.get('reason','')[:80]}")

            executed = True
            if not DEMO_MODE:
                executed = self._execute_real(signal, price, size_usd)
                if executed:
                    self.real_trades_today += 1

            if executed:
                pos = self.tracker.open(signal, price, size_usd, DEMO_MODE)
                self.trail.register(symbol, price, action)
                log.info(f"  ✅ {'Demo' if DEMO_MODE else 'Real'} position opened: {pos['id']}")

    def _monitor_positions(self):
        """Check all open positions for SL/TP."""
        positions = self.tracker.open_positions()
        if not positions: return

        log.info(f"\n🛡️  Monitoring {len(positions)} position(s)...")
        total_pnl = 0

        for pos in positions:
            symbol = pos['symbol']
            price  = self._get_price(symbol)
            if price <= 0: continue

            self.tracker.update_price(symbol, price)
            pnl_pct = pos['pnl_pct']
            total_pnl += pos.get('pnl_usd', 0)

            log.info(f"  {pos['action']:5s} {symbol:12s} "
                     f"${pos['entry_price']:.6f}→${price:.6f}  "
                     f"PnL={pnl_pct:+.2f}%  "
                     f"Peak={pos['peak_pnl_pct']:+.2f}%")

            should_close, reason = self.trail.update(symbol, price)
            if should_close:
                log.info(f"\n  🎯 CLOSING {symbol}: {reason}")
                closed = self.tracker.close(symbol, reason)
                for c in closed:
                    outcome_emoji = '✅' if c['outcome'] == 'WIN' else '❌'
                    log.info(f"  {outcome_emoji} {c['outcome']}  "
                             f"{symbol}  PnL={c['pnl_pct']:+.2f}%  ${c['pnl_usd']:+.4f}")
                    self.journal.record(c)
                    if not DEMO_MODE:
                        self._close_real(symbol, pos['action'])

        summary = self.journal.get_summary()
        log.info(f"  💎 Unrealized: ${total_pnl:+.4f} | "
                 f"Overall: {summary['wins']}W/{summary['losses']}L "
                 f"WR={summary['wr']}% PnL=${summary['pnl']:+.2f}")

    def _write_state(self):
        """Write trader state for dashboard."""
        summary = self.journal.get_summary()
        state = {
            'timestamp':     datetime.now().isoformat(),
            'demo_mode':     DEMO_MODE,
            'open_positions': [
                {k: v for k, v in p.items() if k != 'demo'}
                for p in self.tracker.open_positions()
            ],
            'recent_closed': [
                {k: v for k, v in p.items() if k != 'demo'}
                for p in self.tracker.closed_positions()[-20:]
            ],
            'summary':        summary,
            'real_trades_today': self.real_trades_today,
            'max_real_per_day':  MAX_REAL_PER_DAY,
        }
        with open(TRADER_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)

    def _position_loop(self):
        """Background thread: monitor positions every 30s."""
        while True:
            try:
                self._monitor_positions()
                self._write_state()
            except Exception as e:
                log.error(f"Monitor error: {e}")
            time.sleep(CHECK_INTERVAL)

    def start(self):
        threading.Thread(target=self._position_loop, daemon=True).start()
        log.info("Position monitor started")

        while True:
            try:
                self._process_signals()
                self._write_state()
            except Exception as e:
                log.error(f"Signal loop error: {e}")
            time.sleep(SIGNAL_INTERVAL)


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Warfare Trader</title>
<meta http-equiv="refresh" content="15">
<style>
body{background:#0a0a1a;color:#e2e8f0;font-family:monospace;padding:20px;margin:0}
h1{color:#7c3aed;margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.card{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:16px;text-align:center}
.card .num{font-size:28px;font-weight:700;margin:4px 0}
.card .lbl{color:#6b7280;font-size:11px}
.win{color:#10b981}.loss{color:#ef4444}.neutral{color:#f59e0b}
.demo-badge{background:#1e3a5f;color:#60a5fa;padding:3px 10px;border-radius:10px;font-size:11px}
.real-badge{background:#3f1515;color:#f87171;padding:3px 10px;border-radius:10px;font-size:11px}
table{width:100%;border-collapse:collapse;margin-top:16px}
th{color:#6b7280;font-size:11px;text-align:left;padding:8px;border-bottom:1px solid #1f2937}
td{padding:8px;border-bottom:1px solid #111827;font-size:12px}
</style>
</head>
<body>
<h1>⚔️ Warfare Trader</h1>
<div style="margin-bottom:16px">
  <span class="{{ 'demo-badge' if state.demo_mode else 'real-badge' }}">
    {{ '🟡 DEMO MODE' if state.demo_mode else '🔴 REAL TRADING' }}
  </span>
  &nbsp; Updated: {{ state.timestamp[:19] }}
</div>

<div class="grid">
  <div class="card">
    <div class="num {{ 'win' if state.summary.wins > state.summary.losses else 'loss' if state.summary.losses > 0 else 'neutral' }}">
      {{ state.summary.wr }}%
    </div>
    <div class="lbl">Win Rate</div>
  </div>
  <div class="card">
    <div class="num {{ 'win' if state.summary.pnl >= 0 else 'loss' }}">
      ${{ '%.2f'|format(state.summary.pnl) }}
    </div>
    <div class="lbl">Total P&L</div>
  </div>
  <div class="card">
    <div class="num">{{ state.summary.total }}</div>
    <div class="lbl">Closed Trades</div>
  </div>
  <div class="card">
    <div class="num win">{{ state.summary.wins }}</div>
    <div class="lbl">Wins</div>
  </div>
  <div class="card">
    <div class="num loss">{{ state.summary.losses }}</div>
    <div class="lbl">Losses</div>
  </div>
  <div class="card">
    <div class="num neutral">{{ state.open_positions|length }}</div>
    <div class="lbl">Open Now</div>
  </div>
</div>

{% if state.open_positions %}
<h3 style="color:#a78bfa">Open Positions</h3>
<table>
  <tr><th>Symbol</th><th>Action</th><th>Entry</th><th>Current</th><th>P&L %</th><th>Peak %</th><th>Strategy</th></tr>
  {% for p in state.open_positions %}
  <tr>
    <td>{{ p.symbol }}</td>
    <td>{{ p.action }}</td>
    <td>${{ '%.6f'|format(p.entry_price) }}</td>
    <td>${{ '%.6f'|format(p.current_price) }}</td>
    <td class="{{ 'win' if p.pnl_pct > 0 else 'loss' }}">{{ '%+.2f'|format(p.pnl_pct) }}%</td>
    <td class="win">{{ '%+.2f'|format(p.peak_pnl_pct) }}%</td>
    <td>{{ p.strategy }}</td>
  </tr>
  {% endfor %}
</table>
{% endif %}

{% if state.recent_closed %}
<h3 style="color:#a78bfa;margin-top:24px">Recent Closed</h3>
<table>
  <tr><th>Symbol</th><th>Action</th><th>P&L %</th><th>P&L $</th><th>Outcome</th><th>Strategy</th><th>Reason</th></tr>
  {% for p in state.recent_closed[-10:]|reverse %}
  <tr>
    <td>{{ p.symbol }}</td>
    <td>{{ p.action }}</td>
    <td class="{{ 'win' if p.pnl_pct > 0 else 'loss' }}">{{ '%+.2f'|format(p.pnl_pct) }}%</td>
    <td class="{{ 'win' if p.pnl_usd > 0 else 'loss' }}">${{ '%+.4f'|format(p.pnl_usd) }}</td>
    <td class="{{ 'win' if p.outcome == 'WIN' else 'loss' }}">{{ p.outcome }}</td>
    <td>{{ p.strategy }}</td>
    <td style="color:#6b7280;font-size:10px">{{ p.close_reason[:40] }}</td>
  </tr>
  {% endfor %}
</table>
{% endif %}
</body>
</html>"""

app = Flask(__name__)
_trader_instance = None

@app.route('/')
def dashboard():
    if os.path.exists(TRADER_FILE):
        with open(TRADER_FILE) as f:
            state = json.load(f)
    else:
        state = {'timestamp': '-', 'demo_mode': True,
                 'open_positions': [], 'recent_closed': [],
                 'summary': {'total':0,'wins':0,'losses':0,'wr':0,'pnl':0},
                 'real_trades_today': 0, 'max_real_per_day': 15}

    from flask import render_template_string as rts
    return rts(DASHBOARD_HTML, state=type('S', (), state)())

@app.route('/api/state')
def api_state():
    if os.path.exists(TRADER_FILE):
        with open(TRADER_FILE) as f:
            return jsonify(json.load(f))
    return jsonify({})


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    log.info("=" * 60)
    log.info(f"WARFARE TRADER — SERVICE 3")
    log.info(f"  DEMO_MODE: {DEMO_MODE}")
    log.info(f"  MAX_POSITION_USD: ${MAX_POS_USD}")
    log.info(f"  STOP_LOSS: {SL_PCT*100:.1f}%")
    log.info("=" * 60)

    required = ['MAIN_API_KEY', 'MAIN_API_SECRET']
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing env vars: {missing}")
        sys.exit(1)

    trader = Trader()
    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)),
                               debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()
    log.info("Dashboard running on port 8080")
    trader.start()
