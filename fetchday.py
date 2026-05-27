import yfinance as yf
import pytz
import datetime
from symbol_common import symbols
from db_setup import get_connection, ensure_table

ist = pytz.timezone('Asia/Kolkata')


def fetch_and_store() -> int:
    """
    Fetch 1-min bars for all symbols (period='1d') from yfinance
    and upsert into SQLite.

    Upsert strategy — INSERT OR REPLACE:
      - New bar            → inserted
      - Bar already stored → replaced with latest yfinance data
                             (handles yfinance corrections transparently)
      - Late system start  → full day bulk-upserted in one shot

    Returns the total number of bars processed.
    """
    ticker_map = {row[1] + ".NS": row for row in symbols}
    tickers    = list(ticker_map.keys())
    now_str    = datetime.datetime.now(ist).strftime('%H:%M:%S IST')

    print(f"[{now_str}] Fetching {len(tickers)} symbols ...")

    try:
        data = yf.download(
            tickers,
            period="1d",
            interval="1m",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"  [yfinance ERROR] Download failed: {e}")
        return 0

    if data.empty:
        print("  No data returned — market closed / weekend / holiday.")
        return 0

    # Convert index to IST so timestamps are stored in IST
    data.index = data.index.tz_convert(ist)

    conn = get_connection()
    ensure_table(conn)

    total_bars    = 0
    total_upserts = 0

    try:
        for ticker, row_info in ticker_map.items():
            sym_name = row_info[1]

            try:
                # ── extract per-symbol slice ──────────────────────────────
                if len(tickers) == 1:
                    symbol_data = data.copy()
                else:
                    symbol_data = data.xs(ticker, axis=1, level=1)

                # ── drop bad / missing rows ───────────────────────────────
                mask = (
                    (symbol_data["Open"]   >= 1) &
                    (symbol_data["Close"]  >= 1) &
                    (symbol_data["High"]   >= 1) &
                    (symbol_data["Low"]    >= 1) &
                    (symbol_data["Volume"] >= 1)
                )
                filtered = symbol_data[mask].copy()

                if filtered.empty:
                    continue

                # ── derived fields ────────────────────────────────────────
                filtered["mp"] = (
                    filtered["Open"] + filtered["Close"] +
                    filtered["High"] + filtered["Low"]
                ) / 4

                filtered["ac"] = (filtered["mp"] * filtered["Volume"]) / 1_00_00_000

                # buy/sell pressure for bars with significant turnover (≥ ₹1 Cr)
                # +1 close > open  (big order pushed price up   → Buy)
                # -1 close < open  (big order pushed price down → Sell)
                #  0 close == open (neutral)
                filtered["bs"] = (
                    (filtered["Close"] > filtered["Open"]).astype(int)
                    - (filtered["Close"] < filtered["Open"]).astype(int)
                )
                # Only meaningful when turnover is significant; mark rest as 0
                filtered.loc[filtered["ac"] < 1, "bs"] = 0

                # ── build rows for bulk upsert ────────────────────────────
                rows = [
                    (
                        sym_name,
                        ts.isoformat(),                    # IST ISO-8601 string
                        round(float(row["Open"]),   2),
                        round(float(row["High"]),   2),
                        round(float(row["Low"]),    2),
                        round(float(row["Close"]),  2),
                        int(row["Volume"]),
                        round(float(row["mp"]),     2),
                        round(float(row["ac"]),     4),
                        int(row["bs"]),                    # +1 buy / -1 sell / 0 neutral
                    )
                    for ts, row in filtered.iterrows()
                ]

                if rows:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO bars
                            (symbol, t, o, h, l, c, v, mp, ac, bs)
                        VALUES
                            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    total_bars    += len(rows)
                    total_upserts += len(rows)

            except KeyError:
                pass   # symbol not in this download batch
            except Exception as e:
                print(f"  [{sym_name}] Error: {e}")

        conn.commit()

    finally:
        conn.close()

    print(f"  → {total_bars} bars processed | {total_upserts} upserted into SQLite")
    return total_bars
