"""
notifier.py
───────────
Watches the top-6 big-player symbols every minute and fires a
Pushbullet notification when anything notable shifts:

  • A symbol enters or leaves the top-6
  • A symbol's bias flips  (BUY ↔ SELL)  while inside the top-6
  • A symbol moves ≥ 2 positions within the top-6

Called from main_startup.py after every fetch_and_store().
"""

import datetime
import pushbullet
import pytz

from db_setup import get_connection, log_push
from spike import compute_spikes, ranked_spikes, DEFAULT_LB
from dbdates import day_bounds

PUSHBULLET_TOKEN = "o.jf9XoTk0S42G4FkjfhD5PZFiLcWAbj9r"
IST = pytz.timezone("Asia/Kolkata")

# ── In-memory state (lives for the duration of the process) ───────────────────
_prev_top6:   list = []   # list of dicts: symbol, buy_cr, sell_cr, total_cr, bias
_prev_spikes: list = []   # spike leaderboard from the previous poll


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bias(buy_cr: float, sell_cr: float) -> str:
    if buy_cr > sell_cr:   return "BUY"
    if sell_cr > buy_cr:   return "SELL"
    return "NEUTRAL"

def _emoji(bias: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "⚪"}.get(bias, "⚪")

def _cr(val: float) -> str:
    """Format crore value compactly: 2007 → ₹2007Cr, 85.3 → ₹85Cr"""
    return f"₹{val:,.0f}Cr"

def _52w_pct(r: dict) -> str:
    h, l, p = r.get('week52_high'), r.get('week52_low'), r.get('cur_price')
    if h and l and p and h > l:
        pct = (p - l) / (h - l) * 100
        return f"{pct:.1f}%"
    return ""


def _get_top6(today: str) -> list:
    import sqlite3 as _sqlite3
    conn = get_connection()
    conn.row_factory = _sqlite3.Row
    try:
        rows = conn.execute('''
            SELECT b.symbol,
                   ROUND(SUM(CASE WHEN b.bs=1  THEN b.ac ELSE 0 END), 2) AS buy_cr,
                   ROUND(SUM(CASE WHEN b.bs=-1 THEN b.ac ELSE 0 END), 2) AS sell_cr,
                   ROUND(SUM(b.ac), 2)                                    AS total_cr,
                   si.week52_high,
                   si.week52_low,
                   (SELECT c FROM bars WHERE symbol = b.symbol AND t >= ? AND t < ?
                    ORDER BY t DESC LIMIT 1)                               AS cur_price
            FROM bars b
            LEFT JOIN symbol_info si ON si.symbol = b.symbol
            WHERE b.t >= ? AND b.t < ? AND b.ac >= 1
            GROUP BY b.symbol
            ORDER BY total_cr DESC
            LIMIT 6
        ''', day_bounds(today) * 2).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d['bias'] = _bias(d['buy_cr'], d['sell_cr'])
        result.append(d)
    return result


def _within_push_window() -> bool:
    """Real pushes only fire during market hours: 09:30–15:15 IST."""
    now = datetime.datetime.now(IST).time()
    return datetime.time(9, 30) <= now <= datetime.time(15, 15)


def _send_push(title: str, body: str, sent_at: str, real_push: bool = True):
    # A real push only goes out when requested AND inside market hours.
    did_push = real_push and _within_push_window()
    log_push(title, body, sent_at, real_push=did_push)   # always persist to DB
    if did_push:
        try:
            pb = pushbullet.Pushbullet(PUSHBULLET_TOKEN)
            pb.push_note(title, body)
            print(f"[notify] ✓ Pushed: {title}")
        except Exception as e:
            print(f"[notify] ✗ Push failed: {e}")
    elif real_push:
        print(f"[notify] DB-only (outside 09:30–15:15 window): {title}")
    else:
        print(f"[notify] DB-only (no real push): {title}")


# ── Main entry point ──────────────────────────────────────────────────────────

def check_and_notify():
    """
    Compare current top-6 against the previous snapshot.
    Sends a push if the ranking, membership, or bias changed.
    Safe to call every minute — only notifies on actual changes.
    """
    global _prev_top6

    today   = datetime.datetime.now(IST).strftime('%Y-%m-%d')
    current = _get_top6(today)

    if not current:
        return  # market not open / no data yet

    # ── First call: just store baseline, no push ──────────────────────────────
    if not _prev_top6:
        _prev_top6 = current
        syms = [r['symbol'] for r in current]
        print(f"[notify] Baseline top-6: {syms}")
        return

    prev_syms = [r['symbol'] for r in _prev_top6]
    curr_syms = [r['symbol'] for r in current]
    prev_map  = {r['symbol']: r for r in _prev_top6}
    curr_map  = {r['symbol']: r for r in current}

    changes = []

    # 1. Entries / exits
    for s in curr_syms:
        if s not in prev_syms:
            changes.append(f"{s} entered top-6")
    for s in prev_syms:
        if s not in curr_syms:
            changes.append(f"{s} dropped out")

    # 2. Bias flips for symbols that stayed inside
    for s in curr_syms:
        if s in prev_map and curr_map[s]['bias'] != prev_map[s]['bias']:
            changes.append(
                f"{s} flipped {prev_map[s]['bias']} → {curr_map[s]['bias']}"
            )

    # 3. Rank moves ≥ 2 positions (for symbols present in both)
    for s in curr_syms:
        if s in prev_syms:
            old_rank = prev_syms.index(s) + 1
            new_rank = curr_syms.index(s) + 1
            if abs(new_rank - old_rank) >= 2:
                arrow = "↑" if new_rank < old_rank else "↓"
                changes.append(f"{s} {arrow} #{old_rank}→#{new_rank}")

    if not changes:
        _prev_top6 = current
        return  # nothing notable

    # ── Build notification ────────────────────────────────────────────────────
    now_str = datetime.datetime.now(IST).strftime('%H:%M')
    title   = f"⚡ Big Player Top-6  [{now_str}]"

    lines = []
    for i, r in enumerate(current, 1):
        s        = r['symbol']
        rank_tag = ""
        if s in prev_map:
            old_rank = prev_syms.index(s) + 1
            if old_rank != i:
                rank_tag = f"  (was #{old_rank})"
        else:
            rank_tag = "  ← NEW"

        pct52 = _52w_pct(r)
        pct_str = f"  52W:{pct52}" if pct52 else ""
        lines.append(
            f"#{i} {_emoji(r['bias'])} {s:<12} {_cr(r['total_cr']):>10}  "
            f"{r['bias']}{rank_tag}{pct_str}"
        )

    lines.append("")
    lines.append("Changes:")
    for c in changes:
        lines.append(f"  • {c}")

    body    = "\n".join(lines)
    sent_at = datetime.datetime.now(IST).isoformat()

    has_non_flip    = any("flipped" not in c for c in changes)
    do_real_push    = has_non_flip

    print(f"[notify] Change detected: {changes}")
    _send_push(title, body, sent_at, real_push=do_real_push)

    _prev_top6 = current


# ── Spike leaderboard notifier ────────────────────────────────────────────────

def notify_data_outage(fail_count: int, detail: str = ""):
    """
    One-shot alert when the yfinance feed has failed `fail_count` polls in a row.
    Sent once when the streak hits the threshold; the caller suppresses repeats
    until a successful fetch re-arms it. Always logged to push_log.
    """
    now_str = datetime.datetime.now(IST).strftime('%H:%M')
    title   = f"⚠️ Data feed down  [{now_str}]"
    body    = (f"yfinance fetch failed {fail_count}× in a row — bar collection is "
               f"stalled, no new data until it recovers.")
    if detail:
        body += f"\n\n{detail[:200]}"
    sent_at = datetime.datetime.now(IST).isoformat()
    _send_push(title, body, sent_at, real_push=True)


def _spike_bias(net_cr: float) -> str:
    if net_cr > 0:   return "BUY"
    if net_cr < 0:   return "SELL"
    return "NEUTRAL"


def check_and_notify_spikes():
    """
    Pace-based spike alert. Ranks symbols by how far today's cumulative big-player
    ₹Cr is running ahead of their own daily budget (default 5-day lookback), and
    pushes only when the spike *leaderboard* reshuffles — a symbol enters, drops,
    or moves ≥2 ranks. Each line shows the 2d / 5d / 10d multiples so you can see
    which windows are firing. Runs every poll; safe to call alongside the top-6.
    """
    global _prev_spikes

    today = datetime.datetime.now(IST).strftime('%Y-%m-%d')
    conn  = get_connection()
    import sqlite3 as _sqlite3
    conn.row_factory = _sqlite3.Row
    try:
        spikes  = compute_spikes(conn, today)
        current = ranked_spikes(spikes, lookback=DEFAULT_LB, top=5)
        # Attach 52-week position + last price for the leaderboard symbols
        if current:
            syms = [r['symbol'] for r in current]
            ph   = ','.join('?' * len(syms))
            for m in conn.execute(f'''
                SELECT si.symbol, si.week52_high, si.week52_low,
                       (SELECT c FROM bars WHERE symbol=si.symbol AND t>=? AND t<?
                        ORDER BY t DESC LIMIT 1) AS cur_price
                FROM symbol_info si WHERE si.symbol IN ({ph})
            ''', list(day_bounds(today)) + syms).fetchall():
                r = next(x for x in current if x['symbol'] == m['symbol'])
                r['week52_high'] = m['week52_high']
                r['week52_low']  = m['week52_low']
                r['cur_price']   = m['cur_price']
    finally:
        conn.close()

    # ── First call / nothing spiking: just store baseline ─────────────────────
    if not _prev_spikes:
        _prev_spikes = current
        if current:
            print(f"[notify] Baseline spikes: {[r['symbol'] for r in current]}")
        return

    prev_syms = [r['symbol'] for r in _prev_spikes]
    curr_syms = [r['symbol'] for r in current]
    prev_map  = {r['symbol']: r for r in _prev_spikes}
    curr_map  = {r['symbol']: r for r in current}

    # Order/membership changes — these are what actually fire the push
    order_changes = []
    for s in curr_syms:
        if s not in prev_syms:
            order_changes.append(f"{s} entered spike list")
    for s in prev_syms:
        if s not in curr_syms:
            order_changes.append(f"{s} cooled off")
    for s in curr_syms:
        if s in prev_syms:
            old_rank = prev_syms.index(s) + 1
            new_rank = curr_syms.index(s) + 1
            if abs(new_rank - old_rank) >= 2:
                arrow = "↑" if new_rank < old_rank else "↓"
                order_changes.append(f"{s} {arrow} #{old_rank}→#{new_rank}")

    # Bias flips for symbols staying in the list — shown for context, but a flip
    # on its own does NOT trigger a push (only an order/membership change does)
    flip_changes = []
    for s in curr_syms:
        if s in prev_map:
            old_bias = _spike_bias(prev_map[s]['net_cr'])
            new_bias = _spike_bias(curr_map[s]['net_cr'])
            if old_bias != new_bias:
                flip_changes.append(f"{s} flipped {old_bias} → {new_bias}")

    # Log every change-minute to the DB; real Pushbullet only on order changes
    changes = order_changes + flip_changes
    if not changes:
        _prev_spikes = current
        return

    now_str = datetime.datetime.now(IST).strftime('%H:%M')
    title   = f"🚀 Spike Leaderboard  [{now_str}]"

    def _mult(v):
        return f"{v:g}×" if v is not None else "—"

    lines = []
    for i, r in enumerate(current, 1):
        bias  = _spike_bias(r['net_cr'])
        pct52 = _52w_pct(r)
        p52   = f"  52W:{pct52}" if pct52 else ""
        lines.append(
            f"#{i} {_emoji(bias)} {r['symbol']:<11} "
            f"2d {_mult(r['spike_2d'])} · 5d {_mult(r['spike_5d'])} · "
            f"10d {_mult(r['spike_10d'])} · vol {_mult(r.get('vol_spike'))}  "
            f"{bias}{p52}"
        )
    lines.append("")
    lines.append("Changes:")
    for c in changes:
        lines.append(f"  • {c}")

    body    = "\n".join(lines)
    sent_at = datetime.datetime.now(IST).isoformat()

    do_real_push = bool(order_changes)   # flip-only → DB log, no real push
    print(f"[notify] Spike change: {changes} (real_push={do_real_push})")
    _send_push(title, body, sent_at, real_push=do_real_push)

    _prev_spikes = current
