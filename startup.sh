#!/bin/bash
# /home/ramesh/newtrade/startup.sh
# Launched by cron: @reboot sleep 300 && /bin/bash /home/ramesh/newtrade/startup.sh

PROJECT_DIR="/home/ramesh/newtrade"
LOG_FILE="/home/ramesh/log/newtrade.log"
PYTHON="python3 -u"

# ── Fresh log on every startup (no cross-reboot accumulation) ─────────────────
> "$LOG_FILE"
exec >> "$LOG_FILE" 2>&1

# ── Network check ──────────────────────────────────────────────────────────────
ping_with_retry() {
    local host="$1"
    local attempts=5
    local delay=15

    while [[ $attempts -gt 0 ]]; do
        ping -c 1 -q "$host" > /dev/null 2>&1
        if [[ $? -eq 0 ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Network OK (ping $host succeeded)."
            return 0
        fi
        attempts=$((attempts - 1))
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Ping $host failed. Retrying in ${delay}s ($attempts attempts left)..."
        sleep "$delay"
    done

    echo "$(date '+%Y-%m-%d %H:%M:%S') - Network unreachable after retries. Aborting."
    return 1
}

# ── Daemon wrapper — auto-restarts on crash, stops cleanly on success ──────────
start_daemon() {
    local script="$1"
    local restart_delay="${2:-10}"

    echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting $script ..."

    while true; do
        $PYTHON "$PROJECT_DIR/$script"
        exit_status=$?

        if [[ $exit_status -eq 0 ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - $script finished cleanly (exit 0). Not restarting."
            break
        else
            echo "$(date '+%Y-%m-%d %H:%M:%S') - $script crashed (exit $exit_status). Restarting in ${restart_delay}s..."
            sleep "$restart_delay"
        fi
    done
}

# ── Main ───────────────────────────────────────────────────────────────────────
echo "========================================================"
echo "$(date '+%Y-%m-%d %H:%M:%S') - newtrade startup begins"
echo "========================================================"

# 1. Wait for network
if ! ping_with_retry "www.nseindia.com"; then
    exit 1
fi

# 2. Go to project directory
cd "$PROJECT_DIR" || { echo "ERROR: cannot cd to $PROJECT_DIR"; exit 1; }

# 3. Pull latest code
echo "$(date '+%Y-%m-%d %H:%M:%S') - Pulling latest code ..."
git pull
echo "$(date '+%Y-%m-%d %H:%M:%S') - git pull done."

# 4. Install / upgrade any new dependencies
echo "$(date '+%Y-%m-%d %H:%M:%S') - Installing dependencies ..."
pip install -q -r "$PROJECT_DIR/requirements.txt"
echo "$(date '+%Y-%m-%d %H:%M:%S') - pip install done."

# 5. Refresh symbol metadata once (52wk high/low, fundamentals, avg volumes)
echo "$(date '+%Y-%m-%d %H:%M:%S') - Running symbol_info (one-time refresh) ..."
$PYTHON "$PROJECT_DIR/symbol_info.py"
echo "$(date '+%Y-%m-%d %H:%M:%S') - symbol_info done."

# 6. Web dashboard — standalone Flask on port 8080 (daemon, restarts on crash)
start_daemon "web_server.py" 10 &

# 7. Intraday bar collector + push notifier
#    exits cleanly at market close (15:35) — not restarted
start_daemon "main_startup.py" 30 &

# Wait for all background jobs
wait

echo "$(date '+%Y-%m-%d %H:%M:%S') - newtrade startup exited."
