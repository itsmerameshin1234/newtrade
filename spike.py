"""
spike.py — pace-based big-player spike detection.
────────────────────────────────────────────────
A "spike" is detected by comparing today's *cumulative* big-player ₹Cr against
the symbol's own daily budget, normalized for how far into the session we are.

    bars_elapsed    = session minutes since 09:15           (1 … 375)
    expected_so_far = avg_daily_cr × (bars_elapsed / 375)   (uniform 375-bar split)
    spike           = today_cum_cr / expected_so_far

So at bar 100 (≈27% of the 375-bar day) a symbol that has already done 50% of
its average daily flow reads 0.50 / 0.27 ≈ 1.85× — flagged *now*, not at close.

The same number is computed against 2-, 5- and 10-day averages so you can tell
"hot vs the long trend" (10d) from "hot vs the last couple of days" (2d).

Only big-player bars count (ac ≥ 1 crore), consistent with the rest of the app.
Crore already embeds volume × price, so it is the single sufficient measure.

Shared by notifier.py (push alerts) and web_server.py (dashboard / API).
"""

import datetime
import pytz

IST          = pytz.timezone("Asia/Kolkata")
SESSION_BARS = 375          # 09:15 → 15:30 = 375 one-minute bars
LOOKBACKS    = (2, 5, 10)   # day windows to compare against
DEFAULT_LB   = 5            # default lookback driving ranking / push order

# ── Noise guards ──────────────────────────────────────────────────────────────
MIN_BARS_ELAPSED = 5        # need a few minutes of data before judging pace
MIN_EXPECTED_CR  = 2.0      # ₹Cr floor on the denominator → kills early-session 10×s
SPIKE_NOTABLE    = 1.5      # a symbol is "spiking" once it crosses this multiple


def bars_elapsed_from_ts(latest_ts: str) -> int:
    """Session-minute index (1…375) of the most recent bar timestamp today."""
    dt = datetime.datetime.fromisoformat(latest_ts)
    mins = (dt.hour - 9) * 60 + (dt.minute - 15) + 1
    return max(1, min(mins, SESSION_BARS))


def _avg_over(sym_days: dict, dates: list):
    """Average daily big-player ₹Cr over `dates` (missing day counts as 0)."""
    if not dates:
        return None
    return round(sum(sym_days.get(d, 0) for d in dates) / len(dates), 2)


def compute_spikes(conn, today: str) -> dict:
    """
    Return {symbol: {...}} for every symbol active today, where each entry holds:
        cum_cr, net_cr, bars_elapsed,
        avg_cr_2d/5d/10d, spike_2d/5d/10d  (spike None when below the floor)
    """
    # Last 10 distinct trading dates before today (most-recent first)
    hist_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT DATE(t) FROM bars WHERE DATE(t)<? ORDER BY DATE(t) DESC LIMIT 10",
        (today,)
    ).fetchall()]

    # Per-symbol, per-day big-player ₹Cr total over those dates
    day_tot: dict = {}
    if hist_dates:
        ph = ','.join('?' * len(hist_dates))
        for r in conn.execute(f'''
            SELECT symbol, DATE(t) AS dt, ROUND(SUM(ac), 2) AS tot
            FROM bars WHERE ac>=1 AND DATE(t) IN ({ph})
            GROUP BY symbol, dt
        ''', hist_dates).fetchall():
            day_tot.setdefault(r['symbol'], {})[r['dt']] = r['tot'] or 0

    # Today's cumulative big-player flow + latest bar timestamp per symbol
    today_rows = conn.execute('''
        SELECT symbol,
               ROUND(SUM(ac), 2)                              AS cum_cr,
               SUM(CASE WHEN bs=1  THEN ac ELSE 0 END)        AS buy,
               SUM(CASE WHEN bs=-1 THEN ac ELSE 0 END)        AS sell,
               MAX(t)                                         AS last_t
        FROM bars WHERE DATE(t)=? AND ac>=1
        GROUP BY symbol
    ''', (today,)).fetchall()

    out: dict = {}
    for r in today_rows:
        sym     = r['symbol']
        sd      = day_tot.get(sym, {})
        elapsed = bars_elapsed_from_ts(r['last_t'])
        frac    = elapsed / SESSION_BARS
        cum     = r['cum_cr'] or 0

        entry = {
            'symbol':       sym,
            'cum_cr':       cum,
            'net_cr':       round((r['buy'] or 0) - (r['sell'] or 0), 2),
            'bars_elapsed': elapsed,
        }
        for k in LOOKBACKS:
            avg = _avg_over(sd, hist_dates[:k])
            entry[f'avg_cr_{k}d'] = avg
            expected = avg * frac if avg else None
            if expected and expected >= MIN_EXPECTED_CR and elapsed >= MIN_BARS_ELAPSED:
                entry[f'spike_{k}d'] = round(cum / expected, 2)
            else:
                entry[f'spike_{k}d'] = None
        out[sym] = entry

    return out


def ranked_spikes(spikes: dict, lookback: int = DEFAULT_LB,
                  top: int = 6, min_spike: float = SPIKE_NOTABLE) -> list:
    """Symbols currently spiking ≥ min_spike on `lookback`, highest first."""
    key  = f'spike_{lookback}d'
    rows = [s for s in spikes.values()
            if s.get(key) is not None and s[key] >= min_spike]
    rows.sort(key=lambda s: s[key], reverse=True)
    return rows[:top]
