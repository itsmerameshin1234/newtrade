#!/bin/bash
# backup_gdrive.sh
# Backs up nse_intraday.db (gzipped) + newtrade.log to Google Drive at 15:40 IST.
# Cron: 40 15 * * 1-5  /bin/bash /home/ramesh/newtrade/backup_gdrive.sh
#
# Setup (one-time): rclone config  — name the remote "gdrive"
#
# Retention: last 2 days for both DB and log (dated files only, no LATEST copy)
# Push notifications: sent via Pushbullet at start and on completion

set -uo pipefail          # -u catches undefined vars; NO -e so grep/rclone won't kill us

DB="/home/ramesh/newtrade/nse_intraday.db"
LOGFILE="/home/ramesh/log/newtrade.log"
REMOTE="gdrive:NSE_Backups"
DATE=$(date '+%Y-%m-%d')
ERRORS=0
ERROR_MSGS=""

PB_TOKEN="o.jf9XoTk0S42G4FkjfhD5PZFiLcWAbj9r"

log()  { echo "$(date '+%Y-%m-%d %H:%M:%S') - [backup] $*" | tee -a "$LOGFILE"; }
fail() {
    log "ERROR: $*"
    ERRORS=$((ERRORS+1))
    ERROR_MSGS="${ERROR_MSGS}• $*\n"
}

push() {
    local title="$1" body="$2"
    curl -s -u "${PB_TOKEN}:" https://api.pushbullet.com/v2/pushes \
        -d type=note \
        --data-urlencode "title=${title}" \
        --data-urlencode "body=${body}" \
        -o /dev/null || true
}

DB_SIZE_START="—"
[[ -f "$DB" ]] && DB_SIZE_START=$(du -h "$DB" | cut -f1)

log "========================================"
log "Google Drive backup started — $DATE"
log "========================================"

# ─────────────────────────────────────────────────────────────────────────────
# 1. DATABASE  (gzip compressed, 2-day retention)
# ─────────────────────────────────────────────────────────────────────────────
DB_GZ_SIZE="—"
if [[ ! -f "$DB" ]]; then
    fail "DB not found at $DB"
else
    DB_SIZE=$(du -h "$DB" | cut -f1)
    TMP_GZ="/tmp/nse_intraday_${DATE}.db.gz"

    log "DB: compressing $DB_SIZE ..."
    if gzip -c "$DB" > "$TMP_GZ"; then
        DB_GZ_SIZE=$(du -h "$TMP_GZ" | cut -f1)
        log "DB: $DB_SIZE → $DB_GZ_SIZE  uploading to ${REMOTE}/db/"

        if rclone copyto "$TMP_GZ" "${REMOTE}/db/nse_intraday_${DATE}.db.gz" \
                --drive-acknowledge-abuse --retries 3 --stats-one-line 2>&1; then
            log "DB upload OK: nse_intraday_${DATE}.db.gz ($DB_GZ_SIZE)"
        else
            fail "DB upload to Google Drive failed"
        fi
        rm -f "$TMP_GZ"
    else
        fail "DB gzip compression failed"
        rm -f "$TMP_GZ"
    fi

    # Prune DB backups older than 2 days
    CUTOFF=$(date -d '2 days ago' '+%Y-%m-%d')
    log "Pruning DB backups older than $CUTOFF ..."
    while IFS= read -r fname; do
        fdate="${fname#nse_intraday_}"; fdate="${fdate%.db.gz}"
        if [[ "$fdate" < "$CUTOFF" ]]; then
            log "  Deleting: $fname"
            rclone deletefile "${REMOTE}/db/${fname}" 2>&1 || true
        fi
    done < <(rclone lsf "${REMOTE}/db/" --format "n" 2>/dev/null \
             | grep -E '^nse_intraday_[0-9]{4}-[0-9]{2}-[0-9]{2}\.db\.gz$' || true)
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. LOG FILE  (2-day retention)
# ─────────────────────────────────────────────────────────────────────────────
LOG_SIZE_STR="—"
if [[ ! -f "$LOGFILE" ]]; then
    fail "Log file not found at $LOGFILE"
else
    LOG_SIZE_STR=$(du -h "$LOGFILE" | cut -f1)
    log "Log: $LOG_SIZE_STR  uploading to ${REMOTE}/logs/"

    if rclone copyto "$LOGFILE" "${REMOTE}/logs/newtrade_${DATE}.log" \
            --drive-acknowledge-abuse --retries 3 --stats-one-line 2>&1; then
        log "Log upload OK: newtrade_${DATE}.log ($LOG_SIZE_STR)"
    else
        fail "Log upload to Google Drive failed"
    fi

    # Prune log backups older than 2 days
    CUTOFF=$(date -d '2 days ago' '+%Y-%m-%d')
    log "Pruning log backups older than $CUTOFF ..."
    while IFS= read -r fname; do
        fdate="${fname#newtrade_}"; fdate="${fdate%.log}"
        if [[ "$fdate" < "$CUTOFF" ]]; then
            log "  Deleting: $fname"
            rclone deletefile "${REMOTE}/logs/${fname}" 2>&1 || true
        fi
    done < <(rclone lsf "${REMOTE}/logs/" --format "n" 2>/dev/null \
             | grep -E '^newtrade_[0-9]{4}-[0-9]{2}-[0-9]{2}\.log$' || true)
fi

# ─────────────────────────────────────────────────────────────────────────────
# PUSH: Backup result + shutdown notice
# ─────────────────────────────────────────────────────────────────────────────
log "========================================"
if [[ $ERRORS -eq 0 ]]; then
    log "Backup complete — no errors"
    log "========================================"
    push "✅ NSE Backup Complete — Shutting Down" \
"Date   : $DATE
DB     : $DB_SIZE_START → $DB_GZ_SIZE (gzip)
Log    : $LOG_SIZE_STR
Status : All uploads successful
Action : Shutdown in 60s"
else
    log "Backup finished with $ERRORS error(s) — check above"
    log "========================================"
    # Build error list (convert \n escapes to real newlines for printf)
    ERR_BODY=$(printf "%b" "$ERROR_MSGS")
    push "❌ NSE Backup Failed ($ERRORS error) — Shutting Down" \
"Date   : $DATE
DB     : $DB_SIZE_START → $DB_GZ_SIZE (gzip)
Log    : $LOG_SIZE_STR
Errors :
${ERR_BODY}
Action : Shutdown in 60s"
fi

log "Shutting down system in 60 seconds ..."
sleep 60
sudo /sbin/shutdown -h now "Scheduled shutdown after backup"
