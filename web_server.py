#!/usr/bin/env python3
"""
web_server.py — NSE Intraday Dashboard
Runs as a Blueprint mounted at /nse inside app.py (port 8080).
Can also run standalone on port 8085 for development.

Routes (all prefixed with /nse when mounted):
  /  /bigplayer  /pushlog  /log  /log/download
  /nse.css
  /api/summary  /api/bigplayer  /api/intraday/<sym>  /api/pushlog  /api/log
"""

import sqlite3, os, datetime, re
from flask import (Blueprint, Flask, jsonify, render_template,
                   request, send_file, send_from_directory,
                   Response, current_app)
from symbol_common import symbols as _sc_symbols
INDEX_MAP = {sym: idx for _, sym, idx in _sc_symbols}
from spike import compute_spikes
import pytz

LOG_FILE = "/home/ramesh/log/newtrade.log"

# Blueprint — registered in app.py with url_prefix='/nse'
nse_bp = Blueprint(
    'nse', __name__,
    template_folder='templates',   # → newtrade/templates/
    static_folder='static',        # → newtrade/static/  (served at /nse/static/)
)

DB   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nse_intraday.db")
IST  = pytz.timezone("Asia/Kolkata")
PORT = 8090

_COLORS = ['#58a6ff','#bc8cff','#3fb950','#ffa500','#f85149','#79c0ff',
           '#d2a8ff','#56d364','#ffd700','#ff6b6b','#4fc3f7','#ce93d8',
           '#a5d6a7','#fff176','#ef9a9a']


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def today_ist():
    return datetime.datetime.now(IST).strftime('%Y-%m-%d')

def effective_date(conn):
    """Return today if it has bar data, else fall back to the last date that does."""
    today = today_ist()
    row = conn.execute(
        "SELECT MAX(DATE(t)) FROM bars WHERE DATE(t) <= ?", (today,)
    ).fetchone()
    return row[0] if row and row[0] else today

def is_market_open():
    n = datetime.datetime.now(IST)
    return (9, 15) <= (n.hour, n.minute) <= (15, 35) and n.weekday() < 5

def _prefix():
    """Return the URL prefix this blueprint is mounted at (e.g. '/nse' or '')."""
    return current_app.config.get('NSE_PREFIX', '')


# ── Static CSS ────────────────────────────────────────────────────────────────

@nse_bp.route('/nse.css')
def serve_css():
    """Serve the dashboard stylesheet relative to the blueprint prefix."""
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    return send_from_directory(static_dir, 'nse.css', mimetype='text/css')


# ── API ────────────────────────────────────────────────────────────────────────

@nse_bp.route('/api/summary')
def api_summary():
    conn  = get_conn()
    today = effective_date(conn)

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
    sym_list = []
    for r in syms:
        row = dict(r)
        row['indices'] = INDEX_MAP.get(row['symbol'], '')
        sym_list.append(row)
    return jsonify({
        'market':      dict(mkt) if mkt else {},
        'symbols':     sym_list,
        'as_of':       datetime.datetime.now(IST).isoformat(),
        'data_date':   today,
        'market_open': is_market_open(),
    })


@nse_bp.route('/api/bigplayer')
def api_bigplayer():
    conn  = get_conn()
    today = effective_date(conn)

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
                   - SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END), 2)                  as net_cr,
               (SELECT ROUND(c,2) FROM bars b2
                WHERE b2.symbol=bars.symbol AND DATE(b2.t)=?
                ORDER BY b2.t DESC LIMIT 1)                                       as last_price
        FROM bars WHERE DATE(t)=? AND ac>=1
        GROUP BY symbol ORDER BY total_cr DESC
    ''', (today, today)).fetchall()

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

    # 52-week high/low and last price for each symbol
    sym_meta = {}
    if today_bp:
        syms_list = [r['symbol'] for r in today_bp]
        ph2 = ','.join('?' * len(syms_list))
        for r in conn.execute(f'''
            SELECT symbol, week52_high, week52_low
            FROM symbol_info WHERE symbol IN ({ph2})
        ''', syms_list).fetchall():
            sym_meta[r['symbol']] = dict(r)

    # Pace-based spike (today's cumulative ₹Cr vs the symbol's daily budget,
    # normalized for elapsed session bars) for 2d / 5d / 10d lookbacks.
    spikes = compute_spikes(conn, today)

    conn.close()

    result = []
    for r in today_bp:
        d = dict(r)
        a = avg10.get(d['symbol'], {})
        d['avg_buy_10d']   = a.get('avg_buy')
        d['avg_sell_10d']  = a.get('avg_sell')
        d['avg_total_10d'] = a.get('avg_total')
        sp = spikes.get(d['symbol'], {})
        for k in ('spike_2d', 'spike_5d', 'spike_10d',
                  'avg_cr_2d', 'avg_cr_5d', 'avg_cr_10d', 'bars_elapsed',
                  'vol_spike', 'avg_vol_10d'):
            d[k] = sp.get(k)
        d['indices'] = INDEX_MAP.get(d['symbol'], '')
        sm = sym_meta.get(d['symbol'], {})
        d['week52_high'] = sm.get('week52_high')
        d['week52_low']  = sm.get('week52_low')
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


@nse_bp.route('/api/index_flow')
def api_index_flow():
    conn  = get_conn()
    today = effective_date(conn)

    CODES = ['NI', 'BN', 'FN', 'MS']

    # Build index membership: code -> set of symbols
    # Double-counting is intentional: a symbol in NI+BN contributes fully to both.
    idx_members = {c: set() for c in CODES}
    for sym, idx_str in INDEX_MAP.items():
        for code in (idx_str or '').split(','):
            if code in idx_members:
                idx_members[code].add(sym)

    # Today's big-player data per symbol
    sym_rows = conn.execute('''
        SELECT symbol,
               ROUND(SUM(CASE WHEN bs=1  THEN ac ELSE 0 END), 2)  as buy_cr,
               ROUND(SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END), 2)  as sell_cr,
               ROUND(SUM(ac), 2)                                   as total_cr,
               ROUND(SUM(CASE WHEN bs=1 THEN ac ELSE -ac END), 2) as net_cr,
               COUNT(*)                                            as big_bars
        FROM bars WHERE DATE(t)=? AND ac>=1
        GROUP BY symbol
    ''', (today,)).fetchall()

    # Per-minute per-symbol net flow (for index timeline)
    tl_rows = conn.execute('''
        SELECT t, symbol,
               ROUND(SUM(CASE WHEN bs=1 THEN ac ELSE -ac END), 2) as net_cr
        FROM bars WHERE DATE(t)=? AND ac>=1
        GROUP BY t, symbol ORDER BY t
    ''', (today,)).fetchall()

    # 10-day historical total per symbol per day (for index-level spike ratio)
    hist_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT DATE(t) FROM bars WHERE DATE(t)<? ORDER BY DATE(t) DESC LIMIT 10",
        (today,)
    ).fetchall()]
    hist_sym_day = {}  # (symbol, dt) -> total_cr
    if hist_dates:
        ph = ','.join('?' * len(hist_dates))
        for r in conn.execute(f'''
            SELECT symbol, DATE(t) as dt, ROUND(SUM(ac),2) as total_cr
            FROM bars WHERE ac>=1 AND DATE(t) IN ({ph})
            GROUP BY symbol, dt
        ''', hist_dates).fetchall():
            hist_sym_day[(r['symbol'], r['dt'])] = r['total_cr'] or 0

    # Build per-index aggregates
    indices_out = {}
    for code in CODES:
        members = idx_members[code]
        buy = sell = net = bars = 0
        buying = selling = active = 0
        sym_details = []

        for r in sym_rows:
            sym = r['symbol']
            if sym not in members:
                continue
            active += 1
            b  = r['buy_cr']  or 0
            s  = r['sell_cr'] or 0
            n  = r['net_cr']  or 0
            buy  += b; sell += s; net += n; bars += r['big_bars'] or 0
            if n > 0:   buying   += 1
            elif n < 0: selling  += 1
            sym_details.append(dict(r))

        sym_details.sort(key=lambda x: abs(x['net_cr'] or 0), reverse=True)

        # Index-level 10d avg total
        idx_day_totals = {}
        for sym in members:
            for dt in hist_dates:
                v = hist_sym_day.get((sym, dt), 0)
                idx_day_totals[dt] = idx_day_totals.get(dt, 0) + v
        avg10 = round(sum(idx_day_totals.values()) / len(idx_day_totals), 2) if idx_day_totals else None
        today_total = round(buy + sell, 2)
        spike = round(today_total / avg10, 2) if avg10 and avg10 > 0 else None

        # Top-3 concentration: what % of |net flow| comes from top 3 symbols
        top3_abs = sum(abs(s['net_cr'] or 0) for s in sym_details[:3])
        total_abs = sum(abs(s['net_cr'] or 0) for s in sym_details)
        concentration = round(top3_abs / total_abs * 100) if total_abs > 0 else 0

        indices_out[code] = {
            'buy_cr':        round(buy,  2),
            'sell_cr':       round(sell, 2),
            'net_cr':        round(net,  2),
            'big_bars':      bars,
            'total_symbols': len(members),
            'active_today':  active,
            'buying_count':  buying,
            'selling_count': selling,
            'avg10_total':   avg10,
            'spike_ratio':   spike,
            'concentration': concentration,
            'top_movers':    sym_details[:10],
        }

    # Per-index cumulative net flow timeline
    all_ts = sorted(set(r['t'] for r in tl_rows))
    ts_sym_net = {}  # t -> {symbol: net_cr}
    for r in tl_rows:
        ts_sym_net.setdefault(r['t'], {})[r['symbol']] = r['net_cr'] or 0

    idx_timeline = {}
    for code in CODES:
        members = idx_members[code]
        cum, pts = 0.0, []
        for t in all_ts:
            sym_nets = ts_sym_net.get(t, {})
            cum = round(cum + sum(v for sym, v in sym_nets.items() if sym in members), 2)
            pts.append([t, cum])
        idx_timeline[code] = pts

    conn.close()
    return jsonify({
        'indices':     indices_out,
        'timeline':    idx_timeline,
        'today_date':  today,
        'as_of':       datetime.datetime.now(IST).isoformat(),
        'market_open': is_market_open(),
    })


@nse_bp.route('/index_flow')
def page_index_flow():
    return render_template('nse_index_flow.html', prefix=_prefix())


@nse_bp.route('/api/intraday/<symbol>')
def api_intraday(symbol):
    days = request.args.get('days', 1, type=int)
    days = max(1, min(days, 365))
    conn = get_conn()

    pace = {}
    if days == 1:
        today = effective_date(conn)
        sym_u = symbol.upper()
        bars = conn.execute(
            "SELECT t, o, h, l, c, v, ROUND(ac,4) as ac, bs "
            "FROM bars WHERE symbol=? AND DATE(t)=? ORDER BY t",
            (sym_u, today)
        ).fetchall()

        # Daily big-player ₹Cr budget over the last 2 / 5 / 10 trading dates,
        # used to draw the 2d/5d/10d pace lines in the modal. Use the *global*
        # last-10 dates (not per-symbol) with zero-fill, so these averages match
        # exactly what compute_spikes() uses for the table/push spike numbers.
        hist_dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT DATE(t) FROM bars WHERE DATE(t)<? "
            "ORDER BY DATE(t) DESC LIMIT 10", (today,)
        ).fetchall()]
        day_tot = {}
        if hist_dates:
            ph = ','.join('?' * len(hist_dates))
            for r in conn.execute(
                f"SELECT DATE(t) AS dt, ROUND(SUM(ac),2) AS tot FROM bars "
                f"WHERE symbol=? AND ac>=1 AND DATE(t) IN ({ph}) GROUP BY dt",
                [sym_u] + hist_dates
            ).fetchall():
                day_tot[r['dt']] = r['tot'] or 0
        for k in (2, 5, 10):
            sub = hist_dates[:k]
            pace[f'avg_cr_{k}d'] = (
                round(sum(day_tot.get(d, 0) for d in sub) / len(sub), 2)
                if sub else None
            )
    else:
        # Fetch last `days` distinct trading dates
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT DATE(t) FROM bars WHERE symbol=? "
            "ORDER BY DATE(t) DESC LIMIT ?",
            (symbol.upper(), days)
        ).fetchall()]
        if dates:
            ph = ','.join('?' * len(dates))
            bars = conn.execute(
                f"SELECT t, o, h, l, c, v, ROUND(ac,4) as ac, bs "
                f"FROM bars WHERE symbol=? AND DATE(t) IN ({ph}) ORDER BY t",
                [symbol.upper()] + dates
            ).fetchall()
        else:
            bars = []

    conn.close()
    return jsonify({'symbol': symbol.upper(), 'bars': [dict(r) for r in bars], 'pace': pace})


@nse_bp.route('/api/pushlog')
def api_pushlog():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, sent_at, title, body, real_push FROM push_log ORDER BY sent_at DESC"
    ).fetchall()
    conn.close()
    pushes    = [dict(r) for r in rows]
    real_count = sum(1 for p in pushes if p.get('real_push'))
    return jsonify({
        'pushes':      pushes,
        'count':       len(pushes),
        'real_count':  real_count,
        'as_of':       datetime.datetime.now(IST).isoformat(),
        'market_open': is_market_open(),
    })


@nse_bp.route('/api/log')
def api_log():
    try:
        with open(LOG_FILE, 'r', errors='replace') as fh:
            lines = fh.readlines()
        size_kb = round(os.path.getsize(LOG_FILE) / 1024, 1)
    except FileNotFoundError:
        lines, size_kb = ['Log file not found: ' + LOG_FILE], 0

    started_at = None
    for ln in lines[:10]:
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


# ── Page routes ────────────────────────────────────────────────────────────────

@nse_bp.route('/')
def overview():
    return render_template('nse_overview.html', prefix=_prefix())

@nse_bp.route('/bigplayer')
def bigplayer():
    return render_template('nse_bigplayer.html', prefix=_prefix(), colors=_COLORS)

@nse_bp.route('/pushlog')
def pushlog_view():
    return render_template('nse_pushlog.html', prefix=_prefix())

@nse_bp.route('/log')
def log_view():
    return render_template('nse_log.html', prefix=_prefix())

@nse_bp.route('/log/download')
def log_download():
    try:
        return send_file(LOG_FILE, as_attachment=True,
                         download_name='newtrade.log', mimetype='text/plain')
    except FileNotFoundError:
        return Response('Log file not found', status=404, mimetype='text/plain')


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
