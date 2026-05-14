"""
╔══════════════════════════════════════════════════════════════════╗
║           WARFARE FILTER — SERVICE 2 of 3                       ║
║  Takes scanner opportunities and brutally filters them.          ║
║  Only the best 1-3 per cycle make it through to the trader.      ║
╚══════════════════════════════════════════════════════════════════╝

What this does every 90 seconds:
  1. Reads /app/data/scanner_state.json (written by Service 1)
  2. For each opportunity, applies 6 confirmation layers:
       Layer 1: Market regime alignment (BTC direction)
       Layer 2: Multi-timeframe confluence (1h + 4h agree?)
       Layer 3: Order book depth analysis (whale activity)
       Layer 4: Buy/sell pressure (who controls the tape?)
       Layer 5: Historical pattern matching (does this setup win?)
       Layer 6: Risk/reward calculation (is it worth taking?)
  3. Scores each opportunity 0-100 across all layers
  4. Forwards only signals scoring 78+ to the trader
  5. Writes to /app/data/filtered_signals.json

Railway Variables needed:
  MAIN_API_KEY, MAIN_API_SECRET  (for additional data fetching)
"""

import os, sys, time, json, logging, math
import requests
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from binance.client import Client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [FILTER] %(message)s'
)
log = logging.getLogger('filter')

SCANNER_FILE  = '/app/data/scanner_state.json'
SIGNALS_FILE  = '/app/data/filtered_signals.json'
RESULTS_FILE  = '/app/data/trade_results.json'   # written by trader
FILTER_INTERVAL = 90   # seconds
MIN_SCORE_TO_PASS = 78
MAX_SIGNALS_PER_CYCLE = 3

os.makedirs('/app/data', exist_ok=True)


# ═════════════════════════════════════════════════════════════════════════════
# PERFORMANCE TRACKER — learns from past trades
# ═════════════════════════════════════════════════════════════════════════════

class PerformanceTracker:
    """
    Reads trade results from Service 3 and builds a picture of:
    - Which strategies are actually winning
    - Which market conditions lead to wins
    - Strategy-level win rates (used to weight signals)
    """

    def __init__(self):
        self.results   = []
        self.stats     = {}
        self._load()

    def _load(self):
        if os.path.exists(RESULTS_FILE):
            try:
                with open(RESULTS_FILE) as f:
                    data = json.load(f)
                self.results = data.get('trades', [])
                self._compute_stats()
            except Exception:
                pass

    def _compute_stats(self):
        """Compute win rate per strategy."""
        by_strategy = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total_pnl': 0.0})
        for t in self.results:
            s = t.get('strategy', 'Unknown')
            if t.get('outcome') == 'WIN':
                by_strategy[s]['wins']      += 1
                by_strategy[s]['total_pnl'] += t.get('pnl_pct', 0)
            elif t.get('outcome') == 'LOSS':
                by_strategy[s]['losses']     += 1
                by_strategy[s]['total_pnl'] += t.get('pnl_pct', 0)

        self.stats = {}
        for strat, d in by_strategy.items():
            total = d['wins'] + d['losses']
            if total >= 3:
                self.stats[strat] = {
                    'win_rate':  round(d['wins'] / total, 3),
                    'total':     total,
                    'total_pnl': round(d['total_pnl'], 2),
                }

    def get_strategy_multiplier(self, strategy):
        """
        Returns a score multiplier based on historical performance:
          Win rate > 65%: multiplier 1.3  (boost proven strategies)
          Win rate 50-65%: multiplier 1.0 (neutral)
          Win rate < 40%: multiplier 0.7  (penalize losing strategies)
          No data: multiplier 1.0
        """
        s = self.stats.get(strategy)
        if not s or s['total'] < 5:
            return 1.0
        wr = s['win_rate']
        if wr >= 0.65:   return 1.3
        elif wr >= 0.55: return 1.1
        elif wr >= 0.45: return 1.0
        elif wr >= 0.35: return 0.85
        else:            return 0.70

    def reload(self):
        self._load()


# ═════════════════════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME CHECKER
# ═════════════════════════════════════════════════════════════════════════════

class MultiTimeframeChecker:
    """
    Check if the 4h trend agrees with the 1h signal.
    A LONG signal on 1h is much stronger if 4h is also bullish.
    """

    def __init__(self, client):
        self.client   = client
        self._cache   = {}
        self._cache_ts = {}

    def get_4h_bias(self, symbol):
        """
        Returns: 'BULL', 'BEAR', or 'NEUTRAL'
        Based on 4h candles: EMA20 vs EMA50 + RSI
        """
        now = time.time()
        if symbol in self._cache and now - self._cache_ts.get(symbol, 0) < 900:
            return self._cache[symbol]

        try:
            klines = self.client.get_klines(symbol=symbol, interval='4h', limit=60)
            closes = [float(k[4]) for k in klines]

            if len(closes) < 20:
                return 'NEUTRAL'

            ema20 = _ema(closes, 20)
            ema50 = _ema(closes, 50) if len(closes) >= 50 else _ema(closes, len(closes))
            rsi   = _rsi(closes)
            price = closes[-1]

            if price > ema20 > ema50 and rsi > 52:
                bias = 'BULL'
            elif price < ema20 < ema50 and rsi < 48:
                bias = 'BEAR'
            else:
                bias = 'NEUTRAL'

            self._cache[symbol]    = bias
            self._cache_ts[symbol] = now
            return bias

        except Exception:
            return 'NEUTRAL'


# ═════════════════════════════════════════════════════════════════════════════
# SUPPORT/RESISTANCE DETECTOR
# ═════════════════════════════════════════════════════════════════════════════

class SupportResistance:
    """
    Find key price levels using swing highs/lows.
    A LONG near strong support has better risk/reward.
    A SHORT near strong resistance has better risk/reward.
    """

    def __init__(self, client):
        self.client = client
        self._cache = {}
        self._ts    = {}

    def get_levels(self, symbol):
        """Returns {'support': [...], 'resistance': [...]}"""
        now = time.time()
        if symbol in self._cache and now - self._ts.get(symbol, 0) < 3600:
            return self._cache[symbol]

        try:
            klines = self.client.get_klines(symbol=symbol, interval='1h', limit=100)
            highs  = [float(k[2]) for k in klines]
            lows   = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]

            if len(closes) < 20:
                return {'support': [], 'resistance': []}

            # Find swing highs and lows (local extremes over ±3 candles)
            resistance = []
            support    = []
            for i in range(3, len(highs) - 3):
                if highs[i] == max(highs[i-3:i+4]):
                    resistance.append(highs[i])
                if lows[i]  == min(lows[i-3:i+4]):
                    support.append(lows[i])

            # Cluster nearby levels (within 0.5%)
            resistance = _cluster_levels(resistance)
            support    = _cluster_levels(support)

            result = {
                'support':    sorted(support,    reverse=True)[:5],
                'resistance': sorted(resistance, reverse=False)[:5],
                'current':    closes[-1],
            }
            self._cache[symbol] = result
            self._ts[symbol]    = now
            return result

        except Exception:
            return {'support': [], 'resistance': []}

    def score_proximity(self, symbol, action, current_price):
        """
        Returns a score bonus/penalty based on proximity to S/R levels.
        LONG near support: +15 (buying at a good level)
        LONG near resistance: -10 (buying into a wall)
        SHORT near resistance: +15
        SHORT near support: -10
        """
        levels = self.get_levels(symbol)
        if not levels: return 0

        best_support    = max(levels['support'],    default=0)
        best_resistance = min(levels['resistance'], default=float('inf'))

        if current_price <= 0: return 0

        dist_support    = (current_price - best_support)    / current_price if best_support    else 1
        dist_resistance = (best_resistance - current_price) / current_price if best_resistance else 1

        if action == 'LONG':
            if 0 < dist_support < 0.02:    return 15   # within 2% of support
            elif 0 < dist_support < 0.05:  return 8
            if dist_resistance < 0.03:     return -10  # 3% from resistance wall
        elif action == 'SHORT':
            if 0 < dist_resistance < 0.02: return 15
            elif 0 < dist_resistance < 0.05: return 8
            if dist_support < 0.03:        return -10

        return 0


# ═════════════════════════════════════════════════════════════════════════════
# RISK/REWARD CALCULATOR
# ═════════════════════════════════════════════════════════════════════════════

def calculate_rr(opportunity, levels, action):
    """
    Calculate expected Risk:Reward ratio.
    We use ATR-based stops and support/resistance for targets.

    R:R >= 2.0: very good (+15 score)
    R:R >= 1.5: good      (+8 score)
    R:R < 1.0:  bad       (-15 score)
    """
    price = opportunity.get('price', 0)
    if price <= 0: return 0, 1.0

    sl_pct = 0.015   # 1.5% stop loss (from SL setting)

    support    = max(levels.get('support',    []), default=price * 0.95)
    resistance = min(levels.get('resistance', []), default=price * 1.10)

    if action == 'LONG':
        stop   = max(price * (1 - sl_pct), support * 0.99)
        target = min(resistance * 0.99, price * 1.10)   # target: next resistance or +10%
        risk   = price - stop
        reward = target - price
    else:  # SHORT
        stop   = min(price * (1 + sl_pct), resistance * 1.01)
        target = max(support * 1.01, price * 0.90)
        risk   = stop - price
        reward = price - target

    rr = round(reward / risk, 2) if risk > 0 else 1.0

    if rr >= 2.5:   score_bonus = 20
    elif rr >= 2.0: score_bonus = 15
    elif rr >= 1.5: score_bonus = 8
    elif rr >= 1.0: score_bonus = 0
    else:           score_bonus = -15

    return score_bonus, rr


# ═════════════════════════════════════════════════════════════════════════════
# DUPLICATE GUARD — prevent trading same coin twice
# ═════════════════════════════════════════════════════════════════════════════

class DuplicateGuard:
    """
    Track recently signaled coins to prevent:
    - Same coin being traded twice within 1 hour
    - Conflicting LONG + SHORT signals for same coin
    """

    def __init__(self):
        self._seen = {}   # symbol → {'action': ..., 'ts': datetime}

    def is_allowed(self, symbol, action):
        if symbol not in self._seen:
            return True
        last = self._seen[symbol]
        elapsed = (datetime.now() - last['ts']).seconds / 60
        if elapsed > 60:   # Reset after 1 hour
            del self._seen[symbol]
            return True
        if last['action'] != action:
            return False   # Conflict — skip
        return False        # Same coin again — wait

    def mark(self, symbol, action):
        self._seen[symbol] = {'action': action, 'ts': datetime.now()}

    def cleanup(self):
        cutoff = datetime.now() - timedelta(hours=2)
        self._seen = {k: v for k, v in self._seen.items() if v['ts'] > cutoff}


# ═════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _ema(prices, period):
    if not prices or len(prices) < period: return prices[-1] if prices else 0
    k = 2 / (period + 1)
    e = prices[0]
    for v in prices[1:]: e = v * k + e * (1 - k)
    return e

def _rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    d  = np.diff(np.array(prices[-period*2:], dtype=float))
    up = np.where(d > 0, d, 0)
    dn = np.where(d < 0, -d, 0)
    au = np.mean(up[:period]); ad = np.mean(dn[:period])
    for i in range(period, len(up)):
        au = (au*(period-1)+up[i])/period; ad = (ad*(period-1)+dn[i])/period
    return round(100 - 100/(1+au/ad), 1) if ad > 0 else 100.0

def _cluster_levels(levels, threshold=0.005):
    """Group nearby price levels together (within 0.5%)."""
    if not levels: return []
    levels = sorted(levels)
    clusters = [[levels[0]]]
    for l in levels[1:]:
        if abs(l - clusters[-1][-1]) / clusters[-1][-1] < threshold:
            clusters[-1].append(l)
        else:
            clusters.append([l])
    return [round(np.mean(c), 8) for c in clusters]


# ═════════════════════════════════════════════════════════════════════════════
# MAIN FILTER
# ═════════════════════════════════════════════════════════════════════════════

class Filter:

    def __init__(self):
        self.client  = Client(
            os.getenv('MAIN_API_KEY'),
            os.getenv('MAIN_API_SECRET')
        )
        self.perf    = PerformanceTracker()
        self.mtf     = MultiTimeframeChecker(self.client)
        self.sr      = SupportResistance(self.client)
        self.dedup   = DuplicateGuard()
        log.info("Filter initialized")

    def _score_opportunity(self, opp, scanner_state):
        """
        Apply all 6 layers of filtering and return final score.
        Returns (final_score, breakdown_dict)
        """
        symbol   = opp.get('symbol', '')
        action   = opp.get('action', '')
        strategy = opp.get('strategy', '')
        price    = opp.get('price', 0)
        base     = opp.get('score', 50)   # Scanner's initial score

        breakdown = {'base': base}
        score = base

        # ── Layer 1: Market Regime ────────────────────────────────────────
        regime = scanner_state.get('regime', 'NEUTRAL')
        if regime == 'BULL' and action == 'LONG':
            score += 10; breakdown['regime'] = '+10 BULL+LONG'
        elif regime == 'BEAR' and action == 'SHORT':
            score += 10; breakdown['regime'] = '+10 BEAR+SHORT'
        elif regime == 'BULL' and action == 'SHORT':
            score -= 20; breakdown['regime'] = '-20 shorting in BULL'
        elif regime == 'BEAR' and action == 'LONG':
            score -= 15; breakdown['regime'] = '-15 longing in BEAR'
            if strategy != 'Volume Spike Reversal':
                score -= 5  # Extra penalty for non-reversal longs in bear
        else:
            breakdown['regime'] = '0 NEUTRAL'

        # Funding rate collection is regime-agnostic
        if action == 'FUNDING_SHORT':
            score = base  # Reset to base for funding
            breakdown['regime'] = '0 funding (regime-neutral)'

        # ── Layer 2: Multi-Timeframe Confluence ───────────────────────────
        if action not in ('FUNDING_SHORT',):
            bias_4h = self.mtf.get_4h_bias(symbol)
            if action == 'LONG' and bias_4h == 'BULL':
                score += 15; breakdown['mtf'] = '+15 4h BULL confirms LONG'
            elif action == 'SHORT' and bias_4h == 'BEAR':
                score += 15; breakdown['mtf'] = '+15 4h BEAR confirms SHORT'
            elif bias_4h == 'NEUTRAL':
                breakdown['mtf'] = '0 4h neutral'
            else:
                score -= 12; breakdown['mtf'] = f'-12 4h {bias_4h} conflicts {action}'
        else:
            breakdown['mtf'] = '0 N/A (funding)'

        # ── Layer 3: Order Book / Whale Activity ──────────────────────────
        ta = scanner_state.get('ta_data', {}).get(symbol, {})
        ob = ta.get('ob_imbalance', 0)
        if action == 'LONG':
            if ob > 0.25:
                score += 15; breakdown['orderbook'] = f'+15 heavy buyers ({ob:+.2f})'
            elif ob > 0.10:
                score += 8;  breakdown['orderbook'] = f'+8 buyers ({ob:+.2f})'
            elif ob < -0.15:
                score -= 12; breakdown['orderbook'] = f'-12 sellers dominating ({ob:+.2f})'
            else:
                breakdown['orderbook'] = f'0 balanced ({ob:+.2f})'
        elif action == 'SHORT':
            if ob < -0.25:
                score += 15; breakdown['orderbook'] = f'+15 heavy sellers ({ob:+.2f})'
            elif ob < -0.10:
                score += 8;  breakdown['orderbook'] = f'+8 sellers ({ob:+.2f})'
            elif ob > 0.15:
                score -= 12; breakdown['orderbook'] = f'-12 buyers dominating ({ob:+.2f})'
            else:
                breakdown['orderbook'] = f'0 balanced ({ob:+.2f})'
        else:
            breakdown['orderbook'] = '0 N/A'

        # ── Layer 4: Buy/Sell Pressure (tape reading) ─────────────────────
        buy_pressure = ta.get('buy_pressure', 0.5)
        if action == 'LONG':
            if buy_pressure >= 0.65:
                score += 10; breakdown['tape'] = f'+10 buy pressure {buy_pressure:.0%}'
            elif buy_pressure <= 0.35:
                score -= 8;  breakdown['tape'] = f'-8 sell pressure {buy_pressure:.0%}'
            else:
                breakdown['tape'] = '0 balanced'
        elif action == 'SHORT':
            if buy_pressure <= 0.35:
                score += 10; breakdown['tape'] = f'+10 sell pressure {1-buy_pressure:.0%}'
            elif buy_pressure >= 0.65:
                score -= 8;  breakdown['tape'] = f'-8 buy pressure {buy_pressure:.0%}'
            else:
                breakdown['tape'] = '0 balanced'
        else:
            breakdown['tape'] = '0 N/A'

        # ── Layer 5: Historical Performance Multiplier ────────────────────
        multiplier = self.perf.get_strategy_multiplier(strategy)
        adjusted   = round(score * multiplier)
        breakdown['perf_mult'] = f'×{multiplier} ({strategy})'
        score = adjusted

        # ── Layer 6: Support/Resistance + Risk/Reward ─────────────────────
        sr_levels = self.sr.get_levels(symbol)
        sr_bonus  = self.sr.score_proximity(symbol, action, price)
        score    += sr_bonus
        breakdown['sr'] = f'{sr_bonus:+d} S/R proximity'

        rr_bonus, rr = calculate_rr(opp, sr_levels, action)
        score += rr_bonus
        breakdown['rr'] = f'{rr_bonus:+d} R:R={rr:.1f}'

        # ── Penalty: fear/greed extremes ──────────────────────────────────
        fg = scanner_state.get('fear_greed', 50)
        if fg < 20 and action == 'SHORT':
            score -= 10; breakdown['fg'] = f'-10 extreme fear {fg} (short exhausted)'
        elif fg > 80 and action == 'LONG':
            score -= 10; breakdown['fg'] = f'-10 extreme greed {fg} (long exhausted)'
        else:
            breakdown['fg'] = f'0 FG={fg}'

        final = max(0, min(100, score))
        return final, breakdown, rr

    def _filter_once(self):
        """Read scanner state, score everything, write top signals."""

        # Check scanner file is fresh (< 5 minutes old)
        if not os.path.exists(SCANNER_FILE):
            log.warning("Scanner state not found — waiting for Service 1")
            return

        stat = os.stat(SCANNER_FILE)
        age  = time.time() - stat.st_mtime
        if age > 300:
            log.warning(f"Scanner state is {age:.0f}s old — Service 1 may be down")

        with open(SCANNER_FILE) as f:
            scanner_state = json.load(f)

        opportunities = scanner_state.get('opportunities', [])
        if not opportunities:
            log.info("No opportunities from scanner this cycle")
            self._write_signals([], scanner_state)
            return

        self.perf.reload()
        self.dedup.cleanup()

        log.info(f"Filtering {len(opportunities)} opportunities...")

        scored = []
        for opp in opportunities:
            symbol = opp.get('symbol', '')
            action = opp.get('action', '')

            if not symbol or not action: continue

            # Duplicate check
            if not self.dedup.is_allowed(symbol, action):
                log.info(f"  ⏭ {symbol} skipped (dedup)")
                continue

            try:
                final_score, breakdown, rr = self._score_opportunity(
                    opp, scanner_state)
            except Exception as e:
                log.warning(f"Scoring error for {symbol}: {e}")
                continue

            result = dict(opp)
            result.update({
                'filter_score':  final_score,
                'rr_ratio':      rr,
                'breakdown':     breakdown,
                'filter_ts':     datetime.now().isoformat(),
            })
            scored.append(result)

        # Sort by filter score
        scored.sort(key=lambda x: x['filter_score'], reverse=True)

        # Only pass the top N that score >= threshold
        passed = [s for s in scored if s['filter_score'] >= MIN_SCORE_TO_PASS]
        passed = passed[:MAX_SIGNALS_PER_CYCLE]

        # Mark as sent
        for p in passed:
            self.dedup.mark(p['symbol'], p['action'])

        log.info(f"Filter result: {len(scored)} scored, {len(passed)} passed")
        for p in passed:
            log.info(f"  ✅ PASS {p['action']:5s} {p['symbol']:12s} "
                     f"score={p['filter_score']:3d} R:R={p['rr_ratio']:.1f} "
                     f"← {p['reason'][:50]}")
        for s in scored[:5]:
            if s not in passed:
                log.info(f"  ❌ FAIL {s['action']:5s} {s['symbol']:12s} "
                         f"score={s['filter_score']:3d} "
                         f"({list(s['breakdown'].items())[-1]})")

        self._write_signals(passed, scanner_state)

    def _write_signals(self, signals, scanner_state):
        state = {
            'timestamp':       datetime.now().isoformat(),
            'regime':          scanner_state.get('regime', 'NEUTRAL'),
            'fear_greed':      scanner_state.get('fear_greed', 50),
            'signals':         signals,
            'total_evaluated': len(scanner_state.get('opportunities', [])),
            'total_passed':    len(signals),
        }
        with open(SIGNALS_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)

    def _loop(self):
        while True:
            try:
                self._filter_once()
            except Exception as e:
                log.error(f"Filter error: {e}")
                import traceback; traceback.print_exc()
            time.sleep(FILTER_INTERVAL)


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    log.info("=" * 60)
    log.info("WARFARE FILTER — SERVICE 2")
    log.info("=" * 60)

    required = ['MAIN_API_KEY', 'MAIN_API_SECRET']
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing env vars: {missing}")
        sys.exit(1)

    Filter()._loop()
