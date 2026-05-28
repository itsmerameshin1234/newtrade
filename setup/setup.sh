#!/bin/bash
# setup.sh — Rebuild web access stack on a fresh system
# Run as: bash setup.sh
# Access after setup:
#   Trading app : http://debdesktop.duckdns.org:8080/
#   SSH Terminal: http://debdesktop.duckdns.org:8080/terminal/

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NEWTRADE_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Step 1: Install packages ==="
sudo apt update
sudo apt install -y nginx shellinabox python3-pip
sudo pip3 install certbot-dns-duckdns

echo "=== Step 2: Configure shellinabox ==="
sudo cp "$SCRIPT_DIR/shellinabox-defaults" /etc/default/shellinabox
sudo systemctl enable shellinabox
sudo systemctl restart shellinabox

echo "=== Step 3: Configure nginx ==="
sudo cp "$SCRIPT_DIR/nginx.conf" /etc/nginx/sites-available/wetty
sudo ln -sf /etc/nginx/sites-available/wetty /etc/nginx/sites-enabled/wetty
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo "=== Step 4: Start trading web app on port 8090 ==="
# web_server.py already has PORT=8090 set
cd "$NEWTRADE_DIR"
pkill -f web_server.py 2>/dev/null || true
nohup python3 -u web_server.py &
echo "web_server.py started on port 8090"

echo ""
echo "=== Setup complete ==="
echo "Trading app : http://debdesktop.duckdns.org:8080/"
echo "SSH Terminal: http://debdesktop.duckdns.org:8080/terminal/"
echo ""
echo "NOTE: Make sure port 8080 is forwarded in your Jio router to this machine."
echo "NOTE: To get SSL cert (optional): see ssl-setup.sh"
