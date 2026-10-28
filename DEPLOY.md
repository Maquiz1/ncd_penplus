# Deploying NCD - PENPLUS CLINIC (Hostinger, Docker + PostgreSQL)

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

## Backups

```bash
cd /opt/ncd-penplus && ./backup-penplus.sh            # database, uploaded files and code version
```
Backups go to `~/backups/penplus` and are kept for 30 days (`KEEP_DAYS`). `deploy-penplus.sh` and the import script make one automatically before changing anything. For a daily backup, add with `crontab -e`:
```
30 1 * * * cd /opt/ncd-penplus && ./backup-penplus.sh daily >> ~/backups/penplus/backup.log 2>&1
```
Copy backups off the server regularly.

## Restoring a backup

```bash
cd /opt/ncd-penplus
./restore-penplus.sh                 # lists the backups, newest first; pick a number (Enter = latest)
./restore-penplus.sh latest          # or straight to the latest
./restore-penplus.sh 2026-10-28      # or the latest of a given day
./restore-penplus.sh latest --with-media   # also put back uploaded files (reports, attachments)
```
It asks for confirmation, backs up the current state first (`before_restore`), stops the app, restores the database in one transaction (on an error nothing changes), runs migrations for the current code and starts the app again. It prints the command to undo the restore. "Latest" skips those `before_restore` safety backups, so running it twice does not undo the first restore.

## Importing EDCS data (until go-live)

While staff still work in the old EDCS, import a fresh dump whenever you need up-to-date reports and indicators:

1. **On your computer**, copy the dump to the server:
   ```bash
   ./copy-edcs-to-server.sh                                  # documentations/Database/edc.sql.gz
   ./copy-edcs-to-server.sh ~/Downloads/edc_new.sql.gz       # or another dump
   ```
   It goes to `/root/edc.sql.gz` (root only). If a dump is already there, the script asks before replacing it.

2. **On the server** (inside `tmux`, it takes about an hour):
   ```bash
   cd /opt/ncd-penplus && ./import-edcs-penplus.sh
   ```
   It **replaces** the EDCS patient data with the new dump, so edits and deletions made in the old system are included. Staff accounts, roles, facilities, catalogues and settings are kept, and patients get the same numbers again. It backs up first and restores that backup by itself if the import fails. The temporary MySQL is always removed; the dump is deleted after a successful import.

   The replace step **refuses** when anything was entered directly in this system since the last import, so real data can't be wiped by mistake. Test entries made while trying the system out block it too: check what it lists, and if it really is only testing, run
   `docker compose exec penplus-web python manage.py reset_imported_data --force` once, then the script again.

   `--add-only` keeps the existing data and only adds records not imported before (with a dry run first).

**After go-live** staff enter data directly here: stop importing. Updates are just `git pull && ./deploy-penplus.sh`.

## Go-live

- Email: switch `DJANGO_EMAIL_BACKEND` to SMTP in `.env` (SendGrid block), set `SITE_URL`, recreate the containers, send a test email, then send staff activation links.
- SMS: set `SMS_BACKEND` and the gateway keys.

## Never in git or on the server's repository folder

`.env`, database dumps (`documentations/Database/`), the GoTHOMIS manual and the proposal. They are in `.gitignore`. Copy a dump to the server with `scp` only when it is needed, and delete it after the import.
