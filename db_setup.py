import sqlite3
import datetime
import os

DB_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nse_intraday.db")
TTL_DAYS = 375   # keep 375 days of bars (matches old MongoDB TTL)


# ── connection ─────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """
    Return a new SQLite connection.
    Each caller (thread) should obtain its own connection — sqlite3 connections
    are not thread-safe by default.
    WAL mode lets readers and the writer run concurrently without blocking.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")    # concurrent reads + single writer
    conn.execute("PRAGMA synchronous=NORMAL")  # safe and faster than FULL
    return conn


# ── schema ─────────────────────────────────────────────────────────────────────

def ensure_table(conn: sqlite3.Connection | None = None) -> None:
    """
    Create the bars table + index if they don't exist yet.
    Pass an open connection to reuse it, or leave None to open/close internally.
    """
    _owned = conn is None
    if _owned:
        conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bars (
                symbol  TEXT    NOT NULL,
                t       TEXT    NOT NULL,   -- ISO-8601 timestamp (IST, tz-aware)
                o       REAL,              -- open
                h       REAL,              -- high
                l       REAL,              -- low
                c       REAL,              -- close
                v       INTEGER,           -- volume
                mp      REAL,              -- mean price  (o+h+l+c)/4
                ac      REAL,              -- amount in crores
                bs      INTEGER,           -- buy/sell pressure: +1 buy, -1 sell, 0 neutral
                PRIMARY KEY (symbol, t)
            )
        """)
        # Secondary index speeds up time-range queries and TTL purge
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bars_t ON bars (t)
        """)
        conn.commit()

        # ── migration: add bs column to existing DB if missing ────────────────
        try:
            conn.execute("ALTER TABLE bars ADD COLUMN bs INTEGER")
            conn.commit()
            print("[db] Migration: added 'bs' (buy/sell) column.")
        except sqlite3.OperationalError:
            pass  # column already exists — nothing to do
    finally:
        if _owned:
            conn.close()


# ── symbol_info table (metadata: fundamentals, 52wk, avg volumes) ──────────────

def ensure_symbol_info_table(conn: sqlite3.Connection | None = None) -> None:
    """
    Create the symbol_info table inside nse_intraday.db if it doesn't exist.
    Also runs migrations to add new columns to an existing table.
    """
    _owned = conn is None
    if _owned:
        conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbol_info (
                symbol      TEXT    PRIMARY KEY,
                name        TEXT,           -- company long name
                sector      TEXT,           -- e.g. Technology, Financial Services
                industry    TEXT,           -- e.g. Software, Banks
                market_cap  INTEGER,        -- ₹ in rupees
                pe_ratio    REAL,           -- trailing P/E ratio
                eps         REAL,           -- trailing EPS ₹
                beta        REAL,           -- volatility vs market
                div_yield   REAL,           -- annual dividend yield (fraction, e.g. 0.015 = 1.5%)
                book_value  REAL,           -- book value per share ₹
                avg_vol_3m  INTEGER,        -- 3-month average daily volume
                avg_vol_10d INTEGER,        -- 10-day average daily volume
                week52_high REAL,           -- 52-week high ₹
                week52_low  REAL,           -- 52-week low ₹
                last_price  REAL,           -- last close price ₹
                updated_at  TEXT            -- ISO-8601 timestamp of last refresh
            )
        """)
        conn.commit()

        # ── migration: add avg_vol_10d to existing DB if missing ─────────────
        try:
            conn.execute("ALTER TABLE symbol_info ADD COLUMN avg_vol_10d INTEGER")
            conn.commit()
            print("[db] Migration: added 'avg_vol_10d' column to symbol_info.")
        except sqlite3.OperationalError:
            pass  # column already exists
    finally:
        if _owned:
            conn.close()


# ── push_log table ────────────────────────────────────────────────────────────

def ensure_push_log_table(conn: sqlite3.Connection | None = None) -> None:
    """Create the push_log table (cleared every startup, holds today's pushes)."""
    _owned = conn is None
    if _owned:
        conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at    TEXT    NOT NULL,   -- ISO-8601 IST timestamp
                title      TEXT    NOT NULL,
                body       TEXT    NOT NULL
            )
        """)
        conn.commit()
    finally:
        if _owned:
            conn.close()


def clear_push_log(conn: sqlite3.Connection | None = None) -> None:
    """Truncate push_log — called once at startup for a clean slate."""
    _owned = conn is None
    if _owned:
        conn = get_connection()
    try:
        conn.execute("DELETE FROM push_log")
        conn.commit()
        print("[db] push_log cleared.")
    finally:
        if _owned:
            conn.close()


def log_push(title: str, body: str, sent_at: str,
             conn: sqlite3.Connection | None = None) -> None:
    """Insert one push record into push_log."""
    _owned = conn is None
    if _owned:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO push_log (sent_at, title, body) VALUES (?, ?, ?)",
            (sent_at, title, body)
        )
        conn.commit()
    finally:
        if _owned:
            conn.close()


# ── TTL cleanup ────────────────────────────────────────────────────────────────

def purge_old_data(conn: sqlite3.Connection | None = None) -> None:
    """
    Delete rows older than TTL_DAYS days.
    Equivalent to MongoDB's expireAfterSeconds TTL index.
    Call once at startup (and optionally daily) to keep DB size in check.
    """
    _owned = conn is None
    if _owned:
        conn = get_connection()
    try:
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=TTL_DAYS)
        ).isoformat()
        cur = conn.execute("DELETE FROM bars WHERE t < ?", (cutoff,))
        conn.commit()
        deleted = cur.rowcount
        if deleted:
            print(f"[db] Purged {deleted} rows older than {TTL_DAYS} days.")
    finally:
        if _owned:
            conn.close()
