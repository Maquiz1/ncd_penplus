# Deploying NCD - PENPLUS STUDY (Hostinger, Docker + PostgreSQL)

Server: `31.97.187.66`, project folder `/opt/ncd-penplus`. Same approach as tamris and todolist.

## How it fits on the server

- Address: **https://penplus.tamris.org**. The host nginx (the one serving the other tamris.org subdomains, with certbot) forwards it to `127.0.0.1:8090`.
- Containers: `penplus-db` (PostgreSQL 15), `penplus-redis`, `penplus-web` (gunicorn + WhiteNoise for static files), `penplus-worker` and `penplus-beat` (Celery: SMS, LTFU rule, data checks, monthly reports).
- Only `penplus-web` has a port, and only on `127.0.0.1:WEB_PORT` (default 8090), so nothing is reachable from outside except through nginx and HTTPS.
- Data lives in Docker volumes (`ncd-penplus_postgres_data`, `ncd-penplus_media`, …).

## First deployment

```bash
ss -tlnp | grep ':8090 ' || echo "8090 is free"     # otherwise pick another WEB_PORT
cd /opt/ncd-penplus
git pull
cp .env.production.example .env
nano .env        # DJANGO_SECRET_KEY (openssl rand -base64 48), DB_PASSWORD
chmod 600 .env
chmod +x deploy-penplus.sh
./deploy-penplus.sh --first
```

`--first` loads the Tanzania locations and the PEN-Plus setup (sites, study, diseases, medicines, lab and investigation catalogues), then asks for the first administrator account.

Check from the server: `curl -sI http://127.0.0.1:8090/accounts/login/` should answer (a `301` to HTTPS is normal).

## Domain and HTTPS

1. DNS: add an A record `penplus` → `31.97.187.66` for tamris.org (check with `dig +short penplus.tamris.org`).
2. nginx site and certificate (as root):
   ```bash
   cp /opt/ncd-penplus/docker/nginx-penplus.conf /etc/nginx/sites-available/penplus.tamris.org
   ln -s /etc/nginx/sites-available/penplus.tamris.org /etc/nginx/sites-enabled/
   nginx -t && systemctl reload nginx
   certbot --nginx -d penplus.tamris.org
   ```
3. Open https://penplus.tamris.org and log in. Logins only work over HTTPS (secure cookies).

## Updates

```bash
cd /opt/ncd-penplus
git pull
./deploy-penplus.sh
```

The script backs up the database and uploaded files to `~/backups/penplus`, builds the new image while the old one keeps serving, runs migrations, switches containers, and updates roles and scheduled jobs. It prints the rollback and restore commands at the end.

## Useful commands

```bash
docker compose ps
docker compose logs -f penplus-web
docker compose logs -f penplus-worker
docker compose exec penplus-web python manage.py shell
docker compose exec penplus-web python manage.py detect_data_issues
docker compose restart penplus-web penplus-worker penplus-beat     # after changing .env
```

## Go-live

- Load the EDCS data (fresh dump of the live EDCS, then `import_penplus_edcs`). The importer reads a MySQL copy of EDCS, so it needs a temporary MySQL container with the dump loaded. Do this as a separate, planned step.
- Email: switch `DJANGO_EMAIL_BACKEND` to SMTP in `.env` (SendGrid block), set `SITE_URL`, restart the containers, send a test email, then send staff activation links.
- SMS: set `SMS_BACKEND` and the gateway keys.

## Never in git or on the server's repository folder

`.env`, database dumps (`documentations/Database/`), the GoTHOMIS manual and the proposal. They are in `.gitignore`. Copy a dump to the server with `scp` only when it is needed, and delete it after the import.
