"""
WARFARE TRADER — Fixed version
Reads signals from Filter HTTP API instead of local file.
"""
import os, sys, time, json, logging, threading
from datetime import datetime, timedelta
from binance.client import Client
from flask import Flask, jsonify, render_template_string
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [TRADER] %(message)s')
log = logging.getLogger('trader')

FILTER_URL    = os.getenv('FILTER_URL', '')   # ← reads from filter API
RESULTS_FILE  = '/app/data/trade_results.json'
TRADER_FILE   = '/app/data/trader_state.json'
CHECK_INTERVAL  = 30
SIGNAL_INTERVAL = 60

os.makedirs('/app/data', exist_ok=True)

DEMO_MODE        = os.getenv('DEMO_MODE', 'true').strip().lower() in ('true','1','yes')
MAX_POS_USD      = float(os.getenv('MAX_POSITION_USD', '20'))
MAX_OPEN_POS     = int(os.getenv('MAX_OPEN_POSITIONS', '3'))
SL_PCT           = float(os.getenv('STOP_LOSS_PCT', '1.5')) / 100
MAX_REAL_PER_DAY = int(os.getenv('MAX_REAL_TRADES_DAY', '15'))

log.info("=" * 55)
log.info("WARFARE TRADER — Fixed (reads Filter API)")
log.info(f"  FILTER_URL:  {FILTER_URL or 'NOT SET ❌'}")
log.info(f"  DEMO_MODE:   {DEMO_MODE}")
log.info(f"  SL:          {SL_PCT*100:.1f}%")
log.info("=" * 55)


class TrailingStop:
    def __init__(self, sl_pct=0.015):
        self.sl_pct = sl_pct
        self.state  = {}

    def register(self, symbol, entry, side):
        if symbol not in self.state:
            self.state[symbol] = {
                'entry': entry, 'side': side,
                'peak_pnl': 0.0, 'dynamic_sl': -self.sl_pct, 'be_active': False
            }

    def update(self, symbol, price):
        if symbol not in self.state:
            return False, ''
        s = self.state[symbol]
        pnl = ((price - s['entry']) / s['entry'] if s['side'] == 'LONG'
               else (s['entry'] - price) / s['entry'])
        if pnl > s['peak_pnl']:
            s['peak_pnl'] = pnl
        peak = s['peak_pnl']
        if peak >= 0.20:   buf = 0.005
        elif peak >= 0.10: buf = 0.008
        elif peak >= 0.05: buf = 0.012
        elif peak >= 0.02: buf = 0.013
        else:              buf = 0.015
        if peak > 0:
            new_sl = peak - buf
            if new_sl > s['dynamic_sl']:
                s['dynamic_sl'] = new_sl
                if not s['be_active'] and new_sl >= 0:
                    s['be_active'] = True
                    log.info(f"  🔒 {symbol} BREAKEVEN SL→{new_sl*100:+.2f}%")
        if pnl <= s['dynamic_sl']:
            reason = (f"TRAIL HIT pnl={pnl*100:.2f}% "
                      f"sl={s['dynamic_sl']*100:.2f}% peak={s['peak_pnl']*100:.2f}%")
            del self.state[symbol]
            return True, reason
        if pnl <= -self.sl_pct * 1.5:
            reason = f"HARD STOP pnl={pnl*100:.2f}%"
            del self.state[symbol]
            return True, reason
        return False, ''

    def get_state(self, symbol):
        return self.state.get(symbol, {})

    def remove(self, symbol):
        self.state.pop(symbol, None)


class PositionTracker:
    def __init__(self):
        self.positions = []

    def open(self, signal, price, size_usd, demo):
        pos = {
            'id':            f"{signal['symbol']}_{int(time.time())}",
            'symbol':        signal['symbol'],
            'action':        signal['action'],
            'strategy':      signal.get('strategy', ''),
            'entry_price':   price,
            'entry_time':    datetime.now().isoformat(),
            'size_usd':      size_usd,
            'current_price': price,
            'pnl_pct':       0.0,
            'pnl_usd':       0.0,
            'peak_pnl_pct':  0.0,
            'dynamic_sl_pct': -SL_PCT * 100,
            'status':        'open',
            'demo':          demo,
            'filter_score':  signal.get('filter_score', 0),
            'reason':        signal.get('reason', ''),
        }
        self.positions.append(pos)
        return pos

    def update_price(self, symbol, price, trail_state):
        for p in self.positions:
            if p['symbol'] == symbol and p['status'] == 'open':
                entry = p['entry_price']
                pnl_pct = ((price - entry) / entry * 100 if p['action'] == 'LONG'
                           else (entry - price) / entry * 100)
                p['current_price'] = price
                p['pnl_pct']       = round(pnl_pct, 4)
                p['pnl_usd']       = round(pnl_pct / 100 * p['size_usd'], 4)
                if pnl_pct > p['peak_pnl_pct']:
                    p['peak_pnl_pct'] = round(pnl_pct, 4)
                ts = trail_state.get(symbol, {})
                if ts:
                    p['dynamic_sl_pct'] = round(ts.get('dynamic_sl', -SL_PCT) * 100, 3)

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


class Journal:
    def __init__(self):
        self.trades = []
        self._load()

    def _load(self):
        if os.path.exists(RESULTS_FILE):
            try:
                with open(RESULTS_FILE) as f:
                    self.trades = json.load(f).get('trades', [])
            except Exception:
                pass

    def record(self, pos):
        self.trades.append({
            'id':           pos.get('id'),
            'symbol':       pos['symbol'],
            'action':       pos['action'],
            'strategy':     pos['strategy'],
            'entry_price':  pos['entry_price'],
            'exit_price':   pos['current_price'],
            'pnl_pct':      pos['pnl_pct'],
            'pnl_usd':      pos['pnl_usd'],
            'outcome':      pos.get('outcome', 'UNKNOWN'),
            'close_reason': pos.get('close_reason', ''),
            'entry_time':   pos.get('entry_time'),
            'close_time':   pos.get('close_time'),
            'filter_score': pos.get('filter_score', 0),
            'demo':         pos.get('demo', True),
        })
        self._save()

    def _save(self):
        wins   = [t for t in self.trades if t.get('outcome') == 'WIN']
        losses = [t for t in self.trades if t.get('outcome') == 'LOSS']
        total  = len(wins) + len(losses)
        with open(RESULTS_FILE, 'w') as f:
            json.dump({
                'updated': datetime.now().isoformat(),
                'summary': {
                    'total_trades': total,
                    'wins': len(wins), 'losses': len(losses),
                    'win_rate': round(len(wins)/total*100, 1) if total else 0,
                    'total_pnl_usd': round(sum(t.get('pnl_usd',0) for t in self.trades), 4),
                },
                'trades': self.trades[-500:],
            }, f, indent=2, default=str)

    def summary(self):
        wins   = [t for t in self.trades if t.get('outcome') == 'WIN']
        losses = [t for t in self.trades if t.get('outcome') == 'LOSS']
        total  = len(wins) + len(losses)
        return {
            'total': total, 'wins': len(wins), 'losses': len(losses),
            'wr':    round(len(wins)/total*100, 1) if total else 0,
            'pnl':   round(sum(t.get('pnl_usd',0) for t in self.trades), 2),
        }


class Trader:
    def __init__(self):
        self.client    = Client(os.getenv('MAIN_API_KEY'), os.getenv('MAIN_API_SECRET'))
        self.trail     = TrailingStop(sl_pct=SL_PCT)
        self.tracker   = PositionTracker()
        self.journal   = Journal()
        self.real_trades_today = 0
        self.last_reset_date   = None
        self._seen_signals     = set()  # track which signals were already processed

    def _reset_daily(self):
        today = datetime.now().date()
        if self.last_reset_date != today:
            self.real_trades_today = 0
            self.last_reset_date   = today

    def _price(self, symbol):
        try:
            return float(self.client.get_symbol_ticker(symbol=symbol)['price'])
        except Exception:
            try:
                return float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
            except Exception:
                return 0.0

    def _futures_precision(self, symbol):
        try:
            for s in self.client.futures_exchange_info()['symbols']:
                if s['symbol'] == symbol:
                    return int(s.get('quantityPrecision', 3))
        except Exception:
            pass
        return 3

    # ── KEY FIX: fetch signals from Filter HTTP API ───────────────────────────
    def _fetch_signals(self):
        if not FILTER_URL:
            log.warning("FILTER_URL not set — cannot fetch signals")
            return [], {}
        try:
            r = requests.get(f"{FILTER_URL}/api/state", timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data.get('signals', []), data
        except Exception as e:
            log.warning(f"Filter fetch failed: {e}")
        return [], {}

    def _execute_real(self, signal, price, size_usd):
        symbol = signal['symbol']
        action = signal['action']
        try:
            fi = self.client.futures_exchange_info()
            on_futures = any(s['symbol'] == symbol for s in fi['symbols'])
        except Exception:
            on_futures = False
        prec = self._futures_precision(symbol)
        qty  = round(size_usd / price, prec)
        if action == 'LONG' and not on_futures:
            try:
                self.client.create_order(
                    symbol=symbol, side='BUY', type='MARKET',
                    quoteOrderQty=round(size_usd, 2))
                return True
            except Exception as e:
                log.error(f"SPOT BUY {symbol}: {e}"); return False
        if action in ('LONG', 'FUNDING_SHORT') and on_futures:
            side = 'BUY' if action == 'LONG' else 'SELL'
            try:
                self.client.futures_create_order(
                    symbol=symbol, side=side, type='MARKET', quantity=qty)
                return True
            except Exception as e:
                log.error(f"FUTURES {side} {symbol}: {e}"); return False
        if action == 'SHORT' and on_futures:
            try:
                self.client.futures_create_order(
                    symbol=symbol, side='SELL', type='MARKET', quantity=qty)
                return True
            except Exception as e:
                log.error(f"FUTURES SHORT {symbol}: {e}"); return False
        return False

    def _process_signals(self):
        signals, filter_data = self._fetch_signals()
        if not signals:
            log.info("No signals from filter this cycle")
            return

        open_count = len(self.tracker.open_positions())
        if open_count >= MAX_OPEN_POS:
            log.info(f"Max positions ({open_count}/{MAX_OPEN_POS})")
            return

        self._reset_daily()
        open_syms = {p['symbol'] for p in self.tracker.open_positions()}

        for signal in signals:
            if len(self.tracker.open_positions()) >= MAX_OPEN_POS:
                break

            symbol   = signal.get('symbol', '')
            action   = signal.get('action', '')
            score    = signal.get('filter_score', 0)

            if not symbol or not action:
                continue
            if symbol in open_syms:
                continue

            # Dedup: don't re-enter same signal within 1 hour
            sig_key = f"{symbol}:{action}:{score}"
            if sig_key in self._seen_signals:
                continue

            if not DEMO_MODE and self.real_trades_today >= MAX_REAL_PER_DAY:
                break

            price = self._price(symbol)
            if price <= 0:
                log.warning(f"No price for {symbol}"); continue

            size_usd = min(MAX_POS_USD, 20.0)
            log.info(f"\n{'='*55}")
            log.info(f"{'🟡 DEMO' if DEMO_MODE else '🔴 REAL'}: {action} {symbol}")
            log.info(f"  Score: {score}/100 | Price: ${price:.6f} | Size: ${size_usd:.2f}")
            log.info(f"  {signal.get('reason','')[:80]}")

            executed = True
            if not DEMO_MODE:
                executed = self._execute_real(signal, price, size_usd)
                if executed:
                    self.real_trades_today += 1

            if executed:
                pos = self.tracker.open(signal, price, size_usd, DEMO_MODE)
                self.trail.register(symbol, price, action)
                self._seen_signals.add(sig_key)
                # Keep set from growing unbounded
                if len(self._seen_signals) > 200:
                    self._seen_signals = set(list(self._seen_signals)[-100:])
                log.info(f"  ✅ Position opened: {pos['id']}")

    def _monitor(self):
        positions = self.tracker.open_positions()
        if not positions:
            return
        log.info(f"\n🛡️  Monitoring {len(positions)} position(s)...")
        total_pnl = 0
        for pos in positions:
            symbol = pos['symbol']
            price  = self._price(symbol)
            if price <= 0:
                continue
            self.tracker.update_price(symbol, price, self.trail.state)
            pnl = pos['pnl_pct']
            total_pnl += pos.get('pnl_usd', 0)
            log.info(f"  {pos['action']:5s} {symbol:12s} "
                     f"${pos['entry_price']:.6f}→${price:.6f} "
                     f"PnL={pnl:+.2f}% Peak={pos['peak_pnl_pct']:+.2f}%")
            should_close, reason = self.trail.update(symbol, price)
            if should_close:
                log.info(f"\n  🎯 CLOSING {symbol}: {reason}")
                for c in self.tracker.close(symbol, reason):
                    emoji = '✅' if c['outcome'] == 'WIN' else '❌'
                    log.info(f"  {emoji} {c['outcome']} {symbol} "
                             f"PnL={c['pnl_pct']:+.2f}% ${c['pnl_usd']:+.4f}")
                    self.journal.record(c)
                    if not DEMO_MODE:
                                        self._close_real(symbol, pos['action'])
        sm = self.journal.summary()
        log.info(f"  💎 Unrealized: ${total_pnl:+.4f} | "
                 f"{sm['wins']}W/{sm['losses']}L WR={sm['wr']}% PnL=${sm['pnl']:+.2f}")

    def _write_state(self):
        sm = self.journal.summary()
        state = {
            'timestamp':       datetime.now().isoformat(),
            'demo_mode':       DEMO_MODE,
            'open_positions':  [
                {k: v for k, v in p.items() if k != 'demo'}
                for p in self.tracker.open_positions()
            ],
            'recent_closed':   [
                {k: v for k, v in p.items() if k != 'demo'}
                for p in self.tracker.closed_positions()[-20:]
            ],
            'summary':         sm,
            'real_trades_today': self.real_trades_today,
            'max_real_per_day':  MAX_REAL_PER_DAY,
        }
        with open(TRADER_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)

    def _monitor_loop(self):
        while True:
            try:
                self._monitor()
                self._write_state()
            except Exception as e:
                log.error(f"Monitor error: {e}")
            time.sleep(CHECK_INTERVAL)


    def _close_real(self, symbol, side):
        """Close real futures position."""
        try:
            prec = self._futures_precision(symbol)
            for p in self.client.futures_account()['positions']:
                if p['symbol'] == symbol:
                    amt = float(p['positionAmt'])
                    if abs(amt) > 0:
                        close_side = 'SELL' if amt > 0 else 'BUY'
                        self.client.futures_create_order(
                            symbol=symbol, side=close_side,
                            type='MARKET', quantity=round(abs(amt), prec),
                            reduceOnly=True)
                        log.info(f"  ✅ Closed real {symbol}")
                        return True
        except Exception as e:
            log.error(f"  ❌ Close real {symbol}: {e}")
        return False

    def start(self):
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        log.info("✅ Position monitor started (every 30s)")
        while True:
            try:
                self._process_signals()
                self._write_state()
            except Exception as e:
                log.error(f"Signal loop error: {e}")
            time.sleep(SIGNAL_INTERVAL)



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
  <tr><th>Symbol</th><th>Action</th><th>Entry</th><th>Current</th><th>P&L%</th><th>Peak%</th><th>Trail SL</th><th>Strategy</th></tr>
  {% for p in state.open_positions %}
  <tr>
    <td>{{ p.symbol }}</td>
    <td>{{ p.action }}</td>
    <td>${{ '%.6f'|format(p.entry_price) }}</td>
    <td>${{ '%.6f'|format(p.current_price) }}</td>
    <td class="{{ 'win' if p.pnl_pct > 0 else 'loss' }}">{{ '%+.2f'|format(p.pnl_pct) }}%</td>
    <td class="win">{{ '%+.2f'|format(p.peak_pnl_pct) }}%</td>
    <td>{{ p.dynamic_sl_pct }}%</td>
    <td>{{ p.strategy }}</td>
  </tr>
  {% endfor %}
</table>
{% endif %}
{% if state.recent_closed %}
<h3 style="color:#a78bfa;margin-top:24px">Recent Closed</h3>
<table>
  <tr><th>Symbol</th><th>Action</th><th>P&L%</th><th>P&L$</th><th>Outcome</th><th>Strategy</th><th>Reason</th></tr>
  {% for p in state.recent_closed[-10:]|reverse %}
  <tr>
    <td>{{ p.symbol }}</td>
    <td>{{ p.action }}</td>
    <td class="{{ 'win' if p.pnl_pct > 0 else 'loss' }}">{{ '%+.2f'|format(p.pnl_pct) }}%</td>
    <td class="{{ 'win' if p.pnl_usd > 0 else 'loss' }}">${{ '%+.4f'|format(p.pnl_usd) }}</td>
    <td class="{{ 'win' if p.outcome == 'WIN' else 'loss' }}">{{ p.outcome }}</td>
    <td>{{ p.strategy }}</td>
    <td style="color:#6b7280;font-size:10px">{{ p.close_reason[:50] }}</td>
  </tr>
  {% endfor %}
</table>
{% endif %}
</body>
</html>"""

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route('/api/state')
def api_state():
    if os.path.exists(TRADER_FILE):
        with open(TRADER_FILE) as f:
            return jsonify(json.load(f))
    return jsonify({})

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/')
def dashboard():
    if os.path.exists(TRADER_FILE):
        with open(TRADER_FILE) as f:
            state = json.load(f)
    else:
        state = {'timestamp': '-', 'demo_mode': True, 'open_positions': [],
                 'recent_closed': [], 'summary': {'total':0,'wins':0,'losses':0,'wr':0,'pnl':0},
                 'real_trades_today': 0, 'max_real_per_day': 15}
    return render_template_string(DASHBOARD_HTML, state=type('S', (), state)())


if __name__ == '__main__':
    required = ['MAIN_API_KEY', 'MAIN_API_SECRET']
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing: {missing}"); sys.exit(1)

    threading.Thread(
        target=lambda: app.run(host='0.0.0.0',
                               port=int(os.getenv('PORT', 8080)),
                               debug=False, use_reloader=False),
        daemon=True
    ).start()
    log.info("✅ Trader API on port 8080")
    Trader().start()
