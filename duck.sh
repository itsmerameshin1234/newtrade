#!/bin/bash

DOMAIN="debdesktop"
TOKEN="11969365-09a8-40ba-90e3-88833d097bf9"
LOG="$HOME/duckdns/duckdns.log"
MAX_LINES=500   # keep log from growing forever

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# ── Fetch current IPs ──────────────────────────────────────────────────────────
IPV4=$(curl -4 -s --max-time 10 https://api.ipify.org 2>/dev/null)
IPV6=$(curl -6 -s --max-time 10 https://api6.ipify.org 2>/dev/null)

if [[ -z "$IPV4" && -z "$IPV6" ]]; then
    echo "$(ts) [ERROR] Could not fetch any IP address. Skipping update." >> "$LOG"
    exit 1
fi

echo "$(ts) [INFO]  IPv4=${IPV4:-none}  IPv6=${IPV6:-none}" >> "$LOG"

# ── Build update URL ───────────────────────────────────────────────────────────
URL="https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}"
[[ -n "$IPV4" ]] && URL+="&ip=${IPV4}"
[[ -n "$IPV6" ]] && URL+="&ipv6=${IPV6}"

# ── Send update ────────────────────────────────────────────────────────────────
RESPONSE=$(curl -s --max-time 10 "$URL")

if [[ "$RESPONSE" == "OK" ]]; then
    echo "$(ts) [OK]    DuckDNS updated → ${DOMAIN}.duckdns.org" >> "$LOG"
else
    echo "$(ts) [FAIL]  DuckDNS response: '${RESPONSE}'" >> "$LOG"
fi

# ── Trim log to last MAX_LINES lines ──────────────────────────────────────────
if [[ $(wc -l < "$LOG") -gt $MAX_LINES ]]; then
    tail -n "$MAX_LINES" "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
