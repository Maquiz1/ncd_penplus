#!/bin/bash
#
# Deploy NCD - PENPLUS CLINIC on the Hostinger server (/opt/ncd-penplus), same steps as tamris deploy_live.sh:
#   1. back up the database and uploaded files
#   2. build the new image while the old site keeps serving
#   3. migrate the database
#   4. switch the containers to the new code
#
# Usage:  git pull && ./deploy-penplus.sh
# First time: ./deploy-penplus.sh --first   (also loads locations and PEN-Plus setup, then asks for an admin account)
# Backups go to $BACKUP_DIR (default ~/backups/penplus). Copy them off the server regularly.

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "Missing .env. Run: cp .env.production.example .env  and fill in the values." >&2
    exit 1
fi

# 1. Backups -------------------------------------------------------------------
if docker compose ps --status running --services 2>/dev/null | grep -qx penplus-db; then
    DB_BACKUP="$(./backup-penplus.sh before_deploy | tail -n1)"
else
    echo "Database container is not running yet (first deployment?): skipping backup."
    DB_BACKUP=""
fi

# 2. Build ---------------------------------------------------------------------
echo "Building the new image (the current site keeps running)..."
docker compose build penplus-web

# 3. Migrate -------------------------------------------------------------------
echo "Starting the database if needed..."
docker compose up -d penplus-db penplus-redis
for i in $(seq 1 30); do
    if docker compose exec -T penplus-db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then break; fi
    sleep 2
done

echo "Migrations that will run:"
docker compose run --rm penplus-web python manage.py migrate --plan
echo "Running database migrations..."
docker compose run --rm penplus-web python manage.py migrate --noinput

# 4. Switch --------------------------------------------------------------------
echo "Switching to the new code..."
docker compose up -d

echo "Updating roles and scheduled jobs (existing values are kept)..."
docker compose exec -T penplus-web python manage.py setup_roles
docker compose exec -T penplus-web python manage.py continue_edcs_forms
docker compose exec -T penplus-web python manage.py setup_periodic_tasks

if [ "${1:-}" = "--first" ]; then
    echo "First deployment: loading Tanzania locations and the PEN-Plus setup..."
    docker compose exec -T penplus-web python manage.py import_tz_locations
    docker compose exec -T penplus-web python manage.py setup_penplus --deactivate-demo
    echo "Create the first administrator account:"
    docker compose exec penplus-web python manage.py createsuperuser
fi

echo
docker compose ps
echo
echo "Deployment completed. Check from the server:  curl -sI http://127.0.0.1:\${WEB_PORT:-8090}/accounts/login/"
if [ -n "$DB_BACKUP" ]; then
    echo "Backup from before this deployment: $DB_BACKUP"
    echo "To roll back the code:    git checkout \$(cat ${DB_BACKUP%_db.dump}_commit.txt) && docker compose build penplus-web && docker compose up -d"
    echo "To restore the database:  docker compose exec -T penplus-db sh -c 'pg_restore --clean --if-exists -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"' < $DB_BACKUP"
fi
