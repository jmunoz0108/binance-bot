"""
WARFARE SCANNER — Final version with built-in Flask API
No shared volume needed. Dashboard fetches via HTTP.
"""
import os, sys, time, json, logging, threading
import requests, numpy as np
from datetime import datetime
from collections import defaultdict, deque
from binance.client import Client
from flask import Flask, jsonify
import websocket

logging.basicConfig(level=logging.INFO, format='%(asctime)s [SCANNER] %(message)s')
log = logging.getLogger('scanner')

PORT = int(os.getenv('PORT', 8080))
MIN_VOLUME = 5_000_000
TOP_N = 50
SCAN_INTERVAL = 60

log.info("=" * 55)
log.info("WARFARE SCANNER — Starting")
log.info(f"  MAIN_API_KEY: {'SET ✅' if os.getenv('MAIN_API_KEY') else 'MISSING ❌'}")
log.info(f"  BYBIT_API_KEY: {'SET ✅' if os.getenv('BYBIT_API_KEY') else 'not set'}")
log.info(f"  PORT: {PORT}")
log.info("=" * 55)

# ── TA ────────────────────────────────────────────────────────────────────────
def rsi(p, n=14):
    if len(p)<n+1: return 50.
    d=np.diff(np.array(p[-(n*3):],dtype=float))
    u=np.where(d>0,d,0); v=np.where(d<0,-d,0)
    au=np.mean(u[:n]); ad=np.mean(v[:n])
    for i in range(n,len(u)):
        au=(au*(n-1)+u[i])/n; ad=(ad*(n-1)+v[i])/n
    return round(100-100/(1+au/ad),2) if ad>0 else 100.

def ema(p,n):
    if not p: return 0
    k=2/(n+1); e=float(p[0])
    for v in p[1:]: e=float(v)*k+e*(1-k)
    return e

def bb(p, n=20):
    if len(p)<n:
        v=p[-1] if p else 0
        return {'upper':v,'middle':v,'lower':v,'width':0,'squeeze':False,'pct_b':0.5}
    a=np.array(p[-n:],dtype=float); m=np.mean(a); s=np.std(a)
    up=m+2*s; lo=m-2*s; w=(up-lo)/m if m>0 else 0
    pb=(p[-1]-lo)/(up-lo) if up!=lo else 0.5
    sq=False
    if len(p)>=n*3:
        ws=[np.std(p[i-n:i])*4/np.mean(p[i-n:i]) for i in range(n,len(p)) if np.mean(p[i-n:i])>0]
        if ws: sq=w<=np.percentile(ws,20)
    return {'upper':round(up,8),'middle':round(m,8),'lower':round(lo,8),
            'width':round(w,4),'squeeze':bool(sq),'pct_b':round(pb,3)}

def vr(vols, n=20):
    if len(vols)<n+1: return 1.
    avg=np.mean(vols[-n-1:-1])
    return round(vols[-1]/avg,2) if avg>0 else 1.

def mh(p): return ema(p,12)-ema(p,26) if len(p)>=26 else 0

# ── Feed ──────────────────────────────────────────────────────────────────────
class Feed:
    def __init__(self):
        self.tickers={}; self.klines=defaultdict(lambda:deque(maxlen=200))
        self.valid=set(); self._lock=threading.Lock()

    def start(self, c):
        self._c=c
        if c is None:
            return   # Binance blocked — Feed stays empty, Bybit scan provides opps
        self._rest(c)
        threading.Thread(target=self._ws,daemon=True).start()

    def _rest(self, c):
        try:
            info=c.get_exchange_info()
            self.valid={s['symbol'] for s in info['symbols']
                       if s['symbol'].endswith('USDT') and s['status']=='TRADING'}
            for t in c.get_ticker():
                s=t['symbol']
                if s not in self.valid: continue
                self.tickers[s]={'symbol':s,'price':float(t['lastPrice']),
                    'volume':float(t['quoteVolume']),'change':float(t['priceChangePercent']),
                    'high':float(t['highPrice']),'low':float(t['lowPrice'])}
                self.klines[s].append(float(t['lastPrice']))
            log.info(f"REST loaded {len(self.tickers)} symbols")
        except Exception as e: log.error(f"REST: {e}")

    def _ws(self):
        def on_msg(ws,msg):
            for t in json.loads(msg):
                s=t.get('s','')
                if s not in self.valid: continue
                with self._lock:
                    self.tickers[s]={'symbol':s,'price':float(t['c']),
                        'volume':float(t['q']),'change':float(t['P']),
                        'high':float(t['h']),'low':float(t['l'])}
                    self.klines[s].append(float(t['c']))
        def on_open(ws): log.info("✅ WebSocket connected")
        def on_close(ws,*_): log.warning("WS closed, reconnecting..."); time.sleep(5); self._ws()
        websocket.WebSocketApp('wss://stream.binance.com:9443/ws/!ticker@arr',
            on_message=on_msg,on_open=on_open,on_close=on_close).run_forever()

    def top(self,n=50):
        with self._lock:
            r=sorted([t for t in self.tickers.values() if t['volume']>=MIN_VOLUME],
                     key=lambda x:x['volume'],reverse=True)
        return [t['symbol'] for t in r[:n]]

    def ob(self, c, sym, d=20):
        try:
            o=c.get_order_book(symbol=sym,limit=d)
            b=sum(float(x[1]) for x in o['bids']); a=sum(float(x[1]) for x in o['asks'])
            return round((b-a)/(b+a),3) if b+a>0 else 0.
        except: return 0.

# ── Strategies ────────────────────────────────────────────────────────────────
def mtf_gate(action, cl_1h, cl_4h, highs_4h=None, lows_4h=None):
    """
    MULTI-TIMEFRAME CONFIRMATION GATE — the solid core.

    A trade only passes when the higher timeframe AGREES with the trade
    direction. This is the "use higher timeframe for better data + confirmation,
    lower timeframe for entry" approach. Three checks, all must pass:

      • 4h TREND (the boss): EMA9>EMA21 rising = up; reverse = down. The trade
        must align — no LONG in a 4h downtrend, no SHORT in a 4h uptrend.
      • 1h DIRECTION: must confirm the same direction (not fighting the hour).
      • NOT EXTENDED: if price is already stretched far from the 4h EMA, the
        move is late — reject (this is what fixed the 'enters too late' problem).

    Returns (passed: bool, bonus: int, reason: str). bonus rewards strong
    multi-TF alignment so the best setups score highest.
    """
    if not cl_4h or len(cl_4h) < 21 or not cl_1h or len(cl_1h) < 21:
        return True, 0, "insufficient MTF data — neutral"   # don't block on missing data

    # 4h trend (the boss)
    e9_4  = ema(cl_4h, 9); e21_4 = ema(cl_4h, 21)
    e9_4p = ema(cl_4h[:-2], 9) if len(cl_4h) > 11 else e9_4
    cur4  = cl_4h[-1]
    up_4   = e9_4 > e21_4 and e9_4 >= e9_4p
    down_4 = e9_4 < e21_4 and e9_4 <= e9_4p
    strength_4 = abs(e9_4 - e21_4) / cur4 * 100 if cur4 else 0

    # 1h direction
    e9_1  = ema(cl_1h, 9); e21_1 = ema(cl_1h, 21)
    up_1   = e9_1 > e21_1
    down_1 = e9_1 < e21_1

    # Extension check (late-entry guard): how far is price above/below 4h EMA21?
    ext = (cur4 - e21_4) / e21_4 * 100 if e21_4 else 0

    if action == 'LONG':
        if not up_4:
            return False, 0, f"4h not up (e9{'<' if down_4 else '~'}e21) — no LONG"
        if not up_1:
            return False, 0, "1h not confirming up — wait"
        if ext > 12:
            return False, 0, f"extended +{ext:.0f}% above 4h EMA — late entry"
        bonus = 10
        if strength_4 >= 1.0: bonus += 8
        if up_1 and up_4:     bonus += 7
        if ext < 5:           bonus += 5   # early in the move = best
        return True, bonus, f"4h+1h UP aligned (str {strength_4:.1f}%, ext {ext:+.0f}%)"

    if action == 'SHORT':
        if not down_4:
            return False, 0, f"4h not down — no SHORT"
        if not down_1:
            return False, 0, "1h not confirming down — wait"
        if ext < -12:
            return False, 0, f"extended {ext:.0f}% below 4h EMA — late short"
        bonus = 10
        if strength_4 >= 1.0: bonus += 8
        if down_1 and down_4: bonus += 7
        if ext > -5:          bonus += 5
        return True, bonus, f"4h+1h DOWN aligned (str {strength_4:.1f}%, ext {ext:+.0f}%)"

    # funding / other: don't gate
    return True, 0, "non-directional — passed"


def exhaustion_veto(action, cl, vols, rv):
    """
    REVERSAL / EXHAUSTION VETO (pure math, no AI).

    Catches the two traps flagged on the charts:
      • PULLBACK TRAP — entering a LONG when the up-move is exhausting (price
        stalling, RSI rolling over, volume drying up) = buying the top (CLO case).
      • REVERSAL TRAP — entering a SHORT right as a downtrend bottoms and turns
        up (RSI turning up, capitulation volume done, bouncing) = shorting the
        bottom (DOGE case).

    Returns (block, confidence 0-1, reason). High confidence → block outright;
    mid confidence → let the AI tiebreaker decide.
    """
    if len(cl) < 20:
        return False, 0.0, "insufficient data"
    signals = []; conf = 0.0
    last3_up = sum(1 for i in range(-3, 0) if cl[i] > cl[i-1])
    last3_dn = 3 - last3_up
    rv_prev = rsi(cl[:-3]) if len(cl) > 17 else rv
    rsi_falling = rv < rv_prev - 3
    rsi_rising  = rv > rv_prev + 3
    v_now  = sum(vols[-2:]) / 2 if len(vols) >= 2 else 0
    v_base = sum(vols[-12:-3]) / 9 if len(vols) >= 12 and sum(vols[-12:-3]) > 0 else 0
    v_climax = max(vols[-6:-2]) if len(vols) >= 6 else 0
    vol_drying = v_base > 0 and v_now < v_base * 0.7
    vol_climax_done = v_base > 0 and v_climax > v_base * 2.2 and v_now < v_climax * 0.6
    if action == 'LONG':
        if rv > 70:       signals.append("overbought"); conf += 0.35
        if rsi_falling:   signals.append("RSI rolling over"); conf += 0.30
        if last3_dn >= 2: signals.append("last candles red"); conf += 0.20
        if vol_drying:    signals.append("volume drying up"); conf += 0.20
        e9 = ema(cl, 9)
        if e9 and (cl[-1] - e9) / e9 * 100 > 8:
            signals.append("stretched above EMA"); conf += 0.25
    else:
        if rv < 30:           signals.append("oversold"); conf += 0.35
        if rsi_rising:        signals.append("RSI turning up"); conf += 0.30
        if last3_up >= 2:     signals.append("last candles green"); conf += 0.25
        if vol_climax_done:   signals.append("capitulation done"); conf += 0.25
        e9 = ema(cl, 9)
        if e9 and (e9 - cl[-1]) / e9 * 100 > 8:
            signals.append("stretched below EMA"); conf += 0.20
    conf = min(1.0, conf)
    return (conf >= 0.55), conf, (", ".join(signals) if signals else "clean")


def s_momentum(sym,tk,cl,vols,bbd,rv,mhv,obv):
    ch=tk.get('change',0); pr=tk.get('price',0)
    # Was: ch<5 → only fired AFTER a coin already ran 5%+ (entering late, at the
    # top). Now catch momentum as it BUILDS: 2%+ with rising volume. Also cap the
    # extended chase — a coin already up 20%+ is a LATE entry, penalize it.
    if ch<2: return None
    v=vr(vols); sc=0; rr=[]
    if 2<=ch<5:   sc+=24; rr.append(f"+{ch:.1f}% early")   # the sweet spot: early
    elif 5<=ch<=10: sc+=20; rr.append(f"+{ch:.1f}%")
    elif ch<=20: sc+=14; rr.append(f"+{ch:.1f}% strong")
    else: sc-=5; rr.append(f"+{ch:.1f}% extended/late")    # too late, discourage
    if v>=3: sc+=25; rr.append(f"vol {v:.1f}x")
    elif v>=2: sc+=18; rr.append(f"vol {v:.1f}x")
    elif v>=1.5: sc+=10
    else: sc-=10
    rv2=rsi(cl)
    # Earlier entries mean lower RSI is GOOD (move just starting), not a reject.
    if 48<=rv2<=62: sc+=18; rr.append(f"RSI {rv2:.0f}✅ early")
    elif 62<rv2<=70: sc+=10; rr.append(f"RSI {rv2:.0f}")
    elif rv2<42: return None       # no momentum yet
    elif rv2>74: sc-=18            # overbought = late
    if mhv>0: sc+=10; rr.append("MACD↑")
    if obv>0.20: sc+=14; rr.append(f"OB+{obv:.2f}")
    elif obv>0.10: sc+=7
    elif obv<-0.10: sc-=10
    if cl and cl[-1]>bbd.get('middle',0): sc+=5
    if sc<55: return None
    return {'symbol':sym,'action':'LONG','strategy':'Momentum Breakout',
            'score':min(sc,100),'confidence':min(sc/100,.98),'price':pr,
            'change':ch,'volume':tk.get('volume',0),'rsi':rv2,'ob_imbalance':obv,
            'vol_ratio':v,'reason':f"Momentum: {', '.join(rr[:3])}"}

def s_squeeze(sym,tk,cl,bbd,rv,obv):
    if not bbd.get('squeeze',False): return None
    pr=tk.get('price',0); ch=tk.get('change',0); pb=bbd.get('pct_b',0.5)
    if pr<=0: return None
    sc=40; rr=['BB squeeze']
    if pb>0.85: action='LONG'; sc+=30; rr.append('above upper')
    elif pb<0.15: action='SHORT'; sc+=30; rr.append('below lower')
    else: return None
    if action=='LONG' and ch>2: sc+=15; rr.append(f'+{ch:.1f}%')
    elif action=='SHORT' and ch<-2: sc+=15; rr.append(f'{ch:.1f}%')
    else: sc-=10
    if action=='LONG' and obv>0.05: sc+=10
    elif action=='SHORT' and obv<-0.05: sc+=10
    if sc<65: return None
    return {'symbol':sym,'action':action,'strategy':'BB Squeeze Breakout',
            'score':min(sc,100),'confidence':min(sc/100,.96),'price':pr,
            'change':ch,'volume':tk.get('volume',0),'rsi':rv,'pct_b':pb,
            'ob_imbalance':obv,'reason':f"BB Squeeze: {', '.join(rr[:3])}"}

def s_reversal(sym,tk,cl,vols,rv,obv):
    ch=tk.get('change',0); pr=tk.get('price',0)
    if not (-20<=ch<=-8): return None
    v=vr(vols); sc=0; rr=[]
    if v>=4: sc+=35; rr.append(f"vol {v:.1f}x capitulation")
    elif v>=3: sc+=25; rr.append(f"vol {v:.1f}x")
    elif v>=2: sc+=15
    else: return None
    if rv<25: sc+=30; rr.append(f"RSI {rv:.0f} extreme")
    elif rv<35: sc+=20; rr.append(f"RSI {rv:.0f} oversold")
    elif rv<45: sc+=10
    else: return None
    if obv>0.15: sc+=25; rr.append("buyers stepping in")
    elif obv>0.05: sc+=10
    elif obv<0: return None
    if sc<65: return None
    return {'symbol':sym,'action':'LONG','strategy':'Volume Spike Reversal',
            'score':min(sc,100),'confidence':min(sc/100,.92),'price':pr,
            'change':ch,'volume':tk.get('volume',0),'rsi':rv,'vol_ratio':v,
            'ob_imbalance':obv,'reason':f"Reversal: {', '.join(rr[:3])}"}

def s_funding(sym,rate,tk):
    if rate<0.0003: return None
    pr=tk.get('price',0)
    if pr<=0: return None
    annual=round(rate*3*365*100,1)
    sc=min(50+int(rate/0.0001)*8,95)
    return {'symbol':sym,'action':'FUNDING_SHORT','strategy':'Funding Rate Collection',
            'score':sc,'confidence':sc/100,'price':pr,'change':tk.get('change',0),
            'volume':tk.get('volume',0),'funding_rate':rate,'annualized':annual,
            'reason':f"Funding {rate*100:.4f}%/8h (~{annual}%/yr)"}

# ── Regime ────────────────────────────────────────────────────────────────────
class Regime:
    def __init__(self): self.r='NEUTRAL'; self.bc=0.; self.br=50.; self._ts=datetime.min
    def update(self,feed):
        if (datetime.now()-self._ts).seconds<120: return
        btc=feed.tickers.get('BTCUSDT',{}); self.bc=btc.get('change',0)
        cl=list(feed.klines.get('BTCUSDT',[]))
        if len(cl)>=15: self.br=rsi(cl)
        old=self.r
        if self.bc>2 and self.br>52: self.r='BULL'
        elif self.bc<-2 and self.br<48: self.r='BEAR'
        else: self.r='NEUTRAL'
        if self.r!=old: log.info(f"📈 Regime: {old}→{self.r}")
        self._ts=datetime.now()
    def allows(self,o,coin_klines=None):
        a=o.get('action','')
        if 'funding' in o.get('strategy','').lower(): return True
        if self.r=='BULL': return a=='LONG'
        if self.r=='BEAR': return a=='SHORT'
        # NEUTRAL regime: the old code allowed EVERYTHING here, which is most of
        # the time — so longs AND shorts flooded through with no protection,
        # producing the bad win rate. Now: in NEUTRAL, require the trade to align
        # with the COIN'S OWN trend (EMA9 vs EMA21). No clean coin trend → reject.
        if coin_klines is not None and len(coin_klines) >= 21:
            try:
                cl = [float(x) for x in coin_klines]
                e9, e21 = ema(cl, 9), ema(cl, 21)
                e9_prev = ema(cl[:-3], 9) if len(cl) > 12 else e9
                up   = e9 > e21 and e9 > e9_prev
                down = e9 < e21 and e9 < e9_prev
                if a == 'LONG':  return up
                if a == 'SHORT': return down
            except Exception:
                return True
        return True

# ── Scanner ───────────────────────────────────────────────────────────────────
# AI tiebreaker — ONLY called for mid-confidence exhaustion cases (0.35-0.55),
# where the math is unsure. High-confidence traps are blocked without AI; clean
# setups pass without AI. So Groq is used sparingly (cost + latency control), and
# a circuit breaker disables it after repeated failures so it can never strangle
# trading the way the old setup did.
_AI_STATE = {'dead': False, 'fails': 0, 'calls': 0}

def ai_tiebreak(symbol, action, reason, rv, change):
    """Returns True = allow trade, False = block. Defaults to ALLOW on any error
    (math veto already did the safety work; AI is only a bonus opinion)."""
    if _AI_STATE['dead']:
        return True
    key = os.getenv('GROQ_API_KEY') or os.getenv('GROQ_KEY')
    if not key:
        return True
    try:
        import requests as _rq
        prompt = (f"{action} {symbol}, RSI={rv:.0f}, 24h change={change:.1f}%. "
                  f"Possible reversal/exhaustion signals: {reason}. "
                  f"Is this a TRAP (entering at a top/bottom about to reverse)? "
                  f"Answer ONLY 'TRAP' or 'OK'.")
        r = _rq.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 5, "temperature": 0},
            timeout=6)
        _AI_STATE['calls'] += 1
        if r.status_code == 200:
            ans = r.json()['choices'][0]['message']['content'].strip().upper()
            _AI_STATE['fails'] = 0
            return 'TRAP' not in ans      # TRAP → block, OK → allow
        else:
            _AI_STATE['fails'] += 1
    except Exception:
        _AI_STATE['fails'] += 1
    if _AI_STATE['fails'] >= 5:
        _AI_STATE['dead'] = True
        log.warning("🧠 AI tiebreaker disabled (5 fails) — math veto only")
    return True   # default allow on error


# Global state shared with Flask
STATE = {'timestamp': None, 'opportunities': [], 'regime': 'NEUTRAL',
         'fear_greed': 50, 'symbols_scanned': 0, 'strategy_counts': {},
         'funding_rates': {}, 'ta_data': {}}

class Scanner:
    def __init__(self):
        try:
            self.client=Client(os.getenv('MAIN_API_KEY'),os.getenv('MAIN_API_SECRET'))
            log.info("✅ Binance client ready")
        except Exception as e:
            log.warning(f"⚠️  Binance unavailable ({str(e)[:60]}) — "
                        f"scanner runs on Bybit only (not fatal)")
            self.client=None
        self.feed=Feed(); self.regime=Regime()
        self.klines={}; self.klines_ts=datetime.min

    def _load_klines(self,syms):
        if self.client is None:
            return   # Binance blocked — Bybit scan handles its own klines
        if (datetime.now()-self.klines_ts).seconds<1800 and self.klines: return
        log.info(f"Loading multi-TF klines for {len(syms[:TOP_N])} symbols...")
        new={}
        for s in syms[:TOP_N]:
            try:
                # 1h for momentum/entry, 4h for the higher-timeframe trend (the
                # boss). A trade only fires when 4h trend + 1h direction agree.
                r1=self.client.get_klines(symbol=s,interval='1h',limit=100)
                d={'closes':[float(k[4]) for k in r1],
                   'volumes':[float(k[5]) for k in r1]}
                try:
                    r4=self.client.get_klines(symbol=s,interval='4h',limit=60)
                    d['closes_4h']=[float(k[4]) for k in r4]
                    d['highs_4h']=[float(k[2]) for k in r4]
                    d['lows_4h']=[float(k[3]) for k in r4]
                except Exception:
                    d['closes_4h']=[]
                new[s]=d
            except: pass
        self.klines=new; self.klines_ts=datetime.now()
        log.info(f"✅ Multi-TF klines ready: {len(new)} symbols (1h + 4h)")

    def _funding(self):
        rates={}
        if self.client is None:
            return rates
        try:
            seen={}
            for r in self.client.futures_funding_rate(limit=500):
                s=r['symbol']; rt=float(r['fundingRate']); ts=int(r['fundingTime'])
                if s not in seen or ts>seen[s]['ts']: seen[s]={'rate':rt,'ts':ts}
            rates={s:d['rate'] for s,d in seen.items()
                   if s.endswith('USDT') and d['rate']>0}
        except Exception as e: log.warning(f"Funding: {e}")
        return rates

    def scan(self):
        global STATE
        top=self.feed.top(TOP_N)
        # If Binance is blocked, top is empty — that's OK, skip the Binance loop
        # and go straight to the Bybit scan below (Bybit-only mode).
        if not top and self.client is not None:
            log.warning("No data yet"); return
        if self.client is not None:
            self._load_klines(top)
        self.regime.update(self.feed)
        funding=self._funding()
        opps=[]; ta={}
        log.info(f"🔍 Scanning {len(top)} Binance + Bybit | Regime: {self.regime.r}")
        for sym in top:
            tk=self.feed.tickers.get(sym,{})
            if tk.get('price',0)<=0: continue
            h=self.klines.get(sym,{})
            cl=h.get('closes',list(self.feed.klines.get(sym,[])))
            vols=h.get('volumes',[tk.get('volume',0)]*21)
            if len(cl)<5: continue
            rv=rsi(cl); mhv=mh(cl); bbd=bb(cl); vv=vr(vols)
            obv=self.feed.ob(self.client,sym) if sym in top[:20] else 0.
            ta[sym]={'rsi':rv,'macd_hist':mhv,'bb':bbd,'ob_imbalance':obv,
                     'vol_ratio':vv,'price':tk.get('price',0),'change':tk.get('change',0),
                     'volume':tk.get('volume',0)}
            for fn,args in [(s_momentum,(sym,tk,cl,vols,bbd,rv,mhv,obv)),
                            (s_squeeze,(sym,tk,cl,bbd,rv,obv)),
                            (s_reversal,(sym,tk,cl,vols,rv,obv))]:
                try:
                    o=fn(*args)
                    if not o: continue
                    if not self.regime.allows(o, cl): continue
                    # MULTI-TIMEFRAME GATE — higher timeframe must confirm
                    cl4 = h.get('closes_4h', [])
                    _ok, _bonus, _why = mtf_gate(o['action'], cl, cl4,
                                                 h.get('highs_4h'), h.get('lows_4h'))
                    if not _ok:
                        continue   # higher timeframe disagrees — skip
                    # EXHAUSTION/REVERSAL VETO (math first)
                    _blk, _vconf, _vwhy = exhaustion_veto(o['action'], cl, vols, rv)
                    if _blk:
                        # high-confidence trap — block outright, no AI needed
                        continue
                    if _vconf >= 0.35:
                        # mid-confidence: math unsure → AI tiebreaker decides
                        if not ai_tiebreak(sym, o['action'], _vwhy, rv,
                                           tk.get('change', 0)):
                            continue   # AI says TRAP → skip
                        o['reason'] = f"{o.get('reason','')} | AI-OK({_vwhy})"
                    o['score'] = min(100, o.get('score', 50) + _bonus)
                    o['mtf'] = _why
                    o['reason'] = f"{o.get('reason','')} | MTF: {_why}"
                    opps.append(o)
                except: pass
            fr=funding.get(sym,0)
            if fr>0:
                o=s_funding(sym,fr,tk)
                if o: opps.append(o)
        # Bybit — now runs through the SAME quality gates as Binance (was just
        # a naive "went up → LONG" with a flat score, skipping all the new
        # filtering). Now runs the SAME 3 strategies (momentum/squeeze/reversal)
        # as Binance with the same indicators — full parity, not a weaker scan.
        if os.getenv('BYBIT_API_KEY'):
            try:
                from pybit.unified_trading import HTTP
                b=HTTP(api_key=os.getenv('BYBIT_API_KEY'),api_secret=os.getenv('BYBIT_API_SECRET'))
                r=b.get_tickers(category='linear')
                if r.get('retCode',1)==0:
                    cands=[t for t in r['result']['list']
                           if t['symbol'].endswith('USDT')
                           and abs(float(t['price24hPcnt'])*100)>2
                           and float(t['turnover24h'])>2_000_000]
                    cands.sort(key=lambda t: float(t['turnover24h']), reverse=True)
                    by_count=0
                    for t in cands[:TOP_N]:   # scan as many as Binance (TOP_N)
                        s=t['symbol']
                        ch=float(t['price24hPcnt'])*100; vol=float(t['turnover24h'])
                        px=float(t['lastPrice'])
                        try:
                            k1=b.get_kline(category='linear',symbol=s,interval='60',limit=60)
                            k4=b.get_kline(category='linear',symbol=s,interval='240',limit=50)
                            cl=[float(x[4]) for x in reversed(k1['result']['list'])]
                            cl4=[float(x[4]) for x in reversed(k4['result']['list'])]
                            vols=[float(x[5]) for x in reversed(k1['result']['list'])]
                        except Exception:
                            continue
                        if len(cl)<21: continue
                        # same indicators Binance uses
                        rvb=rsi(cl); mhvb=mh(cl); bbdb=bb(cl)
                        tkb={'price':px,'change':ch,'volume':vol}
                        # run the SAME 3 strategies as Binance
                        for fn,args in [(s_momentum,(s,tkb,cl,vols,bbdb,rvb,mhvb,0.)),
                                        (s_squeeze,(s,tkb,cl,bbdb,rvb,0.)),
                                        (s_reversal,(s,tkb,cl,vols,rvb,0.))]:
                            try:
                                o=fn(*args)
                                if not o: continue
                                o['strategy']='Bybit '+o['strategy']
                                o['exchange']='bybit'
                                if not self.regime.allows(o, cl): continue
                                _ok,_bonus,_why=mtf_gate(o['action'],cl,cl4)
                                if not _ok: continue
                                _blk,_vc,_vw=exhaustion_veto(o['action'],cl,vols,rvb)
                                if _blk: continue
                                if _vc>=0.35 and not ai_tiebreak(s,o['action'],_vw,rvb,ch): continue
                                o['score']=min(100,o.get('score',60)+_bonus)
                                o['mtf']=_why
                                o['reason']=f"{o.get('reason','')} | MTF: {_why}"
                                opps.append(o); by_count+=1
                            except: pass
                    log.info(f"   🟣 Bybit: {by_count} signals (full strategy parity)")
            except Exception as e: log.info(f"Bybit: {e}")
        opps.sort(key=lambda x:x['score'],reverse=True)
        by_s=defaultdict(int)
        for o in opps: by_s[o['strategy']]+=1
        fg=50
        try:
            resp=requests.get('https://api.alternative.me/fng/?limit=1',timeout=5)
            if resp.status_code==200: fg=int(resp.json()['data'][0]['value'])
        except: pass
        log.info(f"✅ {len(opps)} opportunities | FG={fg}")
        for s,c in sorted(by_s.items(),key=lambda x:-x[1]):
            log.info(f"   {s:30s}: {c}")
        if opps:
            log.info(f"   🏆 Best: {opps[0]['action']} {opps[0]['symbol']} "
                     f"score={opps[0]['score']} {opps[0]['reason'][:50]}")
        STATE = {'timestamp':datetime.now().isoformat(),'regime':self.regime.r,
                 'btc_change':self.regime.bc,'btc_rsi':self.regime.br,
                 'fear_greed':fg,'symbols_scanned':len(top),'opportunities':opps,
                 'ta_data':ta,'strategy_counts':dict(by_s),
                 'funding_rates':{k:v for k,v in funding.items() if k in top and v>0.0002}}

    def start(self):
        self.feed.start(self.client)
        log.info("Waiting 15s for WebSocket..."); time.sleep(15)
        log.info("✅ Scan loop starting")
        while True:
            try: self.scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
                import traceback; traceback.print_exc()
            time.sleep(SCAN_INTERVAL)

# ── Flask API ─────────────────────────────────────────────────────────────────
flask_app = Flask('scanner')

@flask_app.route('/api/state')
def api_state():
    return jsonify(STATE)

@flask_app.route('/health')
def health():
    return jsonify({'status':'ok','symbols':len(STATE.get('ta_data',{}))})

if __name__ == '__main__':
    scanner = Scanner()
    # Flask in background
    threading.Thread(
        target=lambda: flask_app.run(host='0.0.0.0',port=PORT,debug=False,use_reloader=False),
        daemon=True).start()
    log.info(f"✅ Scanner API on port {PORT}")
    scanner.start()
