#!/usr/bin/env python3
"""
dbdates.py — index-friendly date helpers for nse_intraday.db

Why this exists
---------------
`bars.t` holds ISO-8601 IST timestamps ('2026-08-10T10:19:00+05:30') and is
indexed by idx_bars_t. Writing `WHERE DATE(t) = ?` wraps the indexed column in
a function, which makes the index unusable — SQLite falls back to scanning
every row. Cost then grows with the *whole* history retained, not with the
window actually being asked for.

Rewriting the same filter as a half-open range on the raw column,
`WHERE t >= '2026-08-07' AND t < '2026-08-08'`, uses the index and reads only
the rows in that window.

This is safe because the timestamps are fixed-offset IST and market hours are
09:16-15:29, so a bar's date never straddles a UTC boundary. Verified against
the full table: 0 rows out of 1.8M where DATE(t) <> substr(t, 1, 10).
"""

import datetime

__all__ = ['next_day', 'day_bounds', 'last_trading_dates', 'date_span']


def next_day(d):
    """'2026-08-07' -> '2026-08-08'. Used as the exclusive upper bound."""
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()


def day_bounds(d):
    """Half-open bounds for a single day: WHERE t >= lo AND t < hi.

    Direct replacement for `DATE(t) = d`.
    """
    return d, next_day(d)


def date_span(dates):
    """Half-open bounds covering a list of (possibly non-contiguous) dates.

    Used to narrow `DATE(t) IN (...)` queries: the range lets the index skip
    to the right window, and the original IN clause still filters out the
    weekends/holidays inside it. The IN list is left untouched, so results are
    identical by construction.

    Returns (None, None) for an empty list.
    """
    if not dates:
        return None, None
    return min(dates), next_day(max(dates))


def last_trading_dates(conn, before, n=10):
    """The n most recent dates that have bars, strictly before `before`.

    Replaces:
        SELECT DISTINCT DATE(t) FROM bars WHERE DATE(t)<? ORDER BY DATE(t) DESC LIMIT ?

    That version scans every index entry (~1s on 1.8M rows). This walks
    backwards one day at a time with MAX(t), which the index answers in
    O(log n) per step — so it costs the same whether the DB holds 50 days or
    5000. Returns dates newest-first, same as the original.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE d(x) AS (
            SELECT (SELECT MAX(t) FROM bars WHERE t < ?)
            UNION ALL
            SELECT (SELECT MAX(t) FROM bars WHERE t < substr(d.x, 1, 10))
            FROM d WHERE d.x IS NOT NULL
        )
        SELECT substr(x, 1, 10) FROM d WHERE x IS NOT NULL LIMIT ?
        """,
        (before, n)
    ).fetchall()
    return [r[0] for r in rows]
