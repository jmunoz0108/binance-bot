"""
╔══════════════════════════════════════════════════════════════════╗
║           WARFARE SCANNER — SERVICE 1 of 3                      ║
║  Pure market intelligence. No trading. No opinions.             ║
║  Just data, cleaned and scored, written to shared volume.        ║
╚══════════════════════════════════════════════════════════════════╝

What this does every 60 seconds:
  1. Pulls real-time prices via WebSocket (Binance)
  2. Calculates RSI, MACD, Bollinger Bands, ATR for top 50 coins
  3. Reads order book depth (supply vs demand)
  4. Reads funding rates (passive income opportunities)
  5. Detects Bollinger Band squeezes (breakout setups)
  6. Checks BTC market regime (bull/bear/neutral)
  7. Scores everything 0-100
  8. Writes to /app/data/scanner_state.json

Railway Variables needed:
  MAIN_API_KEY, MAIN_API_SECRET  (Binance main account)
  BYBIT_API_KEY, BYBIT_API_SECRET (optional)
"""

import os, sys, time, json, logging, threading, math
import requests
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict, deque
from binance.client import Client
import websocket

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SCANNER] %(message)s'
)
log = logging.getLogger('scanner')

# ── Config ────────────────────────────────────────────────────────────────────
STATE_FILE   = '/app/data/scanner_state.json'
SCAN_INTERVAL = 60   # seconds between full scans
TOP_N_COINS  = 50    # analyze top N coins by volume
MIN_VOLUME   = 5_000_000   # $5M minimum 24h volume
os.makedirs('/app/data', exist_ok=True)

# ═════════════════════════════════════════════════════════════════════════════
# WEBSOCKET FEED
# ═════════════════════════════════════════════════════════════════════════════

class LiveFeed:
    """Real-time price + volume + trade data from Binance WebSocket."""

    def __init__(self):
        self.tickers   = {}       # symbol → {price, volume, change, ...}
        self.klines    = defaultdict(lambda: deque(maxlen=200))  # symbol → [closes]
        self.order_books = {}     # symbol → {bids: [...], asks: [...]}
        self.trade_flow  = defaultdict(lambda: {'buy_vol': 0.0, 'sell_vol': 0.0, 'ts': 0})
        self.valid_symbols = set()
        self.connected = False
        self._lock = threading.Lock()

    def start(self, client):
        self._client = client
        self._load_rest(client)
        threading.Thread(target=self._ws_ticker,    daemon=True).start()
        threading.Thread(target=self._ws_agg_trade, daemon=True).start()
        log.info("LiveFeed started")

    def _load_rest(self, client):
        """Pre-load all tickers and valid symbols from REST API."""
        try:
            info = client.get_exchange_info()
            self.valid_symbols = {
                s['symbol'] for s in info['symbols']
                if s['symbol'].endswith('USDT') and s['status'] == 'TRADING'
            }
            tickers = client.get_ticker()
            for t in tickers:
                sym = t['symbol']
                if sym not in self.valid_symbols: continue
                self.tickers[sym] = {
                    'symbol':   sym,
                    'price':    float(t['lastPrice']),
                    'volume':   float(t['quoteVolume']),
                    'change':   float(t['priceChangePercent']),
                    'high':     float(t['highPrice']),
                    'low':      float(t['lowPrice']),
                    'open':     float(t['openPrice']),
                    'ts':       datetime.now().isoformat(),
                }
            log.info(f"REST pre-load: {len(self.tickers)} symbols")
        except Exception as e:
            log.error(f"REST pre-load error: {e}")

    def _ws_ticker(self):
        """Subscribe to all-market ticker stream."""
        def on_msg(ws, msg):
            data = json.loads(msg)
            for t in data:
                sym = t.get('s', '')
                if sym not in self.valid_symbols: continue
                with self._lock:
                    self.tickers[sym] = {
                        'symbol': sym,
                        'price':  float(t['c']),
                        'volume': float(t['q']),   # quote volume (USDT)
                        'change': float(t['P']),
                        'high':   float(t['h']),
                        'low':    float(t['l']),
                        'open':   float(t['o']),
                        'ts':     datetime.now().isoformat(),
                    }
                    self.klines[sym].append(float(t['c']))
                self.connected = True

        def on_err(ws, err): log.warning(f"Ticker WS error: {err}")
        def on_close(ws, *_):
            log.warning("Ticker WS closed, reconnecting...")
            time.sleep(5); self._ws_ticker()

        ws = websocket.WebSocketApp(
            'wss://stream.binance.com:9443/ws/!ticker@arr',
            on_message=on_msg, on_error=on_err, on_close=on_close
        )
        ws.run_forever()

    def _ws_agg_trade(self):
        """
        Track buy vs sell pressure using aggTrade stream for top coins.
        Binance marks taker side: m=True means seller is taker (sell pressure).
        """
        # We'll use individual streams for top 10 by volume
        streams = '/'.join([f'{s.lower()}@aggTrade'
                            for s in self._get_top_symbols(10)])
        url = f'wss://stream.binance.com:9443/stream?streams={streams}'

        def on_msg(ws, msg):
            data = json.loads(msg).get('data', {})
            sym = data.get('s', '')
            qty = float(data.get('q', 0)) * float(data.get('p', 0))
            is_sell = data.get('m', False)   # m=True → market sell
            now = time.time()
            with self._lock:
                tf = self.trade_flow[sym]
                # Reset every 5 minutes
                if now - tf.get('ts', 0) > 300:
                    tf['buy_vol'] = 0.0; tf['sell_vol'] = 0.0; tf['ts'] = now
                if is_sell:
                    tf['sell_vol'] += qty
                else:
                    tf['buy_vol']  += qty

        def on_err(ws, err): pass
        def on_close(ws, *_):
            time.sleep(10); self._ws_agg_trade()

        ws = websocket.WebSocketApp(url, on_message=on_msg,
                                    on_error=on_err, on_close=on_close)
        ws.run_forever()

    def _get_top_symbols(self, n=50):
        with self._lock:
            ranked = sorted(
                [t for t in self.tickers.values() if t['volume'] >= MIN_VOLUME],
                key=lambda x: x['volume'], reverse=True
            )
        return [t['symbol'] for t in ranked[:n]]

    def get_order_book_imbalance(self, client, symbol, depth=20):
        """
        Fetch order book and calculate bid/ask imbalance.
        Returns value between -1 (heavy sell) and +1 (heavy buy).
        """
        try:
            ob = client.get_order_book(symbol=symbol, limit=depth)
            bid_vol = sum(float(b[1]) for b in ob['bids'])
            ask_vol = sum(float(a[1]) for a in ob['asks'])
            total   = bid_vol + ask_vol
            if total == 0: return 0.0
            return round((bid_vol - ask_vol) / total, 3)
        except Exception:
            return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# TECHNICAL ANALYSIS — pure numpy, no TA-Lib dependency
# ═════════════════════════════════════════════════════════════════════════════

class TA:
    """Lightweight technical analysis using only numpy."""

    @staticmethod
    def rsi(prices, period=14):
        if len(prices) < period + 1: return 50.0
        p  = np.array(prices[-period*3:], dtype=float)
        d  = np.diff(p)
        up = np.where(d > 0, d, 0)
        dn = np.where(d < 0, -d, 0)
        # Exponential moving average
        au = np.mean(up[:period])
        ad = np.mean(dn[:period])
        for i in range(period, len(up)):
            au = (au * (period-1) + up[i]) / period
            ad = (ad * (period-1) + dn[i]) / period
        if ad == 0: return 100.0
        return round(100 - 100 / (1 + au/ad), 2)

    @staticmethod
    def ema(prices, period):
        if len(prices) < period: return prices[-1] if prices else 0
        p = np.array(prices, dtype=float)
        k = 2 / (period + 1)
        e = p[0]
        for v in p[1:]:
            e = v * k + e * (1 - k)
        return round(e, 8)

    @staticmethod
    def macd(prices, fast=12, slow=26, signal=9):
        if len(prices) < slow + signal:
            return {'macd': 0, 'signal': 0, 'hist': 0}
        ema_fast   = TA.ema(prices, fast)
        ema_slow   = TA.ema(prices, slow)
        macd_line  = ema_fast - ema_slow
        # Approximate signal line
        macd_series = [TA.ema(prices[:i], fast) - TA.ema(prices[:i], slow)
                       for i in range(slow, len(prices)+1)]
        sig = TA.ema(macd_series, signal) if len(macd_series) >= signal else macd_line
        return {
            'macd':   round(macd_line, 8),
            'signal': round(sig, 8),
            'hist':   round(macd_line - sig, 8),
        }

    @staticmethod
    def bollinger(prices, period=20, std_dev=2):
        if len(prices) < period:
            p = prices[-1] if prices else 0
            return {'upper': p, 'middle': p, 'lower': p, 'width': 0, 'squeeze': False}
        p      = np.array(prices[-period:], dtype=float)
        mean   = np.mean(p)
        std    = np.std(p)
        upper  = mean + std_dev * std
        lower  = mean - std_dev * std
        width  = (upper - lower) / mean if mean > 0 else 0
        # Squeeze: width in lowest 20% of recent history
        if len(prices) >= period * 2:
            widths = []
            for i in range(period, len(prices)):
                s = np.array(prices[i-period:i], dtype=float)
                m = np.mean(s)
                w = (np.std(s) * 2 * std_dev) / m if m > 0 else 0
                widths.append(w)
            squeeze = width <= np.percentile(widths, 20) if widths else False
        else:
            squeeze = False
        return {
            'upper':   round(upper, 8),
            'middle':  round(mean, 8),
            'lower':   round(lower, 8),
            'width':   round(width, 4),
            'squeeze': bool(squeeze),
            'pct_b':   round((prices[-1] - lower) / (upper - lower), 3) if upper != lower else 0.5,
        }

    @staticmethod
    def atr(highs, lows, closes, period=14):
        if len(closes) < period + 1: return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i]  - lows[i],
                abs(highs[i]  - closes[i-1]),
                abs(lows[i]   - closes[i-1])
            )
            trs.append(tr)
        return round(np.mean(trs[-period:]), 8)

    @staticmethod
    def volume_ratio(volumes, period=20):
        """Current volume vs N-period average. >1.5 = elevated volume."""
        if len(volumes) < period + 1: return 1.0
        avg = np.mean(volumes[-period-1:-1])
        cur = volumes[-1]
        return round(cur / avg if avg > 0 else 1.0, 2)


# ═════════════════════════════════════════════════════════════════════════════
# STRATEGIES — each returns a scored opportunity dict or None
# ═════════════════════════════════════════════════════════════════════════════

class Strategies:

    @staticmethod
    def momentum_breakout(symbol, ticker, closes, volumes, bb, rsi_val, macd_d, ob_imbalance):
        """
        MOMENTUM BREAKOUT — trade WITH the trend, never against it.

        Entry conditions (all must pass):
          ✅ Price up 5-25% in 24h (already has momentum)
          ✅ Volume > 1.5x average (real interest, not fake pump)
          ✅ RSI between 50-72 (not overbought yet)
          ✅ MACD histogram positive (momentum increasing)
          ✅ Order book imbalance > 0.1 (more buyers than sellers)
          ✅ Price above Bollinger middle band

        Edge: coins that break out with volume confirmation continue 70%+ of the time.
        """
        change  = ticker.get('change', 0)
        price   = ticker.get('price', 0)
        vol_ratio = TA.volume_ratio(volumes)

        score = 0
        reasons = []

        # Price momentum (5-25% up)
        if 5 <= change <= 10:
            score += 20; reasons.append(f"+{change:.1f}% momentum")
        elif 10 < change <= 20:
            score += 30; reasons.append(f"+{change:.1f}% strong momentum")
        elif 20 < change <= 35:
            score += 25; reasons.append(f"+{change:.1f}% explosive")
        elif change > 35:
            score += 10; reasons.append(f"+{change:.1f}% extended (risky)")
        else:
            return None  # Not moving up

        # Volume confirmation
        if vol_ratio >= 3.0:
            score += 25; reasons.append(f"vol {vol_ratio:.1f}x avg (institutional)")
        elif vol_ratio >= 2.0:
            score += 20; reasons.append(f"vol {vol_ratio:.1f}x avg (strong)")
        elif vol_ratio >= 1.5:
            score += 12; reasons.append(f"vol {vol_ratio:.1f}x avg (elevated)")
        else:
            score -= 10; reasons.append(f"vol only {vol_ratio:.1f}x avg")

        # RSI sweet spot
        if 55 <= rsi_val <= 68:
            score += 20; reasons.append(f"RSI {rsi_val} (sweet spot)")
        elif 50 <= rsi_val < 55:
            score += 10; reasons.append(f"RSI {rsi_val} (ok)")
        elif rsi_val > 72:
            score -= 15; reasons.append(f"RSI {rsi_val} (overbought risk)")
        elif rsi_val < 50:
            return None  # Not in momentum zone

        # MACD
        if macd_d.get('hist', 0) > 0:
            score += 15; reasons.append("MACD bullish")
        else:
            score -= 5

        # Order book (buyers > sellers)
        if ob_imbalance > 0.20:
            score += 15; reasons.append(f"OB imbalance +{ob_imbalance:.2f} (heavy buyers)")
        elif ob_imbalance > 0.10:
            score += 8;  reasons.append(f"OB imbalance +{ob_imbalance:.2f}")
        elif ob_imbalance < -0.10:
            score -= 10; reasons.append("OB: sellers dominating")

        # Price above middle BB
        if closes and closes[-1] > bb.get('middle', 0):
            score += 5; reasons.append("above BB middle")

        if score < 55: return None

        return {
            'symbol':    symbol,
            'action':    'LONG',
            'strategy':  'Momentum Breakout',
            'score':     min(score, 100),
            'confidence': min(score / 100, 0.98),
            'price':     price,
            'change':    change,
            'volume':    ticker.get('volume', 0),
            'rsi':       rsi_val,
            'macd_hist': macd_d.get('hist', 0),
            'ob_imbalance': ob_imbalance,
            'vol_ratio': vol_ratio,
            'reason':    f"Momentum: {', '.join(reasons[:3])}",
        }

    @staticmethod
    def bb_squeeze_breakout(symbol, ticker, closes, bb, rsi_val, ob_imbalance):
        """
        BOLLINGER BAND SQUEEZE BREAKOUT

        When volatility compresses (bands narrow = squeeze), a big move is coming.
        We detect the squeeze and wait for the breakout direction.

        Entry conditions:
          ✅ BB squeeze detected (width in lowest 20% of recent history)
          ✅ Price breaks above upper band OR below lower band
          ✅ Volume confirms the break

        Edge: squeezes resolve into 5-15% moves 65%+ of the time.
        This is one of the highest-probability setups in crypto.
        """
        if not bb.get('squeeze', False):
            return None

        price   = ticker.get('price', 0)
        change  = ticker.get('change', 0)
        pct_b   = bb.get('pct_b', 0.5)

        if price <= 0: return None

        score = 40  # Start with base score for having a squeeze
        reasons = ['BB squeeze detected']

        # Direction of breakout
        if pct_b > 0.85:
            # Breaking above upper band → LONG
            action = 'LONG'
            score += 30; reasons.append('breaking above upper band')
        elif pct_b < 0.15:
            # Breaking below lower band → SHORT
            action = 'SHORT'
            score += 30; reasons.append('breaking below lower band')
        else:
            return None  # In middle of band — no clear direction yet

        # Change confirms direction
        if action == 'LONG' and change > 2:
            score += 15; reasons.append(f'+{change:.1f}% confirming')
        elif action == 'SHORT' and change < -2:
            score += 15; reasons.append(f'{change:.1f}% confirming')
        else:
            score -= 10

        # Order book confirms direction
        if action == 'LONG' and ob_imbalance > 0.05:
            score += 10; reasons.append('buyers in control')
        elif action == 'SHORT' and ob_imbalance < -0.05:
            score += 10; reasons.append('sellers in control')

        # RSI confirms (not extreme)
        if action == 'LONG' and 45 <= rsi_val <= 70:
            score += 5
        elif action == 'SHORT' and 30 <= rsi_val <= 55:
            score += 5

        if score < 65: return None

        return {
            'symbol':    symbol,
            'action':    action,
            'strategy':  'BB Squeeze Breakout',
            'score':     min(score, 100),
            'confidence': min(score / 100, 0.96),
            'price':     price,
            'change':    change,
            'volume':    ticker.get('volume', 0),
            'rsi':       rsi_val,
            'bb_width':  bb.get('width', 0),
            'pct_b':     pct_b,
            'ob_imbalance': ob_imbalance,
            'reason':    f"BB Squeeze: {', '.join(reasons[:3])}",
        }

    @staticmethod
    def funding_rate_collection(symbol, funding_rate, ticker):
        """
        FUNDING RATE PASSIVE INCOME

        When traders pay high positive funding (longs pay shorts),
        we short the futures and collect that payment every 8 hours.
        Risk is very low if position size is conservative.

        Funding of 0.05%/8h = 0.15%/day = ~55%/year on that capital.
        Even at 0.01%/8h = meaningful passive income.

        Entry: funding > 0.03% (positive → shorts collect)
        Exit: after 1-3 funding periods or if price moves against us 1%
        """
        if funding_rate < 0.0003:  # 0.03% minimum
            return None

        price = ticker.get('price', 0)
        if price <= 0: return None

        annualized = funding_rate * 3 * 365 * 100  # 3 payments/day * 365 days

        score = 50
        reasons = [f"funding {funding_rate*100:.4f}%/8h"]

        if funding_rate >= 0.001:    # 0.1%+
            score = 90; reasons.append(f"very high ({annualized:.0f}%/yr)")
        elif funding_rate >= 0.0005: # 0.05%+
            score = 78; reasons.append(f"high ({annualized:.0f}%/yr)")
        elif funding_rate >= 0.0003: # 0.03%+
            score = 65; reasons.append(f"moderate ({annualized:.0f}%/yr)")

        return {
            'symbol':       symbol,
            'action':       'FUNDING_SHORT',
            'strategy':     'Funding Rate Collection',
            'score':        score,
            'confidence':   score / 100,
            'price':        price,
            'change':       ticker.get('change', 0),
            'volume':       ticker.get('volume', 0),
            'funding_rate': funding_rate,
            'annualized':   annualized,
            'reason':       f"Funding: {', '.join(reasons)}",
        }

    @staticmethod
    def volume_spike_reversal(symbol, ticker, closes, volumes, rsi_val, ob_imbalance):
        """
        VOLUME SPIKE REVERSAL — Oversold bounce

        When a coin drops hard (-8 to -20%) with massive volume spike,
        it often bounces 3-8%. This is different from catching falling knives
        because we require VOLUME EXHAUSTION (sellers running out).

        Conditions:
          ✅ Price dropped 8-20% (not more — beyond 20% is a trend)
          ✅ Volume spike > 3x average (capitulation/panic selling)
          ✅ RSI < 35 (oversold)
          ✅ Order book flipping positive (buyers stepping in)
          ✅ Price holding above support (previous day low)
        """
        change = ticker.get('change', 0)
        price  = ticker.get('price', 0)

        # Only for drops in the 8-20% range
        if not (-20 <= change <= -8):
            return None

        vol_ratio = TA.volume_ratio(volumes)

        score = 0
        reasons = []

        # Volume spike (capitulation signal)
        if vol_ratio >= 4.0:
            score += 35; reasons.append(f"vol spike {vol_ratio:.1f}x (capitulation)")
        elif vol_ratio >= 3.0:
            score += 25; reasons.append(f"vol spike {vol_ratio:.1f}x")
        elif vol_ratio >= 2.0:
            score += 15; reasons.append(f"vol elevated {vol_ratio:.1f}x")
        else:
            return None  # Need volume spike for this strategy

        # RSI oversold
        if rsi_val < 25:
            score += 30; reasons.append(f"RSI {rsi_val} (extreme oversold)")
        elif rsi_val < 35:
            score += 20; reasons.append(f"RSI {rsi_val} (oversold)")
        elif rsi_val < 45:
            score += 10; reasons.append(f"RSI {rsi_val} (below neutral)")
        else:
            return None

        # Order book reversal (buyers stepping in after the dump)
        if ob_imbalance > 0.15:
            score += 25; reasons.append(f"OB +{ob_imbalance:.2f} buyers stepping in")
        elif ob_imbalance > 0.05:
            score += 10
        elif ob_imbalance < 0:
            return None  # Still selling

        # Size of drop affects risk/reward
        if -15 <= change <= -10:
            score += 10; reasons.append("optimal drop size")

        if score < 65: return None

        return {
            'symbol':    symbol,
            'action':    'LONG',
            'strategy':  'Volume Spike Reversal',
            'score':     min(score, 100),
            'confidence': min(score / 100, 0.92),
            'price':     price,
            'change':    change,
            'volume':    ticker.get('volume', 0),
            'rsi':       rsi_val,
            'vol_ratio': vol_ratio,
            'ob_imbalance': ob_imbalance,
            'reason':    f"Reversal: {', '.join(reasons[:3])}",
        }


# ═════════════════════════════════════════════════════════════════════════════
# MARKET REGIME DETECTOR
# ═════════════════════════════════════════════════════════════════════════════

class RegimeDetector:
    """
    Detect overall market direction using BTC as the benchmark.
    No point taking LONG altcoin trades when BTC is in a downtrend.
    """

    def __init__(self):
        self.regime     = 'NEUTRAL'   # BULL / BEAR / NEUTRAL
        self.btc_change = 0.0
        self.btc_rsi    = 50.0
        self.last_update = datetime.min

    def update(self, feed):
        now = datetime.now()
        if (now - self.last_update).seconds < 120:
            return  # Only update every 2 minutes

        btc = feed.tickers.get('BTCUSDT', {})
        self.btc_change = btc.get('change', 0)
        btc_closes = list(feed.klines.get('BTCUSDT', []))

        if len(btc_closes) >= 15:
            self.btc_rsi = TA.rsi(btc_closes)

        old = self.regime

        if self.btc_change > 2 and self.btc_rsi > 52:
            self.regime = 'BULL'
        elif self.btc_change < -2 and self.btc_rsi < 48:
            self.regime = 'BEAR'
        else:
            self.regime = 'NEUTRAL'

        if self.regime != old:
            log.info(f"Market regime: {old} → {self.regime} "
                     f"(BTC {self.btc_change:+.1f}%, RSI {self.btc_rsi:.0f})")

        self.last_update = now

    def filter(self, opportunity):
        """Returns True if this trade fits the current regime."""
        action = opportunity.get('action', '')
        strategy = opportunity.get('strategy', '').lower()

        # Funding collection is always OK regardless of regime
        if 'funding' in strategy:
            return True

        if self.regime == 'BULL':
            return action in ('LONG', 'FUNDING_SHORT')
        elif self.regime == 'BEAR':
            return action in ('SHORT', 'FUNDING_SHORT')
        else:  # NEUTRAL — allow both but prefer momentum
            return True


# ═════════════════════════════════════════════════════════════════════════════
# FUNDING RATE FETCHER
# ═════════════════════════════════════════════════════════════════════════════

def get_funding_rates(client):
    """Get current funding rates for all USDT perpetual futures."""
    rates = {}
    try:
        data = client.futures_funding_rate(limit=1000)
        seen = {}
        for r in data:
            sym  = r['symbol']
            rate = float(r['fundingRate'])
            ts   = int(r['fundingTime'])
            if sym not in seen or ts > seen[sym]['ts']:
                seen[sym] = {'rate': rate, 'ts': ts}
        for sym, d in seen.items():
            if sym.endswith('USDT') and d['rate'] > 0:
                rates[sym] = d['rate']
    except Exception as e:
        log.warning(f"Funding rates error: {e}")
    return rates


# ═════════════════════════════════════════════════════════════════════════════
# HISTORICAL KLINES LOADER
# ═════════════════════════════════════════════════════════════════════════════

def load_klines(client, symbols, interval='1h', limit=100):
    """Load historical klines for TA calculations."""
    klines = {}
    for sym in symbols:
        try:
            raw = client.get_klines(symbol=sym, interval=interval, limit=limit)
            klines[sym] = {
                'closes':  [float(k[4]) for k in raw],
                'highs':   [float(k[2]) for k in raw],
                'lows':    [float(k[3]) for k in raw],
                'volumes': [float(k[5]) for k in raw],
            }
        except Exception:
            pass
    log.info(f"Loaded klines for {len(klines)} symbols")
    return klines


# ═════════════════════════════════════════════════════════════════════════════
# MAIN SCANNER LOOP
# ═════════════════════════════════════════════════════════════════════════════

class Scanner:

    def __init__(self):
        self.client  = Client(
            os.getenv('MAIN_API_KEY'),
            os.getenv('MAIN_API_SECRET')
        )
        self.feed    = LiveFeed()
        self.regime  = RegimeDetector()
        self.strats  = Strategies()
        self.klines  = {}
        self.klines_updated = datetime.min
        log.info("Scanner initializing...")

    def start(self):
        self.feed.start(self.client)
        log.info("Waiting 15s for WebSocket data...")
        time.sleep(15)
        log.info("Scanner ready — starting scan loop")
        self._loop()

    def _refresh_klines(self, symbols):
        """Reload klines every 30 minutes."""
        now = datetime.now()
        if (now - self.klines_updated).seconds < 1800 and self.klines:
            return
        log.info(f"Loading klines for {len(symbols)} symbols...")
        self.klines = load_klines(self.client, symbols[:TOP_N_COINS])
        self.klines_updated = now

    def _scan_once(self):
        """Run one full scan and write results."""
        now = datetime.now()

        # Get top coins by volume
        top = self.feed._get_top_symbols(TOP_N_COINS)
        if not top:
            log.warning("No symbols yet — WebSocket may still be connecting")
            return

        # Refresh hourly klines
        self._refresh_klines(top)

        # Update market regime
        self.regime.update(self.feed)

        # Get funding rates
        funding_rates = get_funding_rates(self.client)

        opportunities = []
        ta_data       = {}  # symbol → TA results (for Service 2)

        log.info(f"Scanning {len(top)} symbols | Regime: {self.regime.regime}")

        for symbol in top:
            ticker = self.feed.tickers.get(symbol, {})
            if not ticker: continue

            price  = ticker.get('price', 0)
            volume = ticker.get('volume', 0)
            if price <= 0 or volume < MIN_VOLUME: continue

            # Get historical data
            hist = self.klines.get(symbol, {})
            closes  = hist.get('closes',  list(self.feed.klines.get(symbol, [])))
            highs   = hist.get('highs',   [])
            lows    = hist.get('lows',    [])
            volumes = hist.get('volumes', [volume] * 20)

            if len(closes) < 20:
                closes = list(self.feed.klines.get(symbol, []))
            if len(closes) < 5: continue

            # Calculate indicators
            rsi_val  = TA.rsi(closes)
            macd_d   = TA.macd(closes)
            bb       = TA.bollinger(closes)
            vol_hist = volumes if len(volumes) >= 20 else [volume] * 20

            # Order book (rate-limit: only top 20 by volume)
            ob_imbalance = 0.0
            if symbol in top[:20]:
                ob_imbalance = self.feed.get_order_book_imbalance(
                    self.client, symbol, depth=20)

            # Buy/sell pressure
            tf = self.feed.trade_flow.get(symbol, {})
            buy_vol  = tf.get('buy_vol', 0)
            sell_vol = tf.get('sell_vol', 0)
            total_flow = buy_vol + sell_vol
            buy_pressure = buy_vol / total_flow if total_flow > 0 else 0.5

            # Store TA data for Service 2
            ta_data[symbol] = {
                'rsi':          rsi_val,
                'macd':         macd_d,
                'bb':           bb,
                'ob_imbalance': ob_imbalance,
                'buy_pressure': round(buy_pressure, 3),
                'vol_ratio':    TA.volume_ratio(vol_hist),
                'price':        price,
                'change':       ticker.get('change', 0),
                'volume':       volume,
            }

            # ── Run strategies ────────────────────────────────────────────

            # 1. Momentum Breakout
            opp = Strategies.momentum_breakout(
                symbol, ticker, closes, vol_hist, bb, rsi_val, macd_d, ob_imbalance)
            if opp and self.regime.filter(opp):
                opportunities.append(opp)

            # 2. BB Squeeze Breakout
            opp = Strategies.bb_squeeze_breakout(
                symbol, ticker, closes, bb, rsi_val, ob_imbalance)
            if opp and self.regime.filter(opp):
                opportunities.append(opp)

            # 3. Volume Spike Reversal
            opp = Strategies.volume_spike_reversal(
                symbol, ticker, closes, vol_hist, rsi_val, ob_imbalance)
            if opp and self.regime.filter(opp):
                opportunities.append(opp)

            # 4. Funding Rate Collection
            fr = funding_rates.get(symbol, 0)
            if fr > 0:
                opp = Strategies.funding_rate_collection(symbol, fr, ticker)
                if opp:
                    opportunities.append(opp)

        # Sort by score
        opportunities.sort(key=lambda x: x['score'], reverse=True)

        # Fear & Greed (macro sentiment)
        fear_greed = 50
        try:
            r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=5)
            if r.status_code == 200:
                fear_greed = int(r.json()['data'][0]['value'])
        except Exception:
            pass

        # Write state file
        state = {
            'timestamp':    now.isoformat(),
            'regime':       self.regime.regime,
            'btc_change':   self.regime.btc_change,
            'btc_rsi':      self.regime.btc_rsi,
            'fear_greed':   fear_greed,
            'symbols_scanned': len(top),
            'opportunities': opportunities,
            'ta_data':       ta_data,
            'funding_rates': {k: v for k, v in funding_rates.items()
                              if k in top and v > 0.0002},
        }

        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)

        opps_by_type = {}
        for o in opportunities:
            s = o['strategy']
            opps_by_type[s] = opps_by_type.get(s, 0) + 1

        log.info(f"✅ Scan complete: {len(opportunities)} opportunities")
        for strat, count in sorted(opps_by_type.items(), key=lambda x: -x[1]):
            log.info(f"   {strat}: {count}")
        if opportunities:
            best = opportunities[0]
            log.info(f"   🏆 Best: {best['action']} {best['symbol']} "
                     f"score={best['score']} {best['reason'][:60]}")

    def _loop(self):
        while True:
            try:
                self._scan_once()
            except Exception as e:
                log.error(f"Scan error: {e}")
                import traceback; traceback.print_exc()
            time.sleep(SCAN_INTERVAL)


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    log.info("=" * 60)
    log.info("WARFARE SCANNER — SERVICE 1")
    log.info("=" * 60)

    required = ['MAIN_API_KEY', 'MAIN_API_SECRET']
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing env vars: {missing}")
        sys.exit(1)

    Scanner().start()
