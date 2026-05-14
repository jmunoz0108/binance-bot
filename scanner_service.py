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
        self._c=c; self._rest(c)
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
def s_momentum(sym,tk,cl,vols,bbd,rv,mhv,obv):
    ch=tk.get('change',0); pr=tk.get('price',0)
    if ch<5: return None
    v=vr(vols); sc=0; rr=[]
    if 5<=ch<=10: sc+=20; rr.append(f"+{ch:.1f}%")
    elif ch<=20: sc+=28; rr.append(f"+{ch:.1f}% strong")
    elif ch<=35: sc+=22; rr.append(f"+{ch:.1f}% explosive")
    else: sc+=8; rr.append(f"+{ch:.1f}% extended")
    if v>=3: sc+=25; rr.append(f"vol {v:.1f}x")
    elif v>=2: sc+=18; rr.append(f"vol {v:.1f}x")
    elif v>=1.5: sc+=10
    else: sc-=10
    rv2=rsi(cl)
    if 55<=rv2<=68: sc+=18; rr.append(f"RSI {rv2:.0f}✅")
    elif rv2<50: return None
    elif rv2>72: sc-=15
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
    def allows(self,o):
        a=o.get('action','')
        if 'funding' in o.get('strategy','').lower(): return True
        if self.r=='BULL': return a=='LONG'
        if self.r=='BEAR': return a=='SHORT'
        return True

# ── Scanner ───────────────────────────────────────────────────────────────────
# Global state shared with Flask
STATE = {'timestamp': None, 'opportunities': [], 'regime': 'NEUTRAL',
         'fear_greed': 50, 'symbols_scanned': 0, 'strategy_counts': {},
         'funding_rates': {}, 'ta_data': {}}

class Scanner:
    def __init__(self):
        self.client=Client(os.getenv('MAIN_API_KEY'),os.getenv('MAIN_API_SECRET'))
        self.feed=Feed(); self.regime=Regime()
        self.klines={}; self.klines_ts=datetime.min

    def _load_klines(self,syms):
        if (datetime.now()-self.klines_ts).seconds<1800 and self.klines: return
        log.info(f"Loading klines for {len(syms[:TOP_N])} symbols...")
        new={}
        for s in syms[:TOP_N]:
            try:
                r=self.client.get_klines(symbol=s,interval='1h',limit=100)
                new[s]={'closes':[float(k[4]) for k in r],
                        'volumes':[float(k[5]) for k in r]}
            except: pass
        self.klines=new; self.klines_ts=datetime.now()
        log.info(f"✅ Klines ready: {len(new)} symbols")

    def _funding(self):
        rates={}
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
        if not top: log.warning("No data yet"); return
        self._load_klines(top); self.regime.update(self.feed)
        funding=self._funding()
        opps=[]; ta={}
        log.info(f"🔍 Scanning {len(top)} coins | Regime: {self.regime.r}")
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
                    if o and self.regime.allows(o): opps.append(o)
                except: pass
            fr=funding.get(sym,0)
            if fr>0:
                o=s_funding(sym,fr,tk)
                if o: opps.append(o)
        # Bybit
        if os.getenv('BYBIT_API_KEY'):
            try:
                from pybit.unified_trading import HTTP
                b=HTTP(api_key=os.getenv('BYBIT_API_KEY'),api_secret=os.getenv('BYBIT_API_SECRET'))
                r=b.get_tickers(category='linear')
                if r.get('retCode',1)==0:
                    for t in r['result']['list']:
                        s=t['symbol']
                        if not s.endswith('USDT'): continue
                        ch=float(t['price24hPcnt'])*100; vol=float(t['turnover24h'])
                        if abs(ch)>3 and vol>3_000_000:
                            opps.append({'symbol':s,'action':'LONG' if ch>0 else 'SHORT',
                                'strategy':'Bybit Scanner','score':65,'confidence':0.72,
                                'price':float(t['lastPrice']),'change':ch,'volume':vol,
                                'reason':f"Bybit: {ch:+.1f}% ${vol/1e6:.1f}M"})
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
