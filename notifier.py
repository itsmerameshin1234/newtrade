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

PUSHBULLET_TOKEN = "o.jf9XoTk0S42G4FkjfhD5PZFiLcWAbj9r"
IST = pytz.timezone("Asia/Kolkata")

# ── In-memory state (lives for the duration of the process) ───────────────────
_prev_top6: list = []   # list of dicts: symbol, buy_cr, sell_cr, total_cr, bias


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


def _get_top6(today: str) -> list:
    import sqlite3 as _sqlite3
    conn = get_connection()
    conn.row_factory = _sqlite3.Row
    try:
        rows = conn.execute('''
            SELECT symbol,
                   ROUND(SUM(CASE WHEN bs=1  THEN ac ELSE 0 END), 2) AS buy_cr,
                   ROUND(SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END), 2) AS sell_cr,
                   ROUND(SUM(ac), 2)                                  AS total_cr
            FROM bars
            WHERE DATE(t) = ? AND ac >= 1
            GROUP BY symbol
            ORDER BY total_cr DESC
            LIMIT 6
        ''', (today,)).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d['bias'] = _bias(d['buy_cr'], d['sell_cr'])
        result.append(d)
    return result


def _send_push(title: str, body: str, sent_at: str, real_push: bool = True):
    log_push(title, body, sent_at)              # always persist to DB
    if real_push:
        try:
            pb = pushbullet.Pushbullet(PUSHBULLET_TOKEN)
            pb.push_note(title, body)
            print(f"[notify] ✓ Pushed: {title}")
        except Exception as e:
            print(f"[notify] ✗ Push failed: {e}")
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

        lines.append(
            f"#{i} {_emoji(r['bias'])} {s:<12} {_cr(r['total_cr']):>10}  "
            f"{r['bias']}{rank_tag}"
        )

    lines.append("")
    lines.append("Changes:")
    for c in changes:
        lines.append(f"  • {c}")

    body    = "\n".join(lines)
    sent_at = datetime.datetime.now(IST).isoformat()

    # Real push only between 09:30–15:15 IST and only when change is not flip-only
    t = datetime.datetime.now(IST).time()
    in_window       = datetime.time(9, 30) <= t <= datetime.time(15, 15)
    has_non_flip    = any("flipped" not in c for c in changes)
    do_real_push    = in_window and has_non_flip

    print(f"[notify] Change detected: {changes}")
    _send_push(title, body, sent_at, real_push=do_real_push)

    _prev_top6 = current
