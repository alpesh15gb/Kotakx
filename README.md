# Sahi-Inspired Kotak Neo Trading Terminal (Backend + Web + Android)

FastAPI/Kotak Neo backend, Next.js standalone web terminal, and native Android client. The Kotak integration uses the maintained [Kotak Neo Python SDK (`kotakneoapi==3.0.13`)](https://github.com/Kotak-Neo/kotak-neo-python).

**Validation status:** Compose configuration validation passed. A local Nginx 1.24 HTTPS smoke test served the standalone web build and verified API authentication, the market feed, and the order feed in paper mode. Container startup on the VPS and live Kotak connectivity still require verification.

## Production architecture

- `backend/`: one Python 3.11 FastAPI/Uvicorn worker, running as UID/GID `10001`, with a read-only root filesystem and writable named SQLite volume.
- `web/`: Next.js `output: "standalone"`, running as UID/GID `1001`, with a read-only root filesystem and a small writable cache.
- `docker-compose.yml` publishes both services on host loopback and configures health checks, restart policies, resource limits, and log rotation. Compose reads the root-owned `.env` file and passes the settings to the backend as environment variables.
- `deploy/nginx/kotak-terminal.conf`: system-level Nginx, not a container, terminates Let's Encrypt TLS, redirects HTTP to HTTPS, proxies `/api/*` and `/health` to FastAPI, proxies the exact `/ws/marketfeed` and `/ws/orderfeed` WebSocket paths with upgrade headers and 24-hour timeouts, and proxies all other paths to Next.js.
- Same-origin web routing is deliberate: `web/next.config.mjs` has no rewrites, and the existing client uses an empty `NEXT_PUBLIC_API_URL` for browser `/api/*` requests and derives `wss://<host>` from the page origin. Do **not** set it to `/api`, because client paths already begin with `/api/`.

## Operating limits and safety cautions

- **Single operator only.** This is not a multi-user service. Run exactly one backend replica and one Uvicorn worker; do not scale the backend or let multiple processes share the SQLite volume.
- SQLite is persisted in Docker volume `sahi-kotak-terminal_backend_data`. It is suitable for this single-node deployment, not high availability or concurrent replicas. Never use `docker compose down -v` during routine updates or rollback.
- **No automatic retry of uncertain orders.** A timeout or unconfirmed submission may already have reached Kotak. Check the broker order book, order history, positions, and trades before manually resubmitting anything.
- This terminal currently accepts CNC, MIS, NRML and MTF products for supported segments.
- Kotak permits a maximum of **two order sessions**. Count sessions opened by this app, Kotak's other clients, and any other integration; log out unused sessions before attempting another live login.
- Login and all subsequent order/feed calls leave through the same backend container and VPS egress IP. The login IP and order IP must remain identical. Use a genuinely reserved static IPv4, not a dynamic address observed once.
- **Kotak Neo credentials are entered at runtime** in the browser login dialog (consumer key, mobile number, UCC, TOTP, and MPIN). The app keeps the broker session in memory and does not persist those credentials. The separate terminal access password and session-signing secret are deployment secrets stored only in root-only `.env`; SQLite persists application preferences/state, not Kotak login credentials.
- Paper and live modes share the terminal’s risk settings. Check order defaults, the kill switch, and automatic P&L protection before switching to live mode. Keep the terminal in paper mode until you verify the static-IP whitelist, authentication, order reconciliation, and risk controls.

## Deploy on the shared VPS

The web container uses host loopback port `18740` and the API uses `18741`. Container ports remain `3001` and `8000`. Check the host socket table as well as Docker's published ports before deploying.

Run these commands in the same Bash session as root or with equivalent privileges. Replace the example values with a dedicated hostname, its VPS IPv4 address, and your certificate contact email. The steps assume an existing Docker Engine 28+ with Compose v2 and system Nginx that includes `/etc/nginx/sites-enabled/*`. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)

### 1. Check DNS, ports, and Nginx

```bash
DOMAIN=trade.example.com
VPS_IP=203.0.113.10
CERTBOT_EMAIL=admin@example.com
SITE=kotakx-terminal
ENABLED=zz-$SITE
ACME_ROOT=/var/www/kotakx-acme

getent ahostsv4 "$DOMAIN"
curl -4fsS https://api.ipify.org
docker version --format '{{.Server.Version}}'
docker compose version
df -h /var/lib/docker /opt
free -h
nginx -t
nginx -T 2>/dev/null | grep -E 'include.*/sites-enabled/'
ss -ltnp '( sport = :18740 or sport = :18741 )'
docker ps --format '{{.Names}} {{.Ports}}' | grep -E ':(18740|18741)->'
nginx -T 2>/dev/null | grep -F -e "$DOMAIN" -e kotakx_backend -e kotakx_web -e kotakx_connection_upgrade
```

For a first deployment, the last three checks should show no matching listeners, container bindings, or Nginx names. The DNS record must point to this VPS. If an AAAA record exists, it must route to the same Nginx host over IPv6. Use the existing public HTTP and HTTPS listeners and keep the app ports on loopback. Check an existing site's HTTPS response before and after the deployment.

### 2. Clone and start only Kotakx

```bash
git clone https://github.com/alpesh15gb/Kotakx.git /opt/sahi-kotak-terminal
cd /opt/sahi-kotak-terminal
umask 077
if [ ! -e .env ]; then
  {
    printf 'KOTAK_TERMINAL_ACCESS_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    printf 'KOTAK_TERMINAL_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)"
    printf 'KOTAK_TERMINAL_CORS_ORIGINS=["https://%s"]\n' "$DOMAIN"
    printf 'KOTAK_TERMINAL_PUBLIC_STATIC_IP=%s\n' "$VPS_IP"
    grep -Ev '^KOTAK_TERMINAL_(ACCESS_PASSWORD|SESSION_SECRET|CORS_ORIGINS|PUBLIC_STATIC_IP)=' .env.example
  } > .env
fi
chown root:root .env
chmod 600 .env
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
curl -fsS http://127.0.0.1:18741/health
curl -fsS -o /dev/null http://127.0.0.1:18740/
```

Save the terminal access password from the root-only `.env` in your password manager. The static IP setting is informational. Verify the actual reserved egress IP before enabling any live Kotak session.

### 3. Add the new hostname to the existing Nginx

Check `certbot --version`. If Certbot is absent, install it using the VPS's package manager before running the certificate command. This guide uses Certbot in webroot mode so it can issue a certificate without editing Nginx files. The temporary HTTP site serves only the ACME challenge for the new hostname. [Certbot webroot mode](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot)

```bash
test ! -e "/etc/nginx/sites-available/$SITE" &&
test ! -e "/etc/nginx/sites-enabled/$ENABLED" &&
test ! -L "/etc/nginx/sites-enabled/$ENABLED" &&
test ! -d "/etc/letsencrypt/live/$SITE"
```

Continue only if those checks succeed. If the host uses a different include directory, add this one site under that directory instead.

```bash
install -d -m 755 "$ACME_ROOT/.well-known/acme-challenge"
cat > "/etc/nginx/sites-available/$SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location ^~ /.well-known/acme-challenge/ {
        root $ACME_ROOT;
    }
    location / { return 404; }
}
EOF
ln -s "/etc/nginx/sites-available/$SITE" "/etc/nginx/sites-enabled/$ENABLED"
nginx -t && systemctl reload nginx
```

Proceed after the syntax check and reload succeed. Issue the site certificate and replace only the temporary Kotakx site.

```bash
certbot certonly --webroot -w "$ACME_ROOT" -d "$DOMAIN" --cert-name "$SITE" \
  --non-interactive --agree-tos -m "$CERTBOT_EMAIL" \
  --deploy-hook 'nginx -t && systemctl reload nginx'

cp "/etc/nginx/sites-available/$SITE" "/root/$SITE.http-only.conf"
sed -e "s/trade\.example\.com/${DOMAIN}/g" \
    -e "s|/etc/letsencrypt/live/${DOMAIN}/|/etc/letsencrypt/live/${SITE}/|g" \
    deploy/nginx/kotak-terminal.conf > "/etc/nginx/sites-available/$SITE"
if ! nginx -t; then
  cp "/root/$SITE.http-only.conf" "/etc/nginx/sites-available/$SITE"
else
  systemctl reload nginx
fi
```

The reload is graceful. Keep the existing default site, other virtual hosts, firewall rules, and Nginx service configuration in place. If Nginx reports a listener-option warning, align the new site's listen options with the existing sites on the affected address before reloading. [Nginx reload behavior](https://nginx.org/en/docs/control.html#reconfiguration)

### 4. Verify the deployment

```bash
docker compose ps
curl -fsS -o /dev/null -w '%{http_code} %{redirect_url}\n' "http://$DOMAIN/"
curl -fsS "https://$DOMAIN/health"
curl -fsSI "https://$DOMAIN/"
curl -sS -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/api/v1/session"
certbot renew --cert-name "$SITE" --dry-run
```

Expect both containers healthy, a 301 redirect, a trusted HTTPS response, `{"status":"ok"}`, and a 401 from the unauthenticated session endpoint. Unlock the terminal in a browser and confirm it starts in Paper mode. In browser developer tools, verify HTTP 101 for both `/ws/marketfeed` and `/ws/orderfeed`. Place and close a paper order and confirm it appears in the order book. Check an existing hostname again to confirm it still responds as before.

Before a live Kotak login, register this VPS's reserved outbound IPv4 under Kotak Neo Trade API's Static IP Whitelist and confirm the whitelist is active.

To take Kotakx offline, unlink just `/etc/nginx/sites-enabled/zz-kotakx-terminal`, run `nginx -t && systemctl reload nginx`, and run `docker compose stop` in `/opt/sahi-kotak-terminal`. This preserves the SQLite volume and other sites.

## Operations

### Logs and health

```bash
cd /opt/sahi-kotak-terminal
sudo docker compose ps
sudo docker compose logs --tail=200 backend web
sudo docker compose logs -f --tail=100 backend
sudo journalctl -u nginx --no-pager -n 100
curl -fsS https://"$DOMAIN"/health
```

Container logs are capped at 10 MB x 3 files per service. Protect host, Nginx, and backup logs as operational data; never paste credentials into them.

### Consistent SQLite backup

Use SQLite's online backup API rather than copying a live database file. Run this on the VPS and copy the resulting backup off-server encrypted:

```bash
cd /opt/sahi-kotak-terminal
install -d -m 700 backups
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
sudo docker compose exec -T backend python -c "import sqlite3; source=sqlite3.connect('/app/data/trading_terminal.db'); target=sqlite3.connect('/tmp/trading_terminal.db'); source.backup(target); target.close(); source.close()"
sudo docker compose cp "backend:/tmp/trading_terminal.db" "backups/trading_terminal-${STAMP}.db"
sudo docker compose exec -T backend rm -f /tmp/trading_terminal.db
sudo chmod 600 "backups/trading_terminal-${STAMP}.db"
```

Back up `.env` separately with strict access control or keep the generated secrets in a password manager. Rotating `KOTAK_TERMINAL_SESSION_SECRET` invalidates existing terminal sessions. Schedule backups and off-server retention according to your recovery policy; never commit databases or `.env` to source control.

To restore a backup, stop the services and replace the database through a one-off root helper so stale WAL files are removed and ownership is correct. Set `RESTORE_FILE` to the chosen backup first:

```bash
cd /opt/sahi-kotak-terminal
RESTORE_FILE="backups/trading_terminal-YYYYMMDDTHHMMSSZ.db"
BACKEND_IMAGE="$(sudo docker inspect --format '{{.Image}}' kotak-terminal-backend)"
sudo docker compose stop backend web
sudo docker run --rm --user 0 \
  -v sahi-kotak-terminal_backend_data:/data \
  -v "$PWD/${RESTORE_FILE}:/restore.db:ro" \
  --entrypoint sh "$BACKEND_IMAGE" \
  -c 'rm -f /data/trading_terminal.db /data/trading_terminal.db-wal /data/trading_terminal.db-shm && cp /restore.db /data/trading_terminal.db && chown 10001:10001 /data/trading_terminal.db && chmod 600 /data/trading_terminal.db'
sudo docker compose up -d
```

### Update

Take a backup first, then rebuild from the new release and re-check health/TLS:

```bash
cd /opt/sahi-kotak-terminal
sudo docker compose build --pull
sudo docker compose up -d --build
sudo docker compose ps
curl -fsS https://"$DOMAIN"/health
sudo certbot renew --dry-run
```

To change secrets or other environment settings, use `sudoedit .env`, then run `sudo docker compose up -d` to recreate affected containers.

### Rollback

Retain the previous release directory (for example, `/opt/sahi-kotak-terminal-previous`) and the pre-update database backup. Roll back application images/code with the same Compose project name so the existing SQLite volume is reused:

```bash
cd /opt/sahi-kotak-terminal
sudo docker compose down
sudo install -m 600 /opt/sahi-kotak-terminal/.env /opt/sahi-kotak-terminal-previous/.env
cd /opt/sahi-kotak-terminal-previous
sudo docker compose -p sahi-kotak-terminal up -d --build
sudo docker compose -p sahi-kotak-terminal ps
```

Never add `-v` to `down`. Restore a database backup only when required; an application rollback normally keeps the current volume. Nginx configuration under `/etc/nginx` is independent of the application release and must be rolled back separately only if its proxy/TLS settings caused the incident.

## Local development (without Docker)

### Backend

```powershell
cd backend
py -3.11 -m venv .venv-v3
.\.venv-v3\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-v3\Scripts\python.exe -m pytest -v
.\.venv-v3\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --ws wsproto
```

Local development uses the backend's development defaults. Do not reuse the production `.env`; production mode requires the access password, session secret, secure cookie, and explicit CORS origins.

### Web

Use Node.js 22, matching the Docker build.

```powershell
cd web
npm ci
$env:NEXT_PUBLIC_API_URL="http://127.0.0.1:8000"
$env:NEXT_PUBLIC_WS_URL="ws://127.0.0.1:8000"
npm run dev
```

### Android

Use JDK 17 and Android SDK Platform 35. From the `android/` directory, build a debug APK with `./gradlew assembleDebug` or `./gradlew.bat assembleDebug` on Windows. The emulator connects to the backend on the host at port 8000.

For a release, pass the VPS domain as Gradle properties and sign the output with your Android release key.

```bash
bash ./gradlew bundleRelease \
  -PRELEASE_API_BASE_URL=https://trade.example.com/ \
  -PRELEASE_WS_BASE_URL=wss://trade.example.com
```
