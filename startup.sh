#!/bin/bash
# /home/ramesh/newtrade/startup.sh
# Launched by cron: @reboot sleep 300 && /bin/bash /home/ramesh/newtrade/startup.sh

PROJECT_DIR="/home/ramesh/newtrade"
LOG_FILE="/home/ramesh/log/newtrade.log"
PYTHON="python3 -u"
PB_TOKEN="o.jf9XoTk0S42G4FkjfhD5PZFiLcWAbj9r"

# ── Fresh log on every startup (no cross-reboot accumulation) ─────────────────
> "$LOG_FILE"
exec >> "$LOG_FILE" 2>&1

# ── Helpers ───────────────────────────────────────────────────────────────────
push() {
    local title="$1" body="$2"
    curl -s -u "${PB_TOKEN}:" https://api.pushbullet.com/v2/pushes \
        -d type=note \
        --data-urlencode "title=${title}" \
        --data-urlencode "body=${body}" \
        -o /dev/null || true
}

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) - $*"; }

# ── Network check ──────────────────────────────────────────────────────────────
ping_with_retry() {
    local host="$1" attempts=5 delay=15
    while [[ $attempts -gt 0 ]]; do
        ping -c 1 -q "$host" > /dev/null 2>&1 && {
            log "Network OK (ping $host succeeded)."
            return 0
        }
        attempts=$((attempts-1))
        log "Ping $host failed. Retrying in ${delay}s ($attempts left)..."
        sleep "$delay"
    done
    log "Network unreachable after retries. Aborting."
    return 1
}

# ── Daemon wrapper ─────────────────────────────────────────────────────────────
start_daemon() {
    local script="$1" restart_delay="${2:-10}"
    log "Starting $script ..."
    while true; do
        $PYTHON "$PROJECT_DIR/$script"
        local rc=$?
        if [[ $rc -eq 0 ]]; then
            log "$script finished cleanly (exit 0). Not restarting."
            break
        else
            log "$script crashed (exit $rc). Restarting in ${restart_delay}s..."
            sleep "$restart_delay"
        fi
    done
}

# ── Main ───────────────────────────────────────────────────────────────────────
echo "========================================================"
log "newtrade startup begins"
echo "========================================================"

# 1. Wait for network
if ! ping_with_retry "www.nseindia.com"; then
    push "❌ NSE Startup Failed" "$(ts) — Network unreachable. System NOT running."
    exit 1
fi

# 2. cd to project
cd "$PROJECT_DIR" || { log "ERROR: cannot cd to $PROJECT_DIR"; exit 1; }

# 3. git pull
log "Pulling latest code ..."
GIT_OUT=$(git pull 2>&1); GIT_EXIT=$?
echo "$GIT_OUT"
log "git pull done (exit $GIT_EXIT)."
GIT_STATUS=$( [[ $GIT_EXIT -eq 0 ]] && echo "✓ $(echo "$GIT_OUT" | grep -v '^$' | tail -1)" || echo "✗ failed (exit $GIT_EXIT)" )

# 4. pip install
log "Installing dependencies ..."
pip install -q -r "$PROJECT_DIR/requirements.txt" >> "$LOG_FILE" 2>&1
PIP_EXIT=$?; PIP_STATUS=$( [[ $PIP_EXIT -eq 0 ]] && echo "✓" || echo "✗ exit $PIP_EXIT" )
log "pip install done (exit $PIP_EXIT)."

# 5. symbol_info
log "Running symbol_info ..."
$PYTHON "$PROJECT_DIR/symbol_info.py"
SYM_EXIT=$?; SYM_STATUS=$( [[ $SYM_EXIT -eq 0 ]] && echo "✓" || echo "✗ exit $SYM_EXIT" )
log "symbol_info done (exit $SYM_EXIT)."

# 6. Web server
start_daemon "web_server.py" 10 &

# 7. main_startup.py
start_daemon "main_startup.py" 30 &
MAIN_PID=$!

# ── Single push: system status ────────────────────────────────────────────────
sleep 5
if kill -0 $MAIN_PID 2>/dev/null; then
    push "✅ NSE System Live" \
"Time        : $(ts)
git pull    : $GIT_STATUS
pip install : $PIP_STATUS
symbol_info : $SYM_STATUS
main_startup: running (PID $MAIN_PID)"
else
    push "❌ NSE Startup Failed" \
"Time        : $(ts)
git pull    : $GIT_STATUS
pip install : $PIP_STATUS
symbol_info : $SYM_STATUS
main_startup: CRASHED — check log"
fi

wait
log "newtrade startup exited."
