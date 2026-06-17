"""
vwap.py
───────
Previous-day VWAP (Volume-Weighted Average Price) from stored 1-min bars.

    VWAP = Σ( typical_price × volume )  /  Σ( volume )
           typical_price = (High + Low + Close) / 3      ← classic, per bar

Computed over ALL of the previous trading day's bars (VWAP resets each day),
where "previous" means the most recent trading date strictly before the
reference date (default: the latest date present in the bars table).
"""

import sqlite3

from db_setup import get_connection


def _resolve_conn(conn):
    """Return (conn, owned) — open one if caller didn't pass it."""
    if conn is not None:
        return conn, False
    c = get_connection()
    c.row_factory = sqlite3.Row
    return c, True


def prev_day_vwap_all(conn=None, ref_date=None) -> dict:
    """
    Previous trading day's VWAP for every symbol.

    Args:
        conn:     optional open sqlite connection (reused if given).
        ref_date: 'YYYY-MM-DD' to measure "previous" against. Defaults to the
                  latest date in the bars table. The VWAP is taken from the
                  most recent trading date strictly before this.

    Returns:
        { "SYMBOL": vwap_float, ... }  (empty dict if no prior day exists)
    """
    conn, owned = _resolve_conn(conn)
    try:
        if ref_date is None:
            row = conn.execute("SELECT MAX(DATE(t)) FROM bars").fetchone()
            ref_date = row[0] if row else None
        if ref_date is None:
            return {}

        rows = conn.execute("""
            SELECT symbol,
                   ROUND(SUM((h + l + c) / 3.0 * v) / NULLIF(SUM(v), 0), 2) AS vwap
            FROM bars
            WHERE DATE(t) = (SELECT MAX(DATE(t)) FROM bars WHERE DATE(t) < ?)
            GROUP BY symbol
        """, (ref_date,)).fetchall()

        return {r[0]: r[1] for r in rows if r[1] is not None}
    finally:
        if owned:
            conn.close()


def prev_day_vwap(symbol: str, conn=None, ref_date=None):
    """
    Previous trading day's VWAP for a single symbol.

    Returns a float, or None if there are no bars for that day.
    """
    conn, owned = _resolve_conn(conn)
    try:
        if ref_date is None:
            row = conn.execute("SELECT MAX(DATE(t)) FROM bars").fetchone()
            ref_date = row[0] if row else None
        if ref_date is None:
            return None

        row = conn.execute("""
            SELECT ROUND(SUM((h + l + c) / 3.0 * v) / NULLIF(SUM(v), 0), 2) AS vwap
            FROM bars
            WHERE symbol = ?
              AND DATE(t) = (SELECT MAX(DATE(t)) FROM bars WHERE DATE(t) < ?)
        """, (symbol, ref_date)).fetchone()

        return row[0] if row else None
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":
    # Quick self-check against the hand-verified anchor: HDFCBANK 06-16 = 784.50
    print("HDFCBANK prev-day VWAP:", prev_day_vwap("HDFCBANK"))
    allv = prev_day_vwap_all()
    print(f"batch: {len(allv)} symbols, sample:",
          dict(list(allv.items())[:5]))
