"""
WARFARE DASHBOARD — Fixed version
Fixes: red dots showing for online services (0 || 9999 JS bug)
"""
import os, time, json
import requests
from flask import Flask, jsonify, render_template_string
from datetime import datetime

app = Flask(__name__)

SCANNER_URL = os.getenv('SCANNER_URL', '')
FILTER_URL  = os.getenv('FILTER_URL', '')
TRADER_URL  = os.getenv('TRADER_URL', '')

def fetch(url, path='/api/state'):
    """
    Returns (data, age_seconds).
    FIX: Returns actual elapsed time instead of 0,
    so JavaScript 'age || 9999' works correctly.
    0 is falsy in JS — any positive number is truthy.
    """
    try:
        start = time.time()
        r = requests.get(f"{url}{path}", timeout=5)
        elapsed = max(0.1, time.time() - start)  # min 0.1s so JS sees truthy value
        if r.status_code == 200:
            return r.json(), round(elapsed, 2)
    except Exception:
        pass
    return {}, 9999

DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>⚔️ Warfare System</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#07090f;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;padding:16px}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;
        padding:16px 20px;background:#0e1117;border:1px solid #1a1f2e;border-radius:14px}
.header h1{font-size:20px;font-weight:700;color:#7c3aed}
.ts{font-size:11px;color:#4b5563}
.services{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}
.svc{background:#0e1117;border:1px solid #1a1f2e;border-radius:10px;padding:14px;
     display:flex;align-items:center;gap:12px}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dot.g{background:#10b981;box-shadow:0 0 8px #10b981}
.dot.r{background:#ef4444;box-shadow:0 0 8px #ef4444}
.dot.y{background:#f59e0b;box-shadow:0 0 8px #f59e0b}
.svc-info{flex:1}.svc-name{font-size:12px;font-weight:600}
.svc-age{font-size:10px;color:#4b5563;margin-top:2px}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-bottom:20px}
.stat{background:#0e1117;border:1px solid #1a1f2e;border-radius:12px;padding:16px;text-align:center}
.stat .num{font-size:26px;font-weight:700;margin:4px 0}
.stat .lbl{font-size:10px;color:#4b5563;text-transform:uppercase}
.g{color:#10b981}.r{color:#ef4444}.y{color:#f59e0b}.p{color:#7c3aed}.b{color:#3b82f6}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
.panel{background:#0e1117;border:1px solid #1a1f2e;border-radius:14px;padding:18px}
.ptitle{font-size:13px;font-weight:700;color:#7c3aed;margin-bottom:14px}
.opp{background:#0a0d14;border:1px solid #1a1f2e;border-radius:8px;padding:10px 12px;
     display:flex;align-items:center;gap:10px;margin-bottom:6px}
.sc{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;
    justify-content:center;font-size:13px;font-weight:700;flex-shrink:0}
.sc.h{background:#052e16;color:#34d399}.sc.m{background:#422006;color:#fb923c}
.sc.l{background:#1c1917;color:#a8a29e}
.om{flex:1;min-width:0}
.osym{font-size:13px;font-weight:600}
.ostrat{font-size:10px;color:#4b5563;margin-top:1px}
.oreason{font-size:10px;color:#6b7280;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.or{text-align:right;flex-shrink:0}
.badge{padding:2px 8px;border-radius:6px;font-size:10px;font-weight:600;margin-right:4px}
.bl{background:#052e16;color:#34d399}.bs{background:#3f1515;color:#f87171}
.bf{background:#172554;color:#93c5fd}
.bw{background:#052e16;color:#34d399}.blo{background:#3f1515;color:#f87171}
table{width:100%;border-collapse:collapse;font-size:11px}
th{color:#4b5563;text-align:left;padding:6px 8px;border-bottom:1px solid #1a1f2e;font-size:10px;text-transform:uppercase}
td{padding:6px 8px;border-bottom:1px solid #0e1117}
.mode-d{background:#1e3a5f;color:#60a5fa;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600}
.mode-r{background:#3f1515;color:#f87171;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600}
.bar-w{background:#1a1f2e;border-radius:4px;height:5px;overflow:hidden;margin-top:4px}
.bar-f{height:100%;border-radius:4px}
.fund{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #111}
.fund:last-child{border:none}
</style>
</head>
<body>
<div class="header">
  <div><h1>⚔️ Warfare <span style="color:#e2e8f0">Trading System</span></h1>
  <div class="ts" id="ts">Loading...</div></div>
  <div id="mode" class="mode-d">🟡 DEMO</div>
</div>
<div class="services">
  <div class="svc"><div class="dot r" id="d1"></div>
    <div class="svc-info"><div class="svc-name">📡 Scanner</div>
    <div class="svc-age" id="a1">offline</div></div>
    <div style="font-size:11px;color:#4b5563" id="c1">0 coins</div></div>
  <div class="svc"><div class="dot r" id="d2"></div>
    <div class="svc-info"><div class="svc-name">🔬 Filter AI</div>
    <div class="svc-age" id="a2">offline</div></div>
    <div style="font-size:11px;color:#4b5563" id="c2">0 passed</div></div>
  <div class="svc"><div class="dot r" id="d3"></div>
    <div class="svc-info"><div class="svc-name">⚡ Trader</div>
    <div class="svc-age" id="a3">offline</div></div>
    <div style="font-size:11px;color:#4b5563" id="c3">0 open</div></div>
</div>
<div class="stats">
  <div class="stat"><div class="num g" id="wr">0%</div><div class="lbl">Win Rate</div></div>
  <div class="stat"><div class="num" id="pnl">$0.00</div><div class="lbl">Total P&L</div></div>
  <div class="stat"><div class="num p" id="tt">0</div><div class="lbl">Closed Trades</div></div>
  <div class="stat"><div class="num g" id="ws">0</div><div class="lbl">Wins</div></div>
  <div class="stat"><div class="num r" id="ls">0</div><div class="lbl">Losses</div></div>
  <div class="stat"><div class="num y" id="op">0</div><div class="lbl">Open Now</div></div>
  <div class="stat"><div class="num b" id="rg">—</div><div class="lbl">Regime</div></div>
  <div class="stat"><div class="num" id="fg">—</div><div class="lbl">Fear & Greed</div></div>
</div>
<div class="panels">
  <div class="panel"><div class="ptitle">⚡ Open Positions</div><div id="ops">
    <div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">No open positions</div></div></div>
  <div class="panel"><div class="ptitle">🔬 Filtered Signals</div><div id="sigs">
    <div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">Waiting for signals...</div></div></div>
  <div class="panel"><div class="ptitle">📡 Scanner Top Picks</div><div id="scan">
    <div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">Scanning...</div></div></div>
  <div class="panel"><div class="ptitle">💰 High Funding Rates</div><div id="fund">
    <div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">Scanning...</div></div></div>
</div>
<div class="panel" style="margin-bottom:20px">
  <div class="ptitle">📊 Recent Closed Trades</div>
  <table><thead><tr><th>Symbol</th><th>Action</th><th>Strategy</th><th>Entry</th>
    <th>Exit</th><th>P&L%</th><th>P&L$</th><th>Score</th><th>Outcome</th><th>Reason</th>
  </tr></thead><tbody id="trades"></tbody></table>
</div>
<div class="panels">
  <div class="panel"><div class="ptitle">🏆 Strategy Win Rates</div><div id="strats">
    <div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">Need 3+ trades per strategy</div></div></div>
  <div class="panel"><div class="ptitle">📈 P&L History</div>
    <div id="chart" style="display:flex;align-items:flex-end;gap:3px;height:60px;margin-top:8px"></div>
    <div style="display:flex;justify-content:space-between;margin-top:6px">
      <span style="font-size:10px;color:#4b5563">Oldest</span>
      <span style="font-size:10px;color:#4b5563">Latest</span></div></div>
</div>
<script>
var f=function(n,d){return(+n||0).toFixed(d||2)}
var fp=function(n){var v=+n||0;return(v>=0?'+':'')+v.toFixed(2)+'%'}
function g(id){return document.getElementById(id)}
function sc(el,v){el.style.color=v>0?'#10b981':v<0?'#ef4444':'#f59e0b'}

// FIX: use (age !== undefined && age !== null) instead of age||9999
// Old bug: 0 || 9999 = 9999 in JS because 0 is falsy
function dot(id,age,thr){
  var d=g(id);
  var a=(age!==undefined&&age!==null&&age!==9999)?age:9999;
  d.className='dot '+(a<thr?'g':a<thr*3?'y':'r')
}
function ageStr(age){
  if(age===undefined||age===null||age>=9999)return 'offline';
  if(age<2)return 'just now';
  return age.toFixed(1)+'s ago'
}

function ab(a){
  if(a==='LONG')return '<span class="badge bl">LONG</span>'
  if(a==='SHORT')return '<span class="badge bs">SHORT</span>'
  if(a==='FUNDING_SHORT')return '<span class="badge bf">FUND</span>'
  return '<span class="badge">'+a+'</span>'
}
function sc2(s){return s>=85?'sc h':s>=70?'sc m':'sc l'}

async function refresh(){
  try{
    var r=await fetch('/api/state'); var d=await r.json()
    g('ts').textContent='Updated: '+new Date().toLocaleTimeString()
    var demo=d.trader&&d.trader.demo_mode
    var mb=g('mode'); mb.textContent=demo?'🟡 DEMO MODE':'🔴 REAL TRADING'
    mb.className=demo?'mode-d':'mode-r'

    // FIX: pass ages directly — dot() handles 9999 as offline
    dot('d1',d.scanner_age,120); dot('d2',d.filter_age,200); dot('d3',d.trader_age,90)
    g('a1').textContent=ageStr(d.scanner_age)
    g('a2').textContent=ageStr(d.filter_age)
    g('a3').textContent=ageStr(d.trader_age)
    g('c1').textContent=(d.scanner&&d.scanner.symbols_scanned||0)+' coins'
    g('c2').textContent=(d.filter&&d.filter.total_passed||0)+' passed'
    g('c3').textContent=(d.trader&&d.trader.open_positions&&d.trader.open_positions.length||0)+' open'

    var sum=d.results&&d.results.summary||{}
    var wr=+sum.win_rate||0; g('wr').textContent=f(wr,1)+'%'
    g('wr').style.color=wr>=55?'#10b981':wr>=45?'#f59e0b':'#ef4444'
    var pnl=+sum.total_pnl_usd||0; g('pnl').textContent='$'+f(pnl); sc(g('pnl'),pnl)
    g('tt').textContent=sum.total||0; g('ws').textContent=sum.wins||0; g('ls').textContent=sum.losses||0
    g('op').textContent=d.trader&&d.trader.open_positions&&d.trader.open_positions.length||0
    var rg=d.scanner&&d.scanner.regime||'—'; g('rg').textContent=rg
    g('rg').style.color=rg==='BULL'?'#10b981':rg==='BEAR'?'#ef4444':'#f59e0b'
    var fg=d.scanner&&d.scanner.fear_greed||50; g('fg').textContent=fg+'/100'
    g('fg').style.color=fg<30?'#ef4444':fg<45?'#f59e0b':fg>75?'#ef4444':'#10b981'

    var ops=d.trader&&d.trader.open_positions||[]; var oe=g('ops')
    oe.innerHTML=ops.length?ops.map(function(p){
      var pc=+p.pnl_pct||0; var col=pc>0?'#10b981':'#ef4444'
      return '<div class="opp"><div class="'+sc2(p.filter_score||0)+'">'+
        (p.filter_score||'?')+'</div><div class="om">'+
        '<div class="osym">'+ab(p.action)+' '+p.symbol+'</div>'+
        '<div class="ostrat">'+p.strategy+'</div>'+
        '<div class="oreason">Entry $'+f(p.entry_price,6)+'</div></div>'+
        '<div class="or"><div style="color:'+col+';font-weight:700">'+fp(pc)+'</div>'+
        '<div style="color:'+col+';font-size:11px">$'+f(p.pnl_usd,4)+'</div></div></div>'
    }).join(''):'<div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">No open positions</div>'

    var sigs=d.filter&&d.filter.signals||[]; var se=g('sigs')
    se.innerHTML=sigs.length?sigs.map(function(s){
      return '<div class="opp"><div class="'+sc2(s.filter_score||0)+'">'+
        (s.filter_score||0)+'</div><div class="om">'+
        '<div class="osym">'+ab(s.action)+' '+s.symbol+'</div>'+
        '<div class="ostrat">'+s.strategy+'</div>'+
        '<div class="oreason">'+(s.reason||'')+'</div></div>'+
        '<div class="or"><div style="font-size:13px;font-weight:700">'+f(s.rr_ratio||1,1)+'x</div>'+
        '<div style="color:#4b5563;font-size:10px">$'+f(s.price,6)+'</div></div></div>'
    }).join(''):'<div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">No signals this cycle</div>'

    var opps=d.scanner&&d.scanner.opportunities&&d.scanner.opportunities.slice(0,8)||[]
    var scane=g('scan')
    scane.innerHTML=opps.length?opps.map(function(o){
      return '<div class="opp"><div class="'+sc2(o.score||0)+'">'+
        (o.score||0)+'</div><div class="om">'+
        '<div class="osym">'+ab(o.action)+' '+o.symbol+'</div>'+
        '<div class="ostrat">'+o.strategy+'</div>'+
        '<div class="oreason">'+(o.reason||'')+'</div></div>'+
        '<div class="or"><div style="color:#4b5563;font-size:11px">RSI '+f(o.rsi,0)+'</div>'+
        '<div style="color:#4b5563;font-size:10px">'+(o.change>=0?'+':'')+f(o.change,1)+'%</div></div></div>'
    }).join(''):'<div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">Scanning...</div>'

    var fr=d.scanner&&d.scanner.funding_rates||{}
    var fke=Object.keys(fr).sort(function(a,b){return fr[b]-fr[a]})
    var fe=g('fund')
    fe.innerHTML=fke.length?fke.slice(0,8).map(function(s){
      var rt=fr[s]; var ann=(rt*3*365*100).toFixed(0)
      var col=rt>0.001?'#10b981':rt>0.0005?'#f59e0b':'#e2e8f0'
      return '<div class="fund"><span style="font-size:12px;font-weight:600">'+s+'</span>'+
        '<div style="text-align:right"><div style="color:'+col+';font-size:12px;font-weight:700">'+
        (rt*100).toFixed(4)+'%/8h</div>'+
        '<div style="color:#4b5563;font-size:10px">~'+ann+'%/yr</div></div></div>'
    }).join(''):'<div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">No high funding rates</div>'

    var trs=d.results&&d.results.trades&&d.results.trades.slice(-15).reverse()||[]
    var te=g('trades')
    te.innerHTML=trs.length?trs.map(function(t){
      var col=t.pnl_pct>0?'#10b981':'#ef4444'
      return '<tr><td style="font-weight:600">'+t.symbol+'</td><td>'+ab(t.action)+'</td>'+
        '<td style="color:#4b5563">'+t.strategy+'</td>'+
        '<td>$'+f(t.entry_price,6)+'</td><td>$'+f(t.exit_price,6)+'</td>'+
        '<td style="color:'+col+'">'+fp(t.pnl_pct)+'</td>'+
        '<td style="color:'+col+'">$'+f(t.pnl_usd,4)+'</td>'+
        '<td style="color:#7c3aed">'+Math.round(t.filter_score||0)+'</td>'+
        '<td><span class="badge '+(t.outcome==='WIN'?'bw':'blo')+'">'+t.outcome+'</span></td>'+
        '<td style="color:#4b5563;font-size:10px">'+(t.close_reason||'').slice(0,30)+'</td></tr>'
    }).join(''):'<tr><td colspan="10" style="text-align:center;color:#4b5563;padding:16px">No closed trades yet</td></tr>'

    var stm={}
    trs.forEach(function(t){
      if(!stm[t.strategy])stm[t.strategy]={w:0,l:0,p:0}
      if(t.outcome==='WIN')stm[t.strategy].w++; else stm[t.strategy].l++
      stm[t.strategy].p+=(+t.pnl_usd||0)
    })
    var ste=g('strats')
    ste.innerHTML=Object.keys(stm).length?Object.entries(stm).sort(function(a,b){
      return(b[1].w/(b[1].w+b[1].l))-(a[1].w/(a[1].w+a[1].l))
    }).map(function(e){
      var s=e[0],d2=e[1]; var tot=d2.w+d2.l; var wr2=tot>0?Math.round(d2.w/tot*100):0
      var col=wr2>=60?'#10b981':wr2>=45?'#f59e0b':'#ef4444'
      return '<div style="margin-bottom:10px">'+
        '<div style="display:flex;justify-content:space-between">'+
        '<span style="font-size:11px;font-weight:600">'+s+'</span>'+
        '<div><span style="color:'+col+';font-size:12px;font-weight:700">'+wr2+'%</span>'+
        '<span style="color:#4b5563;font-size:10px;margin-left:8px">'+d2.w+'W/'+d2.l+'L</span>'+
        '<span style="color:'+(d2.p>=0?'#10b981':'#ef4444')+';font-size:10px;margin-left:8px">$'+f(d2.p,2)+'</span></div></div>'+
        '<div class="bar-w"><div class="bar-f" style="width:'+wr2+'%;background:'+col+'"></div></div></div>'
    }).join(''):'<div style="color:#4b5563;font-size:12px;text-align:center;padding:20px">Need 3+ trades per strategy</div>'

    var at=d.results&&d.results.trades&&d.results.trades.slice(-30)||[]
    var ce=g('chart')
    if(at.length){
      var mx=Math.max.apply(null,at.map(function(t){return Math.abs(t.pnl_usd||0)}).concat([0.01]))
      ce.innerHTML=at.map(function(t){
        var v=+t.pnl_usd||0; var h=Math.max(4,Math.round(Math.abs(v)/mx*56))
        return '<div style="flex:1;height:'+h+'px;border-radius:2px 2px 0 0;background:'+(v>=0?'#10b981':'#ef4444')+'" title="'+t.symbol+' '+fp(t.pnl_pct)+'"></div>'
      }).join('')
    }else{ce.innerHTML='<div style="color:#4b5563;font-size:11px;margin:auto">No trades yet</div>'}
  }catch(e){console.error('Dashboard error:',e)}
}
refresh(); setInterval(refresh,10000)
</script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(DASHBOARD)

@app.route('/api/state')
def api_state():
    scanner, sa = fetch(SCANNER_URL) if SCANNER_URL else ({}, 9999)
    filt,    fa = fetch(FILTER_URL)  if FILTER_URL  else ({}, 9999)
    trader,  ta = fetch(TRADER_URL)  if TRADER_URL  else ({}, 9999)
    results, _  = fetch(TRADER_URL, '/api/results') if TRADER_URL else ({}, 9999)
    return jsonify({
        'scanner': scanner, 'filter': filt, 'trader': trader, 'results': results,
        'scanner_age': sa, 'filter_age': fa, 'trader_age': ta,
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"Dashboard on port {port}")
    print(f"SCANNER_URL: {SCANNER_URL or 'NOT SET'}")
    print(f"FILTER_URL:  {FILTER_URL or 'NOT SET'}")
    print(f"TRADER_URL:  {TRADER_URL or 'NOT SET'}")
    app.run(host='0.0.0.0', port=port, debug=False)
