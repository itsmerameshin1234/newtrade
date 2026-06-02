import time
import datetime
from fetchday import fetch_and_store
from db_setup import ensure_table, ensure_symbol_info_table, ensure_push_log_table, clear_push_log, purge_old_data
from notifier import check_and_notify_spikes

# ── market window ─────────────────────────────────────────────────────────────
MARKET_OPEN  = (9,  15)   # 09:15
MARKET_CLOSE = (15, 35)   # 15:35 — 5-min buffer to ensure last bar (15:29) is captured

CHECK_EVERY_SECS = 20     # poll interval — fine enough to never miss a minute


def in_market_hours(now: datetime.datetime) -> bool:
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def main():
    last_run_minute = None          # (hour, minute) of last successful run

    # ── one-time DB init ──────────────────────────────────────────────────────
    ensure_table()              # create bars table if not exists
    ensure_symbol_info_table()  # create symbol_info table if not exists (no-op if exists)
    ensure_push_log_table()     # create push_log table if not exists
    clear_push_log()            # purge push_log records older than 12 hrs
    purge_old_data()            # drop rows older than 375 days (TTL equivalent)

    print("=" * 50)
    print("  NSE Intraday Bar Collector  [SQLite]")
    print(f"  Market : {MARKET_OPEN[0]:02d}:{MARKET_OPEN[1]:02d} → "
          f"{MARKET_CLOSE[0]:02d}:{MARKET_CLOSE[1]:02d} IST")
    print(f"  Poll   : every {CHECK_EVERY_SECS}s")
    print("  Ctrl+C to stop")
    print("=" * 50)

    while True:
        now     = datetime.datetime.now()
        cur_min = (now.hour, now.minute)

        if in_market_hours(now):

            if cur_min != last_run_minute:
                # ── new minute → trigger fetch ────────────────────────────
                print(f"\n[{now.strftime('%H:%M:%S')}] "
                      f"Minute {cur_min[0]:02d}:{cur_min[1]:02d} — starting fetch ...")
                try:
                    fetch_and_store()
                    check_and_notify_spikes()   # push if spike leaderboard changed
                except Exception as e:
                    print(f"  [ERROR] {e}")
                finally:
                    # Always mark this minute as attempted.
                    # Even on error we don't retry within the same minute —
                    # next fetch (next minute) will pick up the corrected data
                    # via upsert anyway.
                    last_run_minute = cur_min

        else:
            if now < datetime.datetime.now().replace(
                    hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0):
                print(f"[{now.strftime('%H:%M:%S')}] Pre-market. Waiting ...", end="\r")

            elif (now.hour, now.minute) > MARKET_CLOSE:
                print(f"\n[{now.strftime('%H:%M:%S')}] Market closed. Exiting.")
                break

        time.sleep(CHECK_EVERY_SECS)


if __name__ == "__main__":
    main()
