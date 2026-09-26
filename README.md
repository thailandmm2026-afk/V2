# V2Box Key Shop — 3x-ui Edition

Telegram buyer shop + password-protected Admin Web Panel + real 3x-ui client creation + per-client traffic.

## What it does
- Admin creates a VLESS client directly in 3x-ui.
- The client gets UUID, expiry and traffic quota.
- The generated VLESS/REALITY link is sent to the Telegram user.
- Admin dashboard reads upload/download/total/last-online from 3x-ui.
- Buyer can open My Keys in Telegram.
- Admin can disable/enable locally and delete the client from 3x-ui.

## Important
This project targets the 3x-ui API and uses the `/login` and `/panel/api/inbounds/...` endpoints. The official 3x-ui API documentation lists login, addClient, traffic and online endpoints.

## Install on your current VPS
unzip v2box-shop-full.zip -d /root/v2box-shop
cd /root/v2box-shop
cp .env.example .env
nano .env
bash install.sh
python app.py

Admin panel:
http://SERVER_IP:8080/

For 24/7:
cp v2box-shop.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now v2box-shop
systemctl status v2box-shop --no-pager

## .env
- BOT_TOKEN = BotFather token
- ADMIN_IDS = Telegram numeric ID(s)
- XUI_URL = https://127.0.0.1:2053
- XUI_USERNAME / XUI_PASSWORD = 3x-ui login
- XUI_INBOUND_ID = your VLESS inbound ID, usually 1 in this setup
- REALITY_PUBLIC_KEY / SHORT_ID / SPX = values matching your current inbound
- SERVER_ADDRESS = VPS public IP or domain

Do not publish .env or your 3x-ui password.
