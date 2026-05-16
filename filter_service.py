# Copy of warfare_filter with two fixes:
# 1. MIN_SCORE lowered from 78 → 68 (was blocking everything in bearish market)
# 2. MTF bias penalty reduced from -12 → -6 (was too harsh)
# 3. Added logging of why signals are rejected

import os, sys, time, json, logging, threading
import requests, numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from binance.client import Client
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format='%(asctime)s [FILTER] %(message)s')
log = logging.getLogger('filter')

PORT            = int(os.getenv('PORT', 8080))
SCANNER_URL     = os.getenv('SCANNER_URL', '')
FILTER_INTERVAL = 90
MIN_SCORE       = 68   # ← FIXED: was 78, too high for bear markets
MAX_SIGNALS     = 3

log.info("=" * 55)
log.info("WARFARE FILTER — Fixed")
log.info(f"  SCANNER_URL: {SCANNER_URL or 'NOT SET ❌'}")
log.info(f"  MIN_SCORE:   {MIN_SCORE} (was 78)")
log.info(f"  PORT:        {PORT}")
log.info("=" * 55)

STATE = {'timestamp': None, 'signals': [], 'total_passed': 0, 'total_evaluated': 0}

def _ema(p, n):
    if not p: return 0
    k = 2/(n+1); e = float(p[0])
    for v in p[1:]: e = float(v)*k + e*(1-k)
    return e

def _rsi(p, n=14):
    if len(p) < n+1: return 50.
    d = np.diff(np.array(p[-(n*3):], dtype=float))
    u = np.where(d > 0, d, 0); v = np.where(d < 0, -d, 0)
    au = np.mean(u[:n]); ad = np.mean(v[:n])
    for i in range(n, len(u)):
        au = (au*(n-1)+u[i])/n; ad = (ad*(n-1)+v[i])/n
    return round(100-100/(1+au/ad), 2) if ad > 0 else 100.

class MTF:
    def __init__(self, c): self.c = c; self._cache = {}; self._ts = {}
    def bias(self, sym):
        now = time.time()
        if sym in self._cache and now - self._ts.get(sym, 0) < 900:
            return self._cache[sym]
        try:
            kl = self.c.get_klines(symbol=sym, interval='4h', limit=60)
            cl = [float(k[4]) for k in kl]
            if len(cl) < 20: return 'NEUTRAL'
            e20 = _ema(cl, 20); e50 = _ema(cl, min(50, len(cl)))
            rv = _rsi(cl); pr = cl[-1]
            if pr > e20 > e50 and rv > 52: b = 'BULL'
            elif pr < e20 < e50 and rv < 48: b = 'BEAR'
            else: b = 'NEUTRAL'
            self._cache[sym] = b; self._ts[sym] = now
            return b
        except: return 'NEUTRAL'

class Dedup:
    def __init__(self): self._s = {}
    def ok(self, sym, action):
        if sym not in self._s: return True
        if (datetime.now() - self._s[sym]['ts']).seconds > 3600:
            del self._s[sym]; return True
        return False
    def mark(self, sym, action): self._s[sym] = {'action': action, 'ts': datetime.now()}
    def clean(self):
        cut = datetime.now() - timedelta(hours=2)
        self._s = {k: v for k, v in self._s.items() if v['ts'] > cut}

def score_opp(opp, scanner_data, mtf, regime):
    sym    = opp.get('symbol', '')
    action = opp.get('action', '')
    strat  = opp.get('strategy', '')
    base   = opp.get('score', 50)
    score  = base; bd = {}

    # Regime alignment
    if regime == 'BULL' and action == 'LONG':   score += 10; bd['regime'] = '+10'
    elif regime == 'BEAR' and action == 'SHORT': score += 10; bd['regime'] = '+10'
    elif regime == 'BULL' and action == 'SHORT': score -= 15; bd['regime'] = '-15'
    elif regime == 'BEAR' and action == 'LONG':
        score -= 8; bd['regime'] = '-8'   # ← FIXED: was -15/-20
        if strat == 'Volume Spike Reversal': score += 5  # allow reversals
    else: bd['regime'] = '0'
    if action == 'FUNDING_SHORT': score = base; bd['regime'] = '0 funding'

    # MTF 4h bias
    if action not in ('FUNDING_SHORT',):
        b4 = mtf.bias(sym)
        if action == 'LONG' and b4 == 'BULL':    score += 15; bd['mtf'] = '+15'
        elif action == 'SHORT' and b4 == 'BEAR':  score += 15; bd['mtf'] = '+15'
        elif b4 != 'NEUTRAL':
            score -= 6; bd['mtf'] = f'-6({b4})'  # ← FIXED: was -12
        else: bd['mtf'] = '0'

    # Order book
    ta = scanner_data.get('ta_data', {}).get(sym, {})
    ob = ta.get('ob_imbalance', 0)
    if action == 'LONG':
        if ob > 0.25:   score += 15; bd['ob'] = '+15'
        elif ob > 0.10: score += 8;  bd['ob'] = '+8'
        elif ob < -0.15: score -= 8; bd['ob'] = '-8'
        else: bd['ob'] = '0'
    elif action == 'SHORT':
        if ob < -0.25:  score += 15; bd['ob'] = '+15'
        elif ob < -0.10: score += 8; bd['ob'] = '+8'
        elif ob > 0.15: score -= 8;  bd['ob'] = '-8'
        else: bd['ob'] = '0'

    # Fear & Greed
    fg = scanner_data.get('fear_greed', 50)
    if fg < 20 and action == 'SHORT': score -= 5; bd['fg'] = '-5'
    elif fg > 80 and action == 'LONG': score -= 5; bd['fg'] = '-5'
    else: bd['fg'] = '0'

    return max(0, min(100, score)), bd

class Filter:
    def __init__(self):
        self.client = Client(os.getenv('MAIN_API_KEY'), os.getenv('MAIN_API_SECRET'))
        self.mtf    = MTF(self.client)
        self.dedup  = Dedup()

    def _fetch_scanner(self):
        if not SCANNER_URL:
            log.warning("SCANNER_URL not set"); return {}
        try:
            r = requests.get(f"{SCANNER_URL}/api/state", timeout=10)
            if r.status_code == 200: return r.json()
        except Exception as e:
            log.warning(f"Scanner fetch: {e}")
        return {}

    def run_once(self):
        global STATE
        data  = self._fetch_scanner()
        opps  = data.get('opportunities', [])
        if not opps:
            log.info("No opportunities from scanner")
            STATE = {'timestamp': datetime.now().isoformat(), 'signals': [],
                     'total_passed': 0, 'total_evaluated': 0,
                     'regime': data.get('regime', 'NEUTRAL'),
                     'fear_greed': data.get('fear_greed', 50)}
            return

        regime = data.get('regime', 'NEUTRAL')
        self.dedup.clean()
        scored = []

        for o in opps:
            sym    = o.get('symbol', '')
            action = o.get('action', '')
            if not sym or not action: continue
            if not self.dedup.ok(sym, action): continue
            try:
                fs, bd = score_opp(o, data, self.mtf, regime)
                r = dict(o)
                r.update({'filter_score': fs, 'breakdown': bd,
                          'filter_ts': datetime.now().isoformat(), 'rr_ratio': 1.5})
                scored.append(r)
            except Exception as e:
                log.warning(f"Score {sym}: {e}")

        scored.sort(key=lambda x: x['filter_score'], reverse=True)
        passed = [s for s in scored if s['filter_score'] >= MIN_SCORE][:MAX_SIGNALS]

        for p in passed:
            self.dedup.mark(p['symbol'], p['action'])

        log.info(f"Filter: {len(scored)} scored, {len(passed)} passed "
                 f"(score>={MIN_SCORE}) | Regime: {regime}")
        for p in passed:
            log.info(f"  ✅ {p['action']:5s} {p['symbol']:12s} "
                     f"score={p['filter_score']} {p.get('reason','')[:50]}")
        # Show why top rejected signals failed
        for s in scored[:8]:
            if s not in passed:
                log.info(f"  ❌ {s['action']:5s} {s['symbol']:12s} "
                         f"score={s['filter_score']} bd={s.get('breakdown',{})}")

        STATE = {'timestamp': datetime.now().isoformat(), 'signals': passed,
                 'total_passed': len(passed), 'total_evaluated': len(opps),
                 'regime': regime, 'fear_greed': data.get('fear_greed', 50)}

    def loop(self):
        while True:
            try: self.run_once()
            except Exception as e:
                log.error(f"Filter error: {e}")
                import traceback; traceback.print_exc()
            time.sleep(FILTER_INTERVAL)

flask_app = Flask('filter')

@flask_app.route('/api/state')
def api_state(): return jsonify(STATE)

@flask_app.route('/health')
def health(): return jsonify({'status': 'ok', 'min_score': MIN_SCORE})

if __name__ == '__main__':
    f = Filter()
    threading.Thread(
        target=lambda: flask_app.run(host='0.0.0.0', port=PORT,
                                     debug=False, use_reloader=False),
        daemon=True).start()
    log.info(f"✅ Filter API on port {PORT}")
    f.loop()
