#!/usr/bin/env python3
"""
web_server.py — NSE Intraday Dashboard
Runs as a Blueprint mounted at /nse inside app.py (port 8080).
Can also run standalone on port 8085 for development.

Routes (all prefixed with /nse when mounted):
  /  /bigplayer  /log  /log/download
  /api/summary  /api/bigplayer  /api/intraday/<sym>
"""

import sqlite3, os, datetime
from flask import Blueprint, Flask, jsonify, render_template_string, request, send_file, Response, current_app
import pytz

LOG_FILE = "/home/ramesh/log/newtrade.log"

# Blueprint — registered in app.py with url_prefix='/nse'
nse_bp = Blueprint('nse', __name__)

DB   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nse_intraday.db")
IST  = pytz.timezone("Asia/Kolkata")
PORT = 8080


def get_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def today_ist():
    return datetime.datetime.now(IST).strftime('%Y-%m-%d')

def is_market_open():
    n = datetime.datetime.now(IST)
    return (9, 15) <= (n.hour, n.minute) <= (15, 35) and n.weekday() < 5


# ── API ────────────────────────────────────────────────────────────────────────

@nse_bp.route('/api/summary')
def api_summary():
    today = today_ist()
    conn  = get_conn()

    mkt = conn.execute('''
        SELECT COUNT(DISTINCT symbol) as symbols,
               ROUND(SUM(ac), 2)                                              as total_cr,
               ROUND(SUM(CASE WHEN bs=1  AND ac>=1 THEN ac ELSE 0 END), 2)   as big_buy_cr,
               ROUND(SUM(CASE WHEN bs=-1 AND ac>=1 THEN ac ELSE 0 END), 2)   as big_sell_cr,
               MAX(t)                                                          as last_bar
        FROM bars WHERE DATE(t) = ?
    ''', (today,)).fetchone()

    syms = conn.execute('''
        SELECT b.symbol,
               si.name, si.sector, si.pe_ratio, si.market_cap,
               si.week52_high, si.week52_low, si.avg_vol_3m, si.avg_vol_10d,
               ROUND(MIN(b.o), 2)  as day_open,
               ROUND(MAX(b.h), 2)  as day_high,
               ROUND(MIN(b.l), 2)  as day_low,
               (SELECT ROUND(c,2) FROM bars b2
                WHERE b2.symbol=b.symbol AND DATE(b2.t)=?
                ORDER BY b2.t DESC LIMIT 1)              as last_price,
               SUM(b.v)            as total_vol,
               ROUND(SUM(b.ac), 2) as total_cr,
               ROUND(SUM(CASE WHEN b.bs=1  AND b.ac>=1 THEN b.ac ELSE 0 END), 2) as big_buy_cr,
               ROUND(SUM(CASE WHEN b.bs=-1 AND b.ac>=1 THEN b.ac ELSE 0 END), 2) as big_sell_cr
        FROM bars b
        LEFT JOIN symbol_info si ON b.symbol = si.symbol
        WHERE DATE(b.t) = ?
        GROUP BY b.symbol
        ORDER BY total_cr DESC
    ''', (today, today)).fetchall()

    conn.close()
    return jsonify({
        'market':      dict(mkt) if mkt else {},
        'symbols':     [dict(r) for r in syms],
        'as_of':       datetime.datetime.now(IST).isoformat(),
        'market_open': is_market_open(),
    })


@nse_bp.route('/api/bigplayer')
def api_bigplayer():
    today = today_ist()
    conn  = get_conn()

    # Last 10 distinct trading dates (excluding today)
    hist_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT DATE(t) FROM bars WHERE DATE(t)<? ORDER BY DATE(t) DESC LIMIT 10",
        (today,)
    ).fetchall()]
    hist_days = len(hist_dates)

    # Today's big-player summary per symbol
    today_bp = conn.execute('''
        SELECT symbol,
               COUNT(*)                                                             as big_bars,
               SUM(CASE WHEN bs=1  THEN 1 ELSE 0 END)                             as buy_bars,
               SUM(CASE WHEN bs=-1 THEN 1 ELSE 0 END)                             as sell_bars,
               ROUND(SUM(CASE WHEN bs=1  THEN ac ELSE 0 END), 2)                  as buy_cr,
               ROUND(SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END), 2)                  as sell_cr,
               ROUND(SUM(ac), 2)                                                   as total_cr,
               ROUND(SUM(CASE WHEN bs=1 THEN ac ELSE 0 END)
                   - SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END), 2)                  as net_cr
        FROM bars WHERE DATE(t)=? AND ac>=1
        GROUP BY symbol ORDER BY total_cr DESC
    ''', (today,)).fetchall()

    # 10-day per-symbol averages
    avg10 = {}
    if hist_dates:
        ph = ','.join('?' * len(hist_dates))
        for r in conn.execute(f'''
            SELECT symbol,
                   ROUND(AVG(d_buy),   2) as avg_buy,
                   ROUND(AVG(d_sell),  2) as avg_sell,
                   ROUND(AVG(d_total), 2) as avg_total
            FROM (
                SELECT symbol, DATE(t) as dt,
                       SUM(CASE WHEN bs=1  THEN ac ELSE 0 END) as d_buy,
                       SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END) as d_sell,
                       SUM(ac)                                  as d_total
                FROM bars WHERE ac>=1 AND DATE(t) IN ({ph})
                GROUP BY symbol, dt
            ) GROUP BY symbol
        ''', hist_dates).fetchall():
            avg10[r['symbol']] = dict(r)

    # Per-day history (for the 10-day grid / sparklines)
    hist = []
    if hist_dates:
        ph = ','.join('?' * len(hist_dates))
        hist = conn.execute(f'''
            SELECT symbol, DATE(t) as dt,
                   ROUND(SUM(CASE WHEN bs=1  THEN ac ELSE 0 END), 2) as buy_cr,
                   ROUND(SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END), 2) as sell_cr,
                   ROUND(SUM(ac), 2)                                   as total_cr
            FROM bars WHERE ac>=1 AND DATE(t) IN ({ph})
            GROUP BY symbol, dt ORDER BY symbol, dt
        ''', hist_dates).fetchall()

    # Intraday per-minute flow (all symbols, today)
    timeline = conn.execute('''
        SELECT t,
               ROUND(SUM(CASE WHEN bs=1  THEN ac ELSE 0 END), 2) as buy_cr,
               ROUND(SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END), 2) as sell_cr
        FROM bars WHERE DATE(t)=? AND ac>=1
        GROUP BY t ORDER BY t
    ''', (today,)).fetchall()

    # Cumulative net flow per symbol (top 15)
    top15 = [r['symbol'] for r in today_bp[:15]]
    cum_flows = {}
    for sym in top15:
        rows = conn.execute('''
            SELECT t, ROUND(bs * ac, 4) as net_bar
            FROM bars WHERE symbol=? AND DATE(t)=? AND ac>=1 ORDER BY t
        ''', (sym, today)).fetchall()
        cum, pts = 0.0, []
        for r in rows:
            cum = round(cum + r['net_bar'], 2)
            pts.append([r['t'], cum])
        cum_flows[sym] = pts

    conn.close()

    result = []
    for r in today_bp:
        d = dict(r)
        a = avg10.get(d['symbol'], {})
        d['avg_buy_10d']   = a.get('avg_buy')
        d['avg_sell_10d']  = a.get('avg_sell')
        d['avg_total_10d'] = a.get('avg_total')
        avg_t = a.get('avg_total')
        d['spike_ratio'] = round(d['total_cr'] / avg_t, 2) if avg_t and avg_t > 0 else None
        result.append(d)

    return jsonify({
        'today':       result,
        'history':     [dict(r) for r in hist],
        'timeline':    [dict(r) for r in timeline],
        'cum_flows':   cum_flows,
        'hist_days':   hist_days,
        'hist_dates':  hist_dates,
        'today_date':  today,
        'as_of':       datetime.datetime.now(IST).isoformat(),
        'market_open': is_market_open(),
    })


@nse_bp.route('/api/intraday/<symbol>')
def api_intraday(symbol):
    today = today_ist()
    conn  = get_conn()
    bars  = conn.execute(
        "SELECT t, o, h, l, c, v, ROUND(ac,4) as ac, bs "
        "FROM bars WHERE symbol=? AND DATE(t)=? ORDER BY t",
        (symbol.upper(), today)
    ).fetchall()
    conn.close()
    return jsonify({'symbol': symbol.upper(), 'bars': [dict(r) for r in bars]})


# ── Shared CSS ─────────────────────────────────────────────────────────────────

_CSS = """
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--bd:#30363d;
      --tx:#e6edf3;--sub:#8b949e;--grn:#3fb950;--red:#f85149;
      --yel:#d29922;--blu:#58a6ff;--pur:#bc8cff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
     font-size:13px;line-height:1.5}
a{color:var(--blu);text-decoration:none}a:hover{text-decoration:underline}
nav{display:flex;align-items:center;gap:16px;padding:10px 20px;
    background:var(--bg2);border-bottom:1px solid var(--bd);
    position:sticky;top:0;z-index:100}
.brand{font-size:15px;font-weight:700}
.nav-links{display:flex;gap:4px}
.nav-links a{padding:5px 12px;border-radius:6px;color:var(--sub);transition:.15s}
.nav-links a:hover,.nav-links a.on{background:var(--bg3);color:var(--tx);text-decoration:none}
.nav-r{margin-left:auto;display:flex;align-items:center;gap:12px;
       color:var(--sub);font-size:12px}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;
       border-radius:12px;font-size:11px;font-weight:600}
.live{background:#1a3a1a;color:var(--grn);border:1px solid #2d5a2d}
.closed{background:#2a1a1a;color:var(--red);border:1px solid #5a2d2d}
.dot{width:6px;height:6px;border-radius:50%;background:currentColor;
     animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
main{padding:16px 20px;max-width:1600px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
       gap:10px;margin-bottom:14px}
.card{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;
      padding:12px 14px}
.clbl{color:var(--sub);font-size:10px;font-weight:600;text-transform:uppercase;
      letter-spacing:.5px;margin-bottom:3px}
.cval{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.csub{color:var(--sub);font-size:11px;margin-top:1px}
.green{color:var(--grn)}.red{color:var(--red)}.blue{color:var(--blu)}
.yellow{color:var(--yel)}.sub{color:var(--sub)}
.sec{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;
     padding:14px;margin-bottom:14px}
.sec-title{font-size:13px;font-weight:600;margin-bottom:10px;
           display:flex;align-items:center;gap:6px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:860px){.grid2{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse}
th{background:var(--bg3);color:var(--sub);font-size:10px;font-weight:600;
   text-transform:uppercase;letter-spacing:.3px;padding:6px 9px;
   text-align:right;white-space:nowrap;cursor:pointer;user-select:none}
th:first-child{text-align:left}
th:hover{color:var(--tx)}
th.asc::after{content:' ↑'}th.desc::after{content:' ↓'}
td{padding:5px 9px;border-bottom:1px solid var(--bd);text-align:right;
   font-variant-numeric:tabular-nums;vertical-align:middle;font-size:12px}
td:first-child{text-align:left}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--bg3)}
.sym{font-weight:600;color:var(--blu);cursor:pointer;font-size:12px}
.sym:hover{text-decoration:underline}
.tag{display:inline-flex;padding:1px 6px;border-radius:10px;
     font-size:10px;font-weight:600}
.tbuy{background:#1a3a1a;color:var(--grn)}
.tsell{background:#3a1a1a;color:var(--red)}
.tneut{background:#2a2a2a;color:var(--sub)}
.spk-hi{background:#3a2000;color:#ff9500;border:1px solid #6b3d00;
        padding:1px 6px;border-radius:10px;font-size:10px;font-weight:700}
.spk-md{background:#2a2a00;color:var(--yel);border:1px solid #5a5a00;
        padding:1px 6px;border-radius:10px;font-size:10px;font-weight:700}
.banner{background:#0d1b2e;border:1px solid #1d3a5e;border-radius:8px;
        padding:8px 14px;margin-bottom:12px;color:var(--blu);font-size:12px;
        display:flex;align-items:center;gap:10px}
.prog-bg{flex:1;background:var(--bg3);border-radius:3px;height:5px;max-width:180px}
.prog-fill{height:100%;background:var(--blu);border-radius:3px}
.alerts{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.ac{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;
    padding:9px 13px;min-width:140px;cursor:pointer}
.ac:hover{border-color:var(--blu)}
.ac-buy{border-color:#2d5a2d}.ac-sell{border-color:#5a2d2d}
.split-wrap{display:flex;align-items:center;gap:4px}
.split-bg{background:var(--bg3);border-radius:3px;height:5px;width:80px;
          overflow:hidden;display:flex}
.sb{height:100%}.sb-b{background:var(--grn)}.sb-s{background:var(--red)}
input[type=text]{background:var(--bg3);border:1px solid var(--bd);
     border-radius:6px;color:var(--tx);padding:5px 10px;font-size:12px;
     outline:none;width:180px}
input[type=text]:focus{border-color:var(--blu)}
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);
    z-index:200;align-items:center;justify-content:center}
.ov.on{display:flex}
.modal{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;
       padding:18px;width:92%;max-width:820px;max-height:92vh;overflow-y:auto}
.mhdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.mx{cursor:pointer;color:var(--sub);font-size:20px;line-height:1;padding:4px}
.mx:hover{color:var(--tx)}
.mstats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}
"""

# ── Overview page ──────────────────────────────────────────────────────────────

_OV = '''<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE — Overview</title><style>''' + _CSS + '''</style></head>
<body>
<nav>
  <div class="brand">📈 NSE Dashboard</div>
  <div class="nav-links"><a href="{{ prefix }}/" class="on">Overview</a><a href="{{ prefix }}/bigplayer">⚡ Big Player</a><a href="{{ prefix }}/pushlog">🔔 Pushes</a><a href="{{ prefix }}/log">📋 Log</a></div>
  <div class="nav-r">
    <span id="mbadge"></span>
    <span id="asof" class="sub"></span>
    <span id="cd" class="sub"></span>
  </div>
</nav>
<main>
  <div class="cards">
    <div class="card"><div class="clbl">Symbols</div><div class="cval blue" id="c0">—</div></div>
    <div class="card"><div class="clbl">Total Turnover</div><div class="cval" id="c1">—</div><div class="csub">₹ Crores</div></div>
    <div class="card"><div class="clbl">Big Buy</div><div class="cval green" id="c2">—</div><div class="csub">1Cr+ buy bars</div></div>
    <div class="card"><div class="clbl">Big Sell</div><div class="cval red" id="c3">—</div><div class="csub">1Cr+ sell bars</div></div>
    <div class="card"><div class="clbl">Net Flow</div><div class="cval" id="c4">—</div><div class="csub">Buy − Sell ₹Cr</div></div>
  </div>
  <div class="sec">
    <div class="toolbar">
      <div class="sec-title" style="margin:0">All Symbols — Today</div>
      <input type="text" id="q" placeholder="Filter…" oninput="filt()">
    </div>
    <div style="overflow-x:auto">
    <table><thead><tr>
      <th onclick="srt(0)">Symbol</th>
      <th onclick="srt(1)">Name</th>
      <th onclick="srt(2)">Sector</th>
      <th onclick="srt(3)">Last ₹</th>
      <th onclick="srt(4)">Open</th>
      <th onclick="srt(5)">High</th>
      <th onclick="srt(6)">Low</th>
      <th onclick="srt(7)">Volume</th>
      <th onclick="srt(8)">Total Cr</th>
      <th onclick="srt(9)">Big Buy Cr</th>
      <th onclick="srt(10)">Big Sell Cr</th>
      <th onclick="srt(11)">Net Cr</th>
      <th onclick="srt(12)">Bias</th>
      <th onclick="srt(13)">52W H</th>
      <th onclick="srt(14)">52W L</th>
      <th onclick="srt(15)">PE</th>
    </tr></thead><tbody id="tb"></tbody></table>
    </div>
  </div>
</main>
<script>
const BASE="{{ prefix }}";
let all=[],sc=8,sa=false,cd=60;
const f=(v,d=2)=>v==null?'<span class="sub">—</span>':Number(v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const fv=v=>{if(!v)return'<span class="sub">—</span>';if(v>=1e7)return(v/1e7).toFixed(2)+'Cr';if(v>=1e5)return(v/1e5).toFixed(1)+'L';return v.toLocaleString('en-IN')};
const fc=v=>{if(v==null)return'<span class="sub">—</span>';const c=v>0?'green':v<0?'red':'sub';return`<span class="${c}">${v>0?'+':''}${f(v)}</span>`};

async function load(){
  const d=await fetch(BASE+'/api/summary').then(r=>r.json()).catch(()=>({}));
  const m=d.market||{};
  document.getElementById('c0').textContent=m.symbols||'—';
  document.getElementById('c1').textContent='₹'+f(m.total_cr);
  document.getElementById('c2').textContent='₹'+f(m.big_buy_cr);
  document.getElementById('c3').textContent='₹'+f(m.big_sell_cr);
  const net=(m.big_buy_cr||0)-(m.big_sell_cr||0);
  const n=document.getElementById('c4');
  n.className='cval '+(net>0?'green':net<0?'red':'sub');
  n.textContent=(net>0?'+':'')+f(net);
  document.getElementById('mbadge').innerHTML=d.market_open?'<span class="badge live"><span class="dot"></span>LIVE</span>':'<span class="badge closed">CLOSED</span>';
  document.getElementById('asof').textContent='Updated '+(d.as_of||'').substring(11,19)+' IST';
  all=d.symbols||[];filt();cd=60;
}
function filt(){
  const q=document.getElementById('q').value.toLowerCase();
  let rows=q?all.filter(r=>[(r.symbol||''),(r.name||''),(r.sector||'')].some(s=>s.toLowerCase().includes(q))):all;
  const keys=['symbol','name','sector','last_price','day_open','day_high','day_low','total_vol','total_cr','big_buy_cr','big_sell_cr','_net','_bias','week52_high','week52_low','pe_ratio'];
  rows=[...rows].sort((a,b)=>{
    let av,bv;
    if(keys[sc]==='_net'){av=(a.big_buy_cr||0)-(a.big_sell_cr||0);bv=(b.big_buy_cr||0)-(b.big_sell_cr||0);}
    else if(keys[sc]==='_bias'){av=a.total_cr?((a.big_buy_cr||0)/a.total_cr):0;bv=b.total_cr?((b.big_buy_cr||0)/b.total_cr):0;}
    else{av=a[keys[sc]];bv=b[keys[sc]];}
    if(av==null)av=sa?Infinity:-Infinity;
    if(bv==null)bv=sa?Infinity:-Infinity;
    return sa?(av>bv?1:-1):(av<bv?1:-1);
  });
  document.getElementById('tb').innerHTML=rows.map(r=>{
    const net=(r.big_buy_cr||0)-(r.big_sell_cr||0);
    const bp=r.total_cr?Math.round((r.big_buy_cr||0)/r.total_cr*100):0;
    const bs=r.total_cr?Math.round((r.big_sell_cr||0)/r.total_cr*100):0;
    const bias=r.big_buy_cr>r.big_sell_cr?`<span class="tag tbuy">BUY ${bp}%</span>`:r.big_sell_cr>r.big_buy_cr?`<span class="tag tsell">SELL ${bs}%</span>`:'<span class="tag tneut">NEUTRAL</span>';
    return`<tr>
      <td><span class="sym" onclick="go('${r.symbol}')">${r.symbol}</span></td>
      <td class="sub" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.name||'—'}</td>
      <td class="sub" style="font-size:11px">${r.sector||'—'}</td>
      <td>${f(r.last_price)}</td><td class="sub">${f(r.day_open)}</td>
      <td class="green">${f(r.day_high)}</td><td class="red">${f(r.day_low)}</td>
      <td>${fv(r.total_vol)}</td><td><b>${f(r.total_cr)}</b></td>
      <td class="green">${f(r.big_buy_cr)}</td><td class="red">${f(r.big_sell_cr)}</td>
      <td>${fc(net)}</td><td>${bias}</td>
      <td class="sub">${f(r.week52_high)}</td><td class="sub">${f(r.week52_low)}</td>
      <td class="sub">${r.pe_ratio?f(r.pe_ratio,1):'<span class="sub">—</span>'}</td>
    </tr>`;
  }).join('');
}
function srt(c){
  if(sc===c)sa=!sa;else{sc=c;sa=false;}
  document.querySelectorAll('th').forEach((t,i)=>{t.classList.remove('asc','desc');if(i===c)t.classList.add(sa?'asc':'desc');});
  filt();
}
function go(sym){window.location.href=BASE+'/bigplayer?sym='+sym;}
function tick(){cd--;document.getElementById('cd').textContent=cd+'s';if(cd<=0)load();}
document.querySelectorAll('th')[8].classList.add('desc');
load();setInterval(tick,1000);
</script></body></html>'''


# ── Big Player page ────────────────────────────────────────────────────────────

_COLORS = ['#58a6ff','#bc8cff','#3fb950','#ffa500','#f85149','#79c0ff',
           '#d2a8ff','#56d364','#ffd700','#ff6b6b','#4fc3f7','#ce93d8',
           '#a5d6a7','#fff176','#ef9a9a']

_BP = '''<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE — Big Player Watch</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>''' + _CSS + '''</style></head>
<body>
<nav>
  <div class="brand">📈 NSE Dashboard</div>
  <div class="nav-links"><a href="{{ prefix }}/">Overview</a><a href="{{ prefix }}/bigplayer" class="on">⚡ Big Player</a><a href="{{ prefix }}/pushlog">🔔 Pushes</a><a href="{{ prefix }}/log">📋 Log</a></div>
  <div class="nav-r"><span id="mbadge"></span><span id="asof" class="sub"></span><span id="cd" class="sub"></span></div>
</nav>
<main>
  <div class="cards">
    <div class="card"><div class="clbl">Big Buy ₹Cr</div><div class="cval green" id="c0">—</div><div class="csub" id="c0s">— bars</div></div>
    <div class="card"><div class="clbl">Big Sell ₹Cr</div><div class="cval red" id="c1">—</div><div class="csub" id="c1s">— bars</div></div>
    <div class="card"><div class="clbl">Net Flow ₹Cr</div><div class="cval" id="c2">—</div></div>
    <div class="card"><div class="clbl">Market Bias</div><div class="cval" id="c3">—</div></div>
    <div class="card"><div class="clbl">History</div><div class="cval blue" id="c4">—</div><div class="csub">trading days</div></div>
  </div>

  <div id="banner" class="banner" style="display:none">
    <span>📊</span><span id="btext"></span>
    <div class="prog-bg"><div class="prog-fill" id="bprog"></div></div>
  </div>

  <div id="alert-sec" style="display:none">
    <div class="sec-title">⚡ Unusual Activity — Spike vs 10d Average</div>
    <div class="alerts" id="alerts"></div>
  </div>

  <div class="grid2">
    <div class="sec">
      <div class="sec-title">Intraday Big Order Flow — ₹Cr per Minute</div>
      <div style="position:relative;height:220px"><canvas id="fc"></canvas></div>
    </div>
    <div class="sec">
      <div class="sec-title">Cumulative Net Flow — Top Symbols <span class="sub" style="font-weight:400;font-size:11px">(rising=accumulation, falling=distribution)</span></div>
      <div style="position:relative;height:220px"><canvas id="cc"></canvas></div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-title">Top 20 Symbols — Big Order Buy vs Sell ₹Cr</div>
    <div style="position:relative;height:280px"><canvas id="sc"></canvas></div>
  </div>

  <div class="sec">
    <div class="toolbar">
      <div class="sec-title" style="margin:0">1Cr+ Order Details</div>
      <input type="text" id="q" placeholder="Filter symbol…" oninput="filt()">
    </div>
    <div style="overflow-x:auto">
    <table><thead><tr>
      <th>Symbol</th>
      <th>Big Bars</th><th>Buy ₹Cr</th><th>Sell ₹Cr</th>
      <th>Net ₹Cr</th><th>Bias</th>
      <th>10d Avg ₹Cr</th><th>Spike</th><th>Buy / Sell Split</th>
    </tr></thead><tbody id="tb"></tbody></table>
    </div>
  </div>
</main>

<!-- Modal -->
<div class="ov" id="ov" onclick="cmClose(event)">
  <div class="modal">
    <div class="mhdr">
      <div><span id="msym" style="font-size:17px;font-weight:700"></span>
           <span id="mname" class="sub" style="font-size:11px;margin-left:8px"></span></div>
      <span class="mx" onclick="cmClose()">✕</span>
    </div>
    <div style="font-size:11px;color:var(--sub);margin-bottom:8px">
      Price (line) · Green dot = 1Cr+ Buy bar · Red dot = 1Cr+ Sell bar
    </div>
    <div style="position:relative;height:210px"><canvas id="mc"></canvas></div>
    <div class="mstats" id="mstats"></div>
  </div>
</div>

<script>
const BASE="{{ prefix }}";
const COLORS=''' + str(_COLORS) + ''';
let bpAll=[],fc=null,cc=null,sc=null,mc=null,cd=60;

const f=(v,d=2)=>v==null?'—':Number(v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const tl=iso=>iso?iso.substring(11,16):'';

async function load(){
  const d=await fetch(BASE+'/api/bigplayer').then(r=>r.json()).catch(()=>({}));
  render(d);
  document.getElementById('mbadge').innerHTML=d.market_open
    ?'<span class="badge live"><span class="dot"></span>LIVE</span>'
    :'<span class="badge closed">CLOSED</span>';
  document.getElementById('asof').textContent='Updated '+(d.as_of||'').substring(11,19)+' IST';
  cd=60;
}

function render(d){
  bpAll=d.today||[];
  const totBuy=bpAll.reduce((s,r)=>s+(r.buy_cr||0),0);
  const totSell=bpAll.reduce((s,r)=>s+(r.sell_cr||0),0);
  const totBB=bpAll.reduce((s,r)=>s+(r.buy_bars||0),0);
  const totSB=bpAll.reduce((s,r)=>s+(r.sell_bars||0),0);
  const net=totBuy-totSell;
  document.getElementById('c0').textContent='₹'+f(totBuy);
  document.getElementById('c1').textContent='₹'+f(totSell);
  document.getElementById('c0s').textContent=totBB+' buy bars';
  document.getElementById('c1s').textContent=totSB+' sell bars';
  const n=document.getElementById('c2');
  n.className='cval '+(net>0?'green':net<0?'red':'sub');
  n.textContent=(net>0?'+':'')+f(net);
  const b=document.getElementById('c3');
  if(net>0){b.className='cval green';b.textContent='BUYING';}
  else if(net<0){b.className='cval red';b.textContent='SELLING';}
  else{b.className='cval sub';b.textContent='NEUTRAL';}
  document.getElementById('c4').textContent=(d.hist_days||0)+'/10';

  // baseline banner
  const hd=d.hist_days||0;
  const bn=document.getElementById('banner');
  if(hd<10){
    bn.style.display='flex';
    document.getElementById('btext').textContent=hd===0
      ?'Day 1 — Building baseline. Spike comparison available from Day 2.'
      :`Baseline: ${hd}/10 trading days. ${hd>=2?'Spike ratios active.':'Spike comparison available tomorrow.'}`;
    document.getElementById('bprog').style.width=(hd/10*100)+'%';
  } else { bn.style.display='none'; }

  // spike alerts
  const spikes=(bpAll.filter(r=>r.spike_ratio&&r.spike_ratio>=1.5)).sort((a,b)=>b.spike_ratio-a.spike_ratio);
  const asec=document.getElementById('alert-sec');
  if(spikes.length){
    asec.style.display='block';
    document.getElementById('alerts').innerHTML=spikes.map(r=>{
      const dir=r.net_cr>0?'ac-buy':r.net_cr<0?'ac-sell':'';
      const dtag=r.net_cr>0?'<span class="tag tbuy">BUY</span>':r.net_cr<0?'<span class="tag tsell">SELL</span>':'';
      const scls=r.spike_ratio>=3?'spk-hi':'spk-md';
      return`<div class="ac ${dir}" onclick="openModal('${r.symbol}')">
        <div style="font-weight:700">${r.symbol}</div>
        <div class="sub" style="font-size:11px;margin:2px 0">₹${f(r.total_cr)} Cr</div>
        <span class="${scls}">${r.spike_ratio}× avg</span> ${dtag}
      </div>`;
    }).join('');
  } else { asec.style.display='none'; }

  // ── Intraday flow chart ───────────────────────────────────────────────────
  const tl_data=d.timeline||[];
  const fcEl=document.getElementById('fc');
  if(fc)fc.destroy();
  fc=new Chart(fcEl,{
    type:'bar',
    data:{
      labels:tl_data.map(r=>tl(r.t)),
      datasets:[
        {label:'Buy ₹Cr', data:tl_data.map(r=>r.buy_cr),
         backgroundColor:'rgba(63,185,80,.4)',borderColor:'#3fb950',borderWidth:1},
        {label:'Sell ₹Cr',data:tl_data.map(r=>r.sell_cr),
         backgroundColor:'rgba(248,81,73,.4)',borderColor:'#f85149',borderWidth:1},
      ]
    },
    options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{labels:{color:'#e6edf3',font:{size:11}}},
               tooltip:{mode:'index',intersect:false}},
      scales:{
        x:{ticks:{color:'#8b949e',maxTicksLimit:18,font:{size:10}},grid:{color:'#21262d'}},
        y:{ticks:{color:'#8b949e',font:{size:10}},grid:{color:'#21262d'},
           title:{display:true,text:'₹ Cr',color:'#8b949e',font:{size:10}}}
      }}
  });

  // ── Cumulative net flow chart ─────────────────────────────────────────────
  const cf=d.cum_flows||{};
  const syms=Object.keys(cf);
  const allTs=[...new Set(Object.values(cf).flat().map(p=>p[0]))].sort();
  const ccEl=document.getElementById('cc');
  if(cc)cc.destroy();
  cc=new Chart(ccEl,{
    type:'line',
    data:{
      labels:allTs.map(tl),
      datasets:syms.map((sym,i)=>{
        const m=Object.fromEntries(cf[sym].map(p=>[p[0],p[1]]));
        return{label:sym,data:allTs.map(t=>m[t]??null),
          borderColor:COLORS[i%COLORS.length],backgroundColor:'transparent',
          borderWidth:1.5,pointRadius:0,spanGaps:true,tension:.2};
      })
    },
    options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{labels:{color:'#e6edf3',font:{size:10},boxWidth:10}},
               tooltip:{mode:'index',intersect:false}},
      scales:{
        x:{ticks:{color:'#8b949e',maxTicksLimit:18,font:{size:10}},grid:{color:'#21262d'}},
        y:{ticks:{color:'#8b949e',font:{size:10}},grid:{color:'#21262d'},
           title:{display:true,text:'Cumulative ₹Cr',color:'#8b949e',font:{size:10}}}
      }}
  });

  // ── Top-20 grouped bar chart ──────────────────────────────────────────────
  const top=bpAll.slice(0,20);
  const scEl=document.getElementById('sc');
  if(sc)sc.destroy();
  sc=new Chart(scEl,{
    type:'bar',
    data:{
      labels:top.map(r=>r.symbol),
      datasets:[
        {label:'Buy ₹Cr', data:top.map(r=>r.buy_cr),
         backgroundColor:'rgba(63,185,80,.5)',borderColor:'#3fb950',borderWidth:1},
        {label:'Sell ₹Cr',data:top.map(r=>r.sell_cr),
         backgroundColor:'rgba(248,81,73,.5)',borderColor:'#f85149',borderWidth:1},
      ]
    },
    options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{labels:{color:'#e6edf3',font:{size:11}}},
               tooltip:{mode:'index',intersect:false}},
      scales:{
        x:{ticks:{color:'#8b949e',font:{size:10}},grid:{color:'#21262d'}},
        y:{ticks:{color:'#8b949e',font:{size:10}},grid:{color:'#21262d'},
           title:{display:true,text:'₹ Cr',color:'#8b949e',font:{size:10}}}
      }}
  });

  filt();
}

function filt(){
  const q=document.getElementById('q').value.toLowerCase();
  const rows=q?bpAll.filter(r=>(r.symbol||'').toLowerCase().includes(q)):bpAll;
  document.getElementById('tb').innerHTML=rows.map(r=>{
    const net=r.net_cr||0;
    const bias=net>0?'<span class="tag tbuy">BUY</span>':net<0?'<span class="tag tsell">SELL</span>':'<span class="tag tneut">NEUTRAL</span>';
    let spk='<span class="sub">—</span>';
    if(r.spike_ratio!=null){
      spk=r.spike_ratio>=3?`<span class="spk-hi">${r.spike_ratio}×</span>`:
          r.spike_ratio>=1.5?`<span class="spk-md">${r.spike_ratio}×</span>`:
          `<span class="sub">${r.spike_ratio}×</span>`;
    }
    const tot=(r.buy_cr||0)+(r.sell_cr||0);
    const bw=tot?Math.round((r.buy_cr||0)/tot*100):50;
    const sw=100-bw;
    return`<tr>
      <td><span class="sym" onclick="openModal('${r.symbol}')">${r.symbol}</span></td>
      <td>${r.big_bars}</td>
      <td class="green">${f(r.buy_cr)}</td>
      <td class="red">${f(r.sell_cr)}</td>
      <td>${net>0?`<span class="green">+${f(net)}</span>`:`<span class="red">${f(net)}</span>`}</td>
      <td>${bias}</td>
      <td class="sub">${r.avg_total_10d!=null?f(r.avg_total_10d):'—'}</td>
      <td>${spk}</td>
      <td><div class="split-wrap">
        <span class="green" style="font-size:10px;width:28px">${bw}%</span>
        <div class="split-bg"><div class="sb sb-b" style="width:${bw}%"></div><div class="sb sb-s" style="width:${sw}%"></div></div>
        <span class="red" style="font-size:10px;width:28px;text-align:right">${sw}%</span>
      </div></td>
    </tr>`;
  }).join('');
}

// ── Modal ────────────────────────────────────────────────────────────────────
async function openModal(sym){
  document.getElementById('ov').classList.add('on');
  document.getElementById('msym').textContent=sym;
  document.getElementById('mname').textContent='';
  document.getElementById('mstats').innerHTML='';
  if(mc){mc.destroy();mc=null;}
  const d=await fetch(BASE+'/api/intraday/'+sym).then(r=>r.json()).catch(()=>({}));
  const bars=d.bars||[];
  const rec=bpAll.find(r=>r.symbol===sym);
  mc=new Chart(document.getElementById('mc'),{
    type:'line',
    data:{
      labels:bars.map(b=>tl(b.t)),
      datasets:[{
        label:'Price ₹',
        data:bars.map(b=>b.c),
        borderColor:'#58a6ff',backgroundColor:'transparent',
        borderWidth:1.5,tension:.1,
        pointRadius:bars.map(b=>b.ac>=1?5:0),
        pointBackgroundColor:bars.map(b=>{
          if(b.ac<1)return'transparent';
          return b.bs===1?'#3fb950':b.bs===-1?'#f85149':'#d29922';
        }),
        pointBorderColor:'transparent',
      }]
    },
    options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{display:false},
        tooltip:{callbacks:{
          label:ctx=>{
            const b=bars[ctx.dataIndex];
            const parts=[`Close: ₹${f(b.c)}`];
            if(b.ac>=1)parts.push(`₹${f(b.ac)} Cr  ${b.bs===1?'BUY':b.bs===-1?'SELL':'NEUTRAL'}`);
            return parts;
          }
        }}},
      scales:{
        x:{ticks:{color:'#8b949e',maxTicksLimit:20,font:{size:10}},grid:{color:'#21262d'}},
        y:{ticks:{color:'#8b949e',font:{size:10}},grid:{color:'#21262d'},
           title:{display:true,text:'Price ₹',color:'#58a6ff',font:{size:10}}}
      }}
  });
  if(rec){
    document.getElementById('mstats').innerHTML=`
      <div class="card" style="padding:8px"><div class="clbl">Big Bars</div><div class="cval" style="font-size:16px">${rec.big_bars}</div></div>
      <div class="card" style="padding:8px"><div class="clbl">Buy ₹Cr</div><div class="cval green" style="font-size:16px">${f(rec.buy_cr)}</div></div>
      <div class="card" style="padding:8px"><div class="clbl">Sell ₹Cr</div><div class="cval red" style="font-size:16px">${f(rec.sell_cr)}</div></div>
      <div class="card" style="padding:8px"><div class="clbl">Net ₹Cr</div>
        <div class="cval ${(rec.net_cr||0)>0?'green':'red'}" style="font-size:16px">${f(rec.net_cr)}</div></div>`;
  }
}
function cmClose(e){if(!e||e.target===document.getElementById('ov'))document.getElementById('ov').classList.remove('on');}

function tick(){cd--;document.getElementById('cd').textContent=cd+'s';if(cd<=0)load();}
const autoSym=new URLSearchParams(window.location.search).get('sym');
load().then(()=>{if(autoSym)openModal(autoSym);});
setInterval(tick,1000);
</script></body></html>'''


# ── Push log page ─────────────────────────────────────────────────────────────

_PUSH_PAGE = '''<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE — Push Notifications</title><style>''' + _CSS + '''
.push-list{display:flex;flex-direction:column;gap:10px}
.push-card{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:12px 16px}
.push-card:first-child{border-color:var(--blu)}
.push-time{font-size:11px;color:var(--sub);margin-bottom:4px;display:flex;align-items:center;gap:8px}
.push-title{font-size:14px;font-weight:700;margin-bottom:8px;color:var(--tx)}
.push-body{font-family:"Courier New",monospace;font-size:12px;
           color:#d4d4d4;white-space:pre-wrap;line-height:1.6;
           background:#0a0a0a;border-radius:6px;padding:10px 12px}
.push-body .buy{color:var(--grn)}.push-body .sell{color:var(--red)}
.empty{text-align:center;padding:60px 20px;color:var(--sub)}
.cnt-badge{background:var(--bg3);border:1px solid var(--bd);border-radius:12px;
           padding:2px 10px;font-size:11px;font-weight:600;color:var(--sub)}
.latest-dot{width:7px;height:7px;border-radius:50%;background:var(--blu);
            display:inline-block;animation:pulse 1.5s infinite}
</style></head>
<body>
<nav>
  <div class="brand">📈 NSE Dashboard</div>
  <div class="nav-links">
    <a href="{{ prefix }}/">Overview</a>
    <a href="{{ prefix }}/bigplayer">⚡ Big Player</a>
    <a href="{{ prefix }}/pushlog" class="on">🔔 Pushes</a>
    <a href="{{ prefix }}/log">📋 Log</a>
  </div>
  <div class="nav-r"><span id="mbadge"></span><span id="cd" class="sub"></span></div>
</nav>
<main>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
    <div class="sec-title" style="margin:0">🔔 Push Notifications — Today</div>
    <span class="cnt-badge" id="cnt">—</span>
    <span class="sub" style="font-size:11px">Auto-refresh every 30s &nbsp;·&nbsp; Cleared each startup</span>
    <span id="asof" class="sub" style="margin-left:auto;font-size:11px"></span>
  </div>
  <div id="push-list" class="push-list"></div>
</main>
<script>
const BASE="{{ prefix }}";
let cd=30;

function colorBody(text) {
  const e = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  return e(text)
    .replace(/(🟢[^\n]*BUY[^\n]*)/g, '<span class="buy">$1</span>')
    .replace(/(🔴[^\n]*SELL[^\n]*)/g,'<span class="sell">$1</span>');
}

async function load() {
  const d = await fetch(BASE+'/api/pushlog').then(r=>r.json()).catch(()=>({pushes:[]}));
  const list  = document.getElementById('push-list');
  const pushes = d.pushes || [];

  document.getElementById('cnt').textContent = pushes.length + ' push' + (pushes.length!==1?'es':'');
  document.getElementById('asof').textContent = 'Updated ' + (d.as_of||'').substring(11,19) + ' IST';
  document.getElementById('mbadge').innerHTML = d.market_open
    ? \'<span class="badge live"><span class="dot"></span>LIVE</span>\'
    : \'<span class="badge closed">CLOSED</span>\';

  if (!pushes.length) {
    list.innerHTML = \'<div class="empty">No push notifications yet today.<br><span style="font-size:12px">They appear here when the top-6 big player positions change.</span></div>\';
    return;
  }

  list.innerHTML = pushes.map((p, i) => {
    const timeStr = (p.sent_at || \'\').substring(11, 19);
    const dateStr = (p.sent_at || \'\').substring(0, 10);
    const isLatest = i === 0;
    return `<div class="push-card">
      <div class="push-time">
        ${isLatest ? \'<span class="latest-dot"></span><span style="color:var(--blu);font-weight:600">Latest</span>\' : \'\'}
        <span>${dateStr} &nbsp;<b>${timeStr}</b> IST</span>
        <span class="sub">#${pushes.length - i}</span>
      </div>
      <div class="push-title">${p.title}</div>
      <div class="push-body">${colorBody(p.body)}</div>
    </div>`;
  }).join(\'\');
  cd = 30;
}

function tick(){cd--;document.getElementById(\'cd\').textContent=cd+\'s\';if(cd<=0)load();}
load(); setInterval(tick,1000);
</script></body></html>'''


@nse_bp.route('/api/pushlog')
def api_pushlog():
    import sqlite3 as _sq
    conn = get_conn()
    conn.row_factory = _sq.Row
    rows = conn.execute(
        "SELECT id, sent_at, title, body FROM push_log ORDER BY sent_at DESC"
    ).fetchall()
    conn.close()
    return jsonify({
        'pushes':      [dict(r) for r in rows],
        'count':       len(rows),
        'as_of':       datetime.datetime.now(IST).isoformat(),
        'market_open': is_market_open(),
    })


# ── Log page ───────────────────────────────────────────────────────────────────

_LOG_PAGE = '''<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE — Startup Log</title><style>''' + _CSS + '''
.term{background:#0a0a0a;border:1px solid var(--bd);border-radius:8px;
      padding:14px;font-family:'Courier New',Courier,monospace;font-size:12px;
      line-height:1.6;overflow-x:auto;white-space:pre-wrap;word-break:break-all;
      max-height:78vh;overflow-y:auto;color:#d4d4d4}
.ln-ok{color:#3fb950}.ln-err{color:#f85149}.ln-warn{color:#d29922}
.ln-info{color:#58a6ff}.ln-head{color:#bc8cff;font-weight:700}
.ln-pip{color:#8b949e}.ln-time{color:#6e7681}
.log-meta{display:flex;align-items:center;gap:16px;margin-bottom:10px;font-size:12px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;
     border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;
     border:1px solid var(--bd);background:var(--bg3);color:var(--tx);
     text-decoration:none}
.btn:hover{background:var(--bd);text-decoration:none}
.btn-dl{background:#1a3a1a;border-color:#2d5a2d;color:var(--grn)}
.btn-dl:hover{background:#2d5a2d}
.tail-toggle{display:flex;align-items:center;gap:6px;color:var(--sub)}
</style></head>
<body>
<nav>
  <div class="brand">📈 NSE Dashboard</div>
  <div class="nav-links">
    <a href="{{ prefix }}/">Overview</a>
    <a href="{{ prefix }}/bigplayer">⚡ Big Player</a>
    <a href="{{ prefix }}/pushlog">🔔 Pushes</a>
    <a href="{{ prefix }}/log" class="on">📋 Log</a>
  </div>
  <div class="nav-r"><span id="cd" class="sub"></span></div>
</nav>
<main>
  <div class="log-meta">
    <span class="sub" id="meta-info">Loading…</span>
    <a class="btn btn-dl" href="{{ prefix }}/log/download" download="newtrade.log">⬇ Download full log</a>
    <label class="tail-toggle">
      <input type="checkbox" id="tail-chk" checked onchange="toggleTail()"> Auto-scroll to bottom
    </label>
    <label class="tail-toggle">
      <input type="checkbox" id="refresh-chk" checked> Auto-refresh
      <span id="cd2" class="sub" style="margin-left:4px"></span>
    </label>
  </div>
  <div class="term" id="log-box"></div>
</main>
<script>
const BASE="{{ prefix }}";
let cd=10, autoScroll=true, autoRefresh=true;

function colorLine(line) {
  const e = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const l = line.toLowerCase();
  // timestamp prefix
  const tsMatch = line.match(/^(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}) - (.*)/s);
  if (tsMatch) {
    const ts = e(tsMatch[1]);
    const rest = tsMatch[2];
    const rl = rest.toLowerCase();
    let cls = '';
    if (rl.includes('error') || rl.includes('crash') || rl.includes('failed') || rl.includes('traceback') || rl.includes('exception')) cls = 'ln-err';
    else if (rl.includes('warn'))  cls = 'ln-warn';
    else if (rl.includes('done') || rl.includes('ok') || rl.includes('success') || rl.includes('finished')) cls = 'ln-ok';
    else if (rl.includes('start') || rl.includes('pull') || rl.includes('install') || rl.includes('running')) cls = 'ln-info';
    else if (rl.includes('restarting')) cls = 'ln-warn';
    return `<span class="ln-time">${ts} - </span><span class="${cls}">${e(rest)}</span>`;
  }
  // separator lines
  if (line.startsWith('===')) return `<span class="ln-head">${e(line)}</span>`;
  // pip / yfinance noise
  if (l.includes('already satisfied') || l.startsWith('requirement')) return `<span class="ln-pip">${e(line)}</span>`;
  // error/traceback lines without timestamp
  if (l.includes('error') || l.includes('traceback') || l.includes('exception') || l.startsWith('  file ')) return `<span class="ln-err">${e(line)}</span>`;
  if (l.includes('warning')) return `<span class="ln-warn">${e(line)}</span>`;
  return e(line);
}

async function load() {
  try {
    const d = await fetch(BASE+'/api/log').then(r=>r.json());
    const box = document.getElementById('log-box');
    box.innerHTML = d.lines.map(colorLine).join('\\n');
    document.getElementById('meta-info').textContent =
      `${d.total_lines} lines · ${d.size_kb} KB · Log: ${d.log_path} · Startup: ${d.started_at||'—'}`;
    if (autoScroll) box.scrollTop = box.scrollHeight;
  } catch(e) { document.getElementById('log-box').textContent = 'Error loading log: '+e; }
  cd = 10;
}

function toggleTail() { autoScroll = document.getElementById('tail-chk').checked; }

function tick() {
  cd--;
  const r = document.getElementById('refresh-chk').checked;
  document.getElementById('cd2').textContent = r ? cd+'s' : '';
  document.getElementById('cd').textContent  = r ? 'Refresh in '+cd+'s' : '';
  if (r && cd <= 0) load();
}

load();
setInterval(tick, 1000);
</script></body></html>'''


@nse_bp.route('/api/log')
def api_log():
    try:
        with open(LOG_FILE, 'r', errors='replace') as fh:
            lines = fh.readlines()
        size_kb = round(os.path.getsize(LOG_FILE) / 1024, 1)
    except FileNotFoundError:
        lines, size_kb = ['Log file not found: ' + LOG_FILE], 0

    # Derive startup time from first timestamp line
    started_at = None
    for ln in lines[:10]:
        import re
        m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', ln)
        if m:
            started_at = m.group(1)
            break

    return jsonify({
        'lines':       [l.rstrip('\n') for l in lines],
        'total_lines': len(lines),
        'size_kb':     size_kb,
        'log_path':    LOG_FILE,
        'started_at':  started_at,
    })


@nse_bp.route('/log/download')
def log_download():
    try:
        return send_file(LOG_FILE, as_attachment=True,
                         download_name='newtrade.log', mimetype='text/plain')
    except FileNotFoundError:
        return Response('Log file not found', status=404, mimetype='text/plain')


# ── Page routes ────────────────────────────────────────────────────────────────

def _prefix():
    """Return the URL prefix this blueprint is mounted at (e.g. '/nse' or '')."""
    return current_app.config.get('NSE_PREFIX', '')

@nse_bp.route('/')
def overview():
    return render_template_string(_OV, prefix=_prefix())

@nse_bp.route('/bigplayer')
def bigplayer():
    return render_template_string(_BP, prefix=_prefix())

@nse_bp.route('/pushlog')
def pushlog_view():
    return render_template_string(_PUSH_PAGE, prefix=_prefix())

@nse_bp.route('/log')
def log_view():
    return render_template_string(_LOG_PAGE, prefix=_prefix())


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import logging
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    _app = Flask(__name__)
    _app.config['NSE_PREFIX'] = ''           # standalone: no prefix
    _app.register_blueprint(nse_bp, url_prefix='')
    print(f'[web] NSE Dashboard (standalone)  →  http://[::]:{PORT}')
    print(f'[web]   Overview   : http://localhost:{PORT}/')
    print(f'[web]   Big Player : http://localhost:{PORT}/bigplayer')
    print(f'[web]   Log        : http://localhost:{PORT}/log')
    _app.run(host='::', port=PORT, debug=False, use_reloader=False)
