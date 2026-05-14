"""
╔══════════════════════════════════════════════════════════════════╗
║           WARFARE DASHBOARD — Main Entry Point                   ║
║  Reads state from all 3 services and shows unified dashboard.    ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, json, time
from datetime import datetime
from flask import Flask, jsonify, render_template_string

app  = Flask(__name__)

SCANNER_FILE = '/app/data/scanner_state.json'
SIGNALS_FILE = '/app/data/filtered_signals.json'
TRADER_FILE  = '/app/data/trader_state.json'
RESULTS_FILE = '/app/data/trade_results.json'

def read_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return default or {}

def file_age_seconds(path):
    try:
        return int(time.time() - os.path.getmtime(path))
    except Exception:
        return 9999

# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>⚔️ Warfare System</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#07090f;--card:#0e1117;--border:#1a1f2e;--text:#e2e8f0;
  --muted:#4b5563;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;
  --purple:#7c3aed;--blue:#3b82f6;--teal:#14b8a6;
}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;
     min-height:100vh;padding:16px}
/* HEADER */
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;
        padding:16px 20px;background:var(--card);border:1px solid var(--border);border-radius:14px}
.header h1{font-size:20px;font-weight:700;color:var(--purple)}
.header h1 span{color:var(--text)}
.ts{font-size:11px;color:var(--muted)}
/* SERVICE STATUS BAR */
.services{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}
.svc{background:var(--card);border:1px solid var(--border);border-radius:10px;
     padding:14px;display:flex;align-items:center;gap:12px}
.svc-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.svc-dot.green{background:var(--green);box-shadow:0 0 8px var(--green)}
.svc-dot.red{background:var(--red);box-shadow:0 0 8px var(--red)}
.svc-dot.yellow{background:var(--yellow);box-shadow:0 0 8px var(--yellow)}
.svc-info{flex:1;min-width:0}
.svc-name{font-size:12px;font-weight:600;color:var(--text)}
.svc-age{font-size:10px;color:var(--muted);margin-top:2px}
/* STATS GRID */
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;text-align:center}
.stat-num{font-size:28px;font-weight:700;margin:4px 0}
.stat-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.g{color:var(--green)}.r{color:var(--red)}.y{color:var(--yellow)}.p{color:var(--purple)}.b{color:var(--blue)}
/* PANELS */
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
@media(max-width:800px){.panels{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px}
.panel-title{font-size:13px;font-weight:700;color:var(--purple);margin-bottom:14px;
             display:flex;align-items:center;gap:8px}
/* TABLE */
table{width:100%;border-collapse:collapse;font-size:11px}
th{color:var(--muted);text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);
   font-size:10px;text-transform:uppercase;letter-spacing:.5px}
td{padding:6px 8px;border-bottom:1px solid #0e1117}
tr:last-child td{border:none}
.badge{padding:2px 8px;border-radius:6px;font-size:10px;font-weight:600}
.badge-long{background:#052e16;color:#34d399}
.badge-short{background:#3f1515;color:#f87171}
.badge-fund{background:#172554;color:#93c5fd}
.badge-win{background:#052e16;color:#34d399}
.badge-loss{background:#3f1515;color:#f87171}
.badge-open{background:#1c1917;color:#a8a29e}
/* OPPORTUNITIES */
.opp-grid{display:flex;flex-direction:column;gap:6px}
.opp-item{background:#0a0d14;border:1px solid var(--border);border-radius:8px;
          padding:10px 12px;display:flex;align-items:center;gap:10px}
.opp-score{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;
           justify-content:center;font-size:13px;font-weight:700;flex-shrink:0}
.score-high{background:#052e16;color:#34d399}
.score-mid{background:#422006;color:#fb923c}
.score-low{background:#1c1917;color:#a8a29e}
.opp-main{flex:1;min-width:0}
.opp-sym{font-size:13px;font-weight:600}
.opp-strat{font-size:10px;color:var(--muted);margin-top:1px}
.opp-reason{font-size:10px;color:#6b7280;margin-top:2px;white-space:nowrap;
            overflow:hidden;text-overflow:ellipsis}
.opp-right{text-align:right;flex-shrink:0}
/* MARKET REGIME */
.regime-bull{color:var(--green);font-weight:700}
.regime-bear{color:var(--red);font-weight:700}
.regime-neutral{color:var(--yellow);font-weight:700}
/* PROGRESS BAR */
.bar-wrap{background:#1a1f2e;border-radius:4px;height:6px;overflow:hidden;margin-top:4px}
.bar-fill{height:100%;border-radius:4px;transition:width .5s}
/* FUNDING */
.funding-item{display:flex;justify-content:space-between;align-items:center;
              padding:6px 0;border-bottom:1px solid #0e1117}
.funding-item:last-child{border:none}
/* DEMO BADGE */
.mode-demo{background:#1e3a5f;color:#60a5fa;padding:4px 12px;border-radius:20px;
           font-size:11px;font-weight:600}
.mode-real{background:#3f1515;color:#f87171;padding:4px 12px;border-radius:20px;
           font-size:11px;font-weight:600}
/* CHART PLACEHOLDER */
.pnl-history{display:flex;align-items:flex-end;gap:3px;height:60px;margin-top:8px}
.pnl-bar{flex:1;border-radius:2px 2px 0 0;min-height:2px;transition:height .3s}
.pnl-bar.pos{background:var(--green)}
.pnl-bar.neg{background:var(--red)}
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div>
    <h1>⚔️ Warfare <span>Trading System</span></h1>
    <div class="ts" id="ts">Loading...</div>
  </div>
  <div id="mode-badge" class="mode-demo">🟡 DEMO MODE</div>
</div>

<!-- SERVICE STATUS -->
<div class="services">
  <div class="svc">
    <div class="svc-dot" id="scanner-dot"></div>
    <div class="svc-info">
      <div class="svc-name">📡 Scanner</div>
      <div class="svc-age" id="scanner-age">—</div>
    </div>
    <div style="font-size:11px;color:var(--muted)" id="scanner-count">—</div>
  </div>
  <div class="svc">
    <div class="svc-dot" id="filter-dot"></div>
    <div class="svc-info">
      <div class="svc-name">🔬 Filter AI</div>
      <div class="svc-age" id="filter-age">—</div>
    </div>
    <div style="font-size:11px;color:var(--muted)" id="filter-passed">—</div>
  </div>
  <div class="svc">
    <div class="svc-dot" id="trader-dot"></div>
    <div class="svc-info">
      <div class="svc-name">⚡ Trader</div>
      <div class="svc-age" id="trader-age">—</div>
    </div>
    <div style="font-size:11px;color:var(--muted)" id="open-count">—</div>
  </div>
</div>

<!-- PERFORMANCE STATS -->
<div class="stats">
  <div class="stat">
    <div class="stat-num g" id="win-rate">0%</div>
    <div class="stat-lbl">Win Rate</div>
  </div>
  <div class="stat">
    <div class="stat-num" id="total-pnl">$0.00</div>
    <div class="stat-lbl">Total P&L</div>
  </div>
  <div class="stat">
    <div class="stat-num p" id="total-trades">0</div>
    <div class="stat-lbl">Closed Trades</div>
  </div>
  <div class="stat">
    <div class="stat-num g" id="wins">0</div>
    <div class="stat-lbl">Wins</div>
  </div>
  <div class="stat">
    <div class="stat-num r" id="losses">0</div>
    <div class="stat-lbl">Losses</div>
  </div>
  <div class="stat">
    <div class="stat-num y" id="open-pos">0</div>
    <div class="stat-lbl">Open Now</div>
  </div>
  <div class="stat">
    <div class="stat-num b" id="regime">—</div>
    <div class="stat-lbl">Market Regime</div>
  </div>
  <div class="stat">
    <div class="stat-num" id="fear-greed">—</div>
    <div class="stat-lbl">Fear & Greed</div>
  </div>
</div>

<!-- MAIN PANELS -->
<div class="panels">

  <!-- LEFT: Open Positions -->
  <div class="panel">
    <div class="panel-title">⚡ Open Positions</div>
    <div id="open-positions-list">
      <div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">
        No open positions
      </div>
    </div>
  </div>

  <!-- RIGHT: Top Signals from Filter -->
  <div class="panel">
    <div class="panel-title">🔬 Latest Filtered Signals</div>
    <div class="opp-grid" id="signals-list">
      <div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">
        Waiting for signals...
      </div>
    </div>
  </div>

  <!-- LEFT: Scanner Opportunities -->
  <div class="panel">
    <div class="panel-title">📡 Scanner — Top Opportunities</div>
    <div class="opp-grid" id="scanner-opps">
      <div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">
        Waiting for scanner...
      </div>
    </div>
  </div>

  <!-- RIGHT: Funding Rates -->
  <div class="panel">
    <div class="panel-title">💰 High Funding Rates (passive income)</div>
    <div id="funding-list">
      <div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">
        Scanning...
      </div>
    </div>
  </div>

</div>

<!-- RECENT TRADES TABLE -->
<div class="panel" style="margin-bottom:20px">
  <div class="panel-title">📊 Recent Closed Trades</div>
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>Action</th><th>Strategy</th>
        <th>Entry</th><th>Exit</th><th>P&L %</th><th>P&L $</th>
        <th>Score</th><th>R:R</th><th>Outcome</th><th>Reason</th>
      </tr>
    </thead>
    <tbody id="recent-trades"></tbody>
  </table>
</div>

<!-- STRATEGY PERFORMANCE -->
<div class="panels">
  <div class="panel">
    <div class="panel-title">🏆 Strategy Win Rates</div>
    <div id="strategy-stats">
      <div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">
        Needs 3+ closed trades per strategy
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">📈 P&L History</div>
    <div class="pnl-history" id="pnl-chart"></div>
    <div style="display:flex;justify-content:space-between;margin-top:6px">
      <span style="font-size:10px;color:var(--muted)">Oldest</span>
      <span style="font-size:10px;color:var(--muted)">Latest</span>
    </div>
  </div>
</div>

<script>
function fmt(n,d=2){return(+n||0).toFixed(d)}
function fmtPct(n){var v=+n||0;return(v>=0?'+':'')+v.toFixed(2)+'%'}
function el(id){return document.getElementById(id)}
function setColor(el,val){
  el.style.color=val>0?'var(--green)':val<0?'var(--red)':'var(--yellow)'
}

function dot(id, age, threshold){
  var d=el(id)
  if(age<threshold){d.className='svc-dot green'}
  else if(age<threshold*3){d.className='svc-dot yellow'}
  else{d.className='svc-dot red'}
}

function actionBadge(a){
  if(a==='LONG')return '<span class="badge badge-long">LONG</span>'
  if(a==='SHORT')return '<span class="badge badge-short">SHORT</span>'
  if(a==='FUNDING_SHORT')return '<span class="badge badge-fund">FUND</span>'
  return '<span class="badge badge-open">'+a+'</span>'
}

function scoreColor(s){
  if(s>=85)return 'score-high'
  if(s>=70)return 'score-mid'
  return 'score-low'
}

async function refresh(){
  try{
    var r=await fetch('/api/state')
    var d=await r.json()
    var ts=new Date().toLocaleTimeString()
    el('ts').textContent='Last update: '+ts

    // Mode badge
    var mb=el('mode-badge')
    if(d.trader&&!d.trader.demo_mode){
      mb.textContent='🔴 REAL TRADING'; mb.className='mode-real'
    } else {
      mb.textContent='🟡 DEMO MODE'; mb.className='mode-demo'
    }

    // ── Service status ────────────────────────────────────────────
    var sa=d.scanner_age||9999, fa=d.filter_age||9999, ta=d.trader_age||9999
    dot('scanner-dot',sa,120); dot('filter-dot',fa,200); dot('trader-dot',ta,90)
    el('scanner-age').textContent=sa<9999?sa+'s ago':'offline'
    el('filter-age').textContent=fa<9999?fa+'s ago':'offline'
    el('trader-age').textContent=ta<9999?ta+'s ago':'offline'
    el('scanner-count').textContent=(d.scanner?.symbols_scanned||0)+' coins'
    el('filter-passed').textContent=(d.filter?.total_passed||0)+' passed'
    el('open-count').textContent=(d.trader?.open_positions?.length||0)+' open'

    // ── Stats ─────────────────────────────────────────────────────
    var sum=d.results?.summary||{}
    var wr=sum.win_rate||0
    el('win-rate').textContent=fmt(wr,1)+'%'
    el('win-rate').style.color=wr>=55?'var(--green)':wr>=45?'var(--yellow)':'var(--red)'
    var pnl=sum.total_pnl_usd||0
    el('total-pnl').textContent='$'+fmt(pnl)
    setColor(el('total-pnl'),pnl)
    el('total-trades').textContent=sum.total||0
    el('wins').textContent=sum.wins||0
    el('losses').textContent=sum.losses||0
    el('open-pos').textContent=d.trader?.open_positions?.length||0

    var regime=d.scanner?.regime||'—'
    var re=el('regime')
    re.textContent=regime
    re.className='stat-num'+(regime==='BULL'?' g':regime==='BEAR'?' r':' y')

    var fg=d.scanner?.fear_greed||50
    var fge=el('fear-greed')
    fge.textContent=fg+'/100'
    fge.style.color=fg<30?'var(--red)':fg<45?'var(--yellow)':fg>75?'var(--red)':'var(--green)'

    // ── Open positions ────────────────────────────────────────────
    var ops=d.trader?.open_positions||[]
    var opEl=el('open-positions-list')
    if(ops.length===0){
      opEl.innerHTML='<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">No open positions</div>'
    } else {
      opEl.innerHTML=ops.map(function(p){
        var pnlPct=+p.pnl_pct||0
        var pnlUsd=+p.pnl_usd||0
        var pnlCol=pnlPct>0?'var(--green)':'var(--red)'
        return '<div class="opp-item">'+
          '<div class="opp-score '+scoreColor(p.filter_score||0)+'">'+
            (p.filter_score||'?')+
          '</div>'+
          '<div class="opp-main">'+
            '<div class="opp-sym">'+actionBadge(p.action)+' '+p.symbol+'</div>'+
            '<div class="opp-strat">'+p.strategy+'</div>'+
            '<div class="opp-reason">Entry $'+fmt(p.entry_price,6)+'</div>'+
          '</div>'+
          '<div class="opp-right">'+
            '<div style="color:'+pnlCol+';font-weight:700;font-size:13px">'+fmtPct(pnlPct)+'</div>'+
            '<div style="color:'+pnlCol+';font-size:11px">$'+fmt(pnlUsd,4)+'</div>'+
            '<div style="color:var(--muted);font-size:10px">peak '+fmtPct(p.peak_pnl_pct||0)+'</div>'+
          '</div>'+
        '</div>'
      }).join('')
    }

    // ── Filtered signals ──────────────────────────────────────────
    var sigs=d.filter?.signals||[]
    var sigEl=el('signals-list')
    if(sigs.length===0){
      sigEl.innerHTML='<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">No signals this cycle</div>'
    } else {
      sigEl.innerHTML=sigs.map(function(s){
        return '<div class="opp-item">'+
          '<div class="opp-score '+scoreColor(s.filter_score||0)+'">'+
            (s.filter_score||0)+
          '</div>'+
          '<div class="opp-main">'+
            '<div class="opp-sym">'+actionBadge(s.action)+' '+s.symbol+'</div>'+
            '<div class="opp-strat">'+s.strategy+'</div>'+
            '<div class="opp-reason">'+s.reason+'</div>'+
          '</div>'+
          '<div class="opp-right">'+
            '<div style="color:var(--text);font-weight:700;font-size:13px">'+fmt(s.rr_ratio||1,1)+'x R:R</div>'+
            '<div style="color:var(--muted);font-size:10px">$'+fmt(s.price||0,6)+'</div>'+
          '</div>'+
        '</div>'
      }).join('')
    }

    // ── Scanner opportunities ─────────────────────────────────────
    var opps=(d.scanner?.opportunities||[]).slice(0,8)
    var scEl=el('scanner-opps')
    if(opps.length===0){
      scEl.innerHTML='<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">Scanning...</div>'
    } else {
      scEl.innerHTML=opps.map(function(o){
        return '<div class="opp-item">'+
          '<div class="opp-score '+scoreColor(o.score||0)+'">'+
            (o.score||0)+
          '</div>'+
          '<div class="opp-main">'+
            '<div class="opp-sym">'+actionBadge(o.action)+' '+o.symbol+'</div>'+
            '<div class="opp-strat">'+o.strategy+'</div>'+
            '<div class="opp-reason">'+o.reason+'</div>'+
          '</div>'+
          '<div class="opp-right">'+
            '<div style="color:var(--muted);font-size:11px">RSI '+fmt(o.rsi||0,0)+'</div>'+
            '<div style="color:var(--muted);font-size:10px">'+fmtPct(o.change||0)+'</div>'+
          '</div>'+
        '</div>'
      }).join('')
    }

    // ── Funding rates ─────────────────────────────────────────────
    var funding=d.scanner?.funding_rates||{}
    var fundEl=el('funding-list')
    var fundKeys=Object.keys(funding).sort(function(a,b){return funding[b]-funding[a]})
    if(fundKeys.length===0){
      fundEl.innerHTML='<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">No high funding rates detected</div>'
    } else {
      fundEl.innerHTML=fundKeys.slice(0,8).map(function(sym){
        var rate=funding[sym]
        var annual=(rate*3*365*100).toFixed(0)
        var col=rate>0.001?'var(--green)':rate>0.0005?'var(--yellow)':'var(--text)'
        return '<div class="funding-item">'+
          '<span style="font-size:12px;font-weight:600">'+sym+'</span>'+
          '<div style="text-align:right">'+
            '<div style="color:'+col+';font-size:12px;font-weight:700">'+
              (rate*100).toFixed(4)+'%/8h</div>'+
            '<div style="color:var(--muted);font-size:10px">~'+annual+'%/yr</div>'+
          '</div>'+
        '</div>'
      }).join('')
    }

    // ── Recent trades ─────────────────────────────────────────────
    var trades=(d.results?.trades||[]).slice(-15).reverse()
    var tbody=el('recent-trades')
    if(trades.length===0){
      tbody.innerHTML='<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:16px">No closed trades yet</td></tr>'
    } else {
      tbody.innerHTML=trades.map(function(t){
        var pnlCol=t.pnl_pct>0?'var(--green)':'var(--red)'
        var outBadge=t.outcome==='WIN'?
          '<span class="badge badge-win">WIN</span>':
          '<span class="badge badge-loss">LOSS</span>'
        return '<tr>'+
          '<td style="font-weight:600">'+t.symbol+'</td>'+
          '<td>'+actionBadge(t.action)+'</td>'+
          '<td style="color:var(--muted)">'+t.strategy+'</td>'+
          '<td>$'+fmt(t.entry_price,6)+'</td>'+
          '<td>$'+fmt(t.exit_price,6)+'</td>'+
          '<td style="color:'+pnlCol+'">'+fmtPct(t.pnl_pct)+'</td>'+
          '<td style="color:'+pnlCol+'">$'+fmt(t.pnl_usd,4)+'</td>'+
          '<td style="color:var(--purple)">'+Math.round(t.filter_score||0)+'</td>'+
          '<td style="color:var(--muted)">'+fmt(t.rr_ratio||1,1)+'x</td>'+
          '<td>'+outBadge+'</td>'+
          '<td style="color:var(--muted);font-size:10px">'+
            (t.close_reason||'').slice(0,30)+'</td>'+
        '</tr>'
      }).join('')
    }

    // ── Strategy win rates ────────────────────────────────────────
    var strats={}
    trades.forEach(function(t){
      if(!strats[t.strategy]) strats[t.strategy]={w:0,l:0,pnl:0}
      if(t.outcome==='WIN') strats[t.strategy].w++
      else strats[t.strategy].l++
      strats[t.strategy].pnl+=(+t.pnl_usd||0)
    })
    var stratEl=el('strategy-stats')
    var stratKeys=Object.keys(strats)
    if(stratKeys.length===0){
      stratEl.innerHTML='<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">Needs 3+ trades per strategy</div>'
    } else {
      stratEl.innerHTML=stratKeys.sort(function(a,b){
        var awa=strats[a].w/(strats[a].w+strats[a].l)||0
        var bwr=strats[b].w/(strats[b].w+strats[b].l)||0
        return bwr-awa
      }).map(function(s){
        var sd=strats[s]; var total=sd.w+sd.l
        var wr=total>0?Math.round(sd.w/total*100):0
        var col=wr>=60?'var(--green)':wr>=45?'var(--yellow)':'var(--red)'
        var pnlCol=sd.pnl>=0?'var(--green)':'var(--red)'
        return '<div style="margin-bottom:10px">'+
          '<div style="display:flex;justify-content:space-between;align-items:center">'+
            '<span style="font-size:11px;font-weight:600">'+s+'</span>'+
            '<div style="text-align:right">'+
              '<span style="color:'+col+';font-size:12px;font-weight:700">'+wr+'%</span>'+
              '<span style="color:var(--muted);font-size:10px;margin-left:8px">'+
                sd.w+'W/'+sd.l+'L</span>'+
              '<span style="color:'+pnlCol+';font-size:10px;margin-left:8px">$'+
                fmt(sd.pnl,2)+'</span>'+
            '</div>'+
          '</div>'+
          '<div class="bar-wrap"><div class="bar-fill" style="width:'+wr+'%;background:'+col+'"></div></div>'+
        '</div>'
      }).join('')
    }

    // ── P&L chart ─────────────────────────────────────────────────
    var allTrades=(d.results?.trades||[]).slice(-30)
    var chartEl=el('pnl-chart')
    if(allTrades.length>0){
      var maxAbs=Math.max(...allTrades.map(function(t){return Math.abs(t.pnl_usd||0)}),0.01)
      chartEl.innerHTML=allTrades.map(function(t){
        var v=+t.pnl_usd||0
        var h=Math.max(4,Math.round(Math.abs(v)/maxAbs*56))
        return '<div class="pnl-bar '+(v>=0?'pos':'neg')+'" style="height:'+h+'px" '+
               'title="'+t.symbol+' '+fmtPct(t.pnl_pct)+'"></div>'
      }).join('')
    } else {
      chartEl.innerHTML='<div style="color:var(--muted);font-size:11px;margin:auto">No trades yet</div>'
    }

  } catch(e){
    console.error('Dashboard error:',e)
  }
}

// Auto-refresh every 10 seconds
refresh()
setInterval(refresh, 10000)
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(DASHBOARD)

@app.route('/api/state')
def api_state():
    scanner = read_json(SCANNER_FILE)
    signals = read_json(SIGNALS_FILE)
    trader  = read_json(TRADER_FILE)
    results = read_json(RESULTS_FILE)

    return jsonify({
        'scanner':     scanner,
        'filter':      signals,
        'trader':      trader,
        'results':     results,
        'scanner_age': file_age_seconds(SCANNER_FILE),
        'filter_age':  file_age_seconds(SIGNALS_FILE),
        'trader_age':  file_age_seconds(TRADER_FILE),
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'ts': datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
