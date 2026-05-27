"""
symbol_info.py
──────────────
Fetches and stores basic metadata for every NSE symbol into nse_intraday.db
(symbol_info table — separate from the bars table).

What it stores per symbol:
  - Company name, sector, industry
  - Market cap, P/E, EPS, Beta, Dividend yield, Book value
  - 10-day  average daily volume
  - 3-month average daily volume
  - 52-week high & low  (computed from 1-year daily history — most accurate)
  - Last traded price

Run once a day (before market open or after close) to keep info fresh.
"""

import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pytz

from symbol_common import symbols
from db_setup import get_connection, ensure_symbol_info_table

IST         = pytz.timezone("Asia/Kolkata")
MAX_WORKERS = 5    # parallel Ticker.info calls — keep low to avoid rate-limiting


# ── Step 1: 52-week high / low via batch daily download ────────────────────────

def fetch_52week(sym_names: list) -> dict:
    """
    Single batch download of 1-year daily bars for all symbols.
    Computes 52-week high/low from actual OHLC — more reliable than Ticker.info.

    Returns:
        { "SYMBOL": {"week52_high": x, "week52_low": y, "last_price": z}, ... }
    """
    tickers = [s + ".NS" for s in sym_names]
    print(f"\n[Step 1] Downloading 1-year daily bars for {len(tickers)} symbols ...")

    try:
        data = yf.download(
            tickers,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"  [ERROR] yfinance download failed: {e}")
        return {}

    if data.empty:
        print("  No data returned.")
        return {}

    result = {}
    for sym in sym_names:
        ticker = sym + ".NS"
        try:
            sd = data.xs(ticker, axis=1, level=1) if len(tickers) > 1 else data.copy()
            sd = sd[sd["Close"] >= 1].dropna(subset=["Close", "High", "Low"])
            if sd.empty:
                continue

            result[sym] = {
                "week52_high": round(float(sd["High"].max()),     2),
                "week52_low":  round(float(sd["Low"].min()),      2),
                "last_price":  round(float(sd["Close"].iloc[-1]), 2),
            }
        except Exception as e:
            print(f"  [{sym}] 52wk error: {e}")

    print(f"  → Got 52-week data for {len(result)} symbols.")
    return result


# ── Step 2: per-symbol metadata via Ticker.info (parallel) ─────────────────────

def fetch_ticker_info(sym_name: str) -> dict:
    """
    Fetch fundamental metadata for one symbol using Ticker.info.
    Returns a flat dict — missing fields are None.
    """
    result = {
        "symbol":      sym_name,
        "name":        None,
        "sector":      None,
        "industry":    None,
        "market_cap":  None,
        "pe_ratio":    None,
        "eps":         None,
        "beta":        None,
        "div_yield":   None,
        "book_value":  None,
        "avg_vol_3m":  None,
        "avg_vol_10d": None,
    }
    try:
        info = yf.Ticker(sym_name + ".NS").info
        result.update({
            "name":       info.get("longName")  or info.get("shortName"),
            "sector":     info.get("sector"),
            "industry":   info.get("industry"),
            "market_cap": info.get("marketCap"),
            "pe_ratio":   info.get("trailingPE"),
            "eps":        info.get("trailingEps"),
            "beta":       info.get("beta"),
            "div_yield":  info.get("dividendYield"),
            "book_value": info.get("bookValue"),
            "avg_vol_3m": (
                info.get("threeMonthAverageVolume")
                or info.get("averageVolume3Month")
                or info.get("averageVolume")
            ),
            "avg_vol_10d": info.get("averageDailyVolume10Day"),
        })
    except Exception as e:
        print(f"  [{sym_name}] info error: {e}")

    return result


# ── main ───────────────────────────────────────────────────────────────────────

def update_symbol_info():
    sym_names = [row[1] for row in symbols]
    now_iso   = datetime.datetime.now(IST).isoformat()

    # ── Step 1: batch 52-week high/low ───────────────────────────────────────
    wk52 = fetch_52week(sym_names)

    # ── Step 2: parallel metadata fetch ──────────────────────────────────────
    print(f"\n[Step 2] Fetching metadata for {len(sym_names)} symbols "
          f"(parallel workers={MAX_WORKERS}) ...")
    meta  = {}
    done  = 0
    total = len(sym_names)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_ticker_info, s): s for s in sym_names}
        for future in as_completed(futures):
            sym        = futures[future]
            meta[sym]  = future.result()
            done      += 1
            print(f"  [{done:>3}/{total}] {sym:<14} "
                  f"PE={meta[sym]['pe_ratio']}  "
                  f"MCap={meta[sym]['market_cap']}")

    # ── Step 3: merge + upsert into nse_intraday.db ───────────────────────────
    conn = get_connection()
    ensure_symbol_info_table(conn)

    print(f"\n[Step 3] Writing {len(sym_names)} rows to nse_intraday.db ...")

    rows = []
    for sym in sym_names:
        m = meta.get(sym, {"symbol": sym})
        w = wk52.get(sym, {})
        rows.append((
            sym,
            m.get("name"),
            m.get("sector"),
            m.get("industry"),
            m.get("market_cap"),
            m.get("pe_ratio"),
            m.get("eps"),
            m.get("beta"),
            m.get("div_yield"),
            m.get("book_value"),
            m.get("avg_vol_3m"),
            m.get("avg_vol_10d"),
            w.get("week52_high"),
            w.get("week52_low"),
            w.get("last_price"),
            now_iso,
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO symbol_info
            (symbol, name, sector, industry, market_cap,
             pe_ratio, eps, beta, div_yield, book_value,
             avg_vol_3m, avg_vol_10d,
             week52_high, week52_low, last_price, updated_at)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Done — {len(rows)} symbols refreshed in nse_intraday.db")
    print(f"  52wk data     : {len(wk52)} symbols")
    print(f"  Metadata      : {sum(1 for m in meta.values() if m.get('name'))} symbols with name")
    print(f"  Updated at    : {now_iso[:19]} IST")
    print(f"{'='*60}\n")

    # ── Quick preview ─────────────────────────────────────────────────────────
    conn = get_connection()
    rows_preview = conn.execute("""
        SELECT symbol, name, sector, pe_ratio,
               week52_low, week52_high, last_price,
               avg_vol_10d, avg_vol_3m
        FROM symbol_info
        ORDER BY market_cap DESC NULLS LAST
        LIMIT 10
    """).fetchall()
    conn.close()

    print(f"  Top 10 by Market Cap:")
    print(f"  {'Symbol':<12} {'Name':<25} {'Sector':<20} {'PE':>6}  "
          f"{'52L':>8}  {'52H':>8}  {'Last':>8}  {'Vol10D':>12}  {'Vol3M':>12}")
    print("  " + "-" * 115)
    for r in rows_preview:
        print(f"  {str(r[0]):<12} {str(r[1] or '')[:24]:<25} {str(r[2] or '')[:19]:<20} "
              f"{str(round(r[3],1)) if r[3] else '-':>6}  "
              f"{str(r[4]) if r[4] else '-':>8}  "
              f"{str(r[5]) if r[5] else '-':>8}  "
              f"{str(r[6]) if r[6] else '-':>8}  "
              f"{str(r[7]) if r[7] else '-':>12}  "
              f"{str(r[8]) if r[8] else '-':>12}")


if __name__ == "__main__":
    update_symbol_info()
