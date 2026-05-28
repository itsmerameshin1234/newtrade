#!/bin/bash
# ssl-setup.sh — Get SSL cert via DuckDNS DNS challenge (no port 443 needed)
# Run AFTER setup.sh if you want HTTPS later.
# DuckDNS token is stored in /etc/letsencrypt/duckdns.ini (not in git)

DOMAIN="debdesktop.duckdns.org"
EMAIL="itsmerameshin@gmail.com"

echo "=== Creating DuckDNS credentials file ==="
sudo mkdir -p /etc/letsencrypt
sudo tee /etc/letsencrypt/duckdns.ini << EOF
dns_duckdns_token = YOUR_DUCKDNS_TOKEN_HERE
EOF
sudo chmod 600 /etc/letsencrypt/duckdns.ini

echo "Edit /etc/letsencrypt/duckdns.ini and replace YOUR_DUCKDNS_TOKEN_HERE with your token, then run:"
echo ""
echo "  sudo certbot certonly \\"
echo "    --authenticator dns-duckdns \\"
echo "    --dns-duckdns-credentials /etc/letsencrypt/duckdns.ini \\"
echo "    --dns-duckdns-propagation-seconds 60 \\"
echo "    -d $DOMAIN \\"
echo "    --non-interactive --agree-tos -m $EMAIL"
echo ""
echo "Cert will be saved to: /etc/letsencrypt/live/$DOMAIN/"
