#!/bin/bash
#
# Back up NCD - PENPLUS CLINIC on the server: database, uploaded files and the code version.
# Run in /opt/ncd-penplus. Also used by deploy-penplus.sh and import-edcs-penplus.sh before they change anything.
#
# Usage:  ./backup-penplus.sh [label]        e.g. ./backup-penplus.sh before_import
# Files:  $BACKUP_DIR/<date>_<label>_db.dump, _media.tar.gz, _commit.txt   (default BACKUP_DIR ~/backups/penplus)
# Old backups are deleted after KEEP_DAYS (default 30). Copy backups off the server regularly.
#
# Daily at 01:30 (crontab -e):
#   30 1 * * * cd /opt/ncd-penplus && ./backup-penplus.sh daily >> ~/backups/penplus/backup.log 2>&1
#
# Restore a database backup:
#   docker compose exec -T penplus-db sh -c 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < FILE_db.dump

set -euo pipefail
cd "$(dirname "$0")"

LABEL="${1:-manual}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/penplus}"
KEEP_DAYS="${KEEP_DAYS:-30}"
PREFIX="$BACKUP_DIR/$(date +%F_%H%M)_${LABEL}"

mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx penplus-db; then
    echo "The database container is not running: nothing to back up." >&2
    exit 1
fi

echo "Backing up the database to ${PREFIX}_db.dump ..." >&2
docker compose exec -T penplus-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "${PREFIX}_db.dump"
if [ ! -s "${PREFIX}_db.dump" ]; then
    echo "Database backup is empty." >&2
    rm -f "${PREFIX}_db.dump"
    exit 1
fi

echo "Backing up uploaded files to ${PREFIX}_media.tar.gz ..." >&2
docker compose run --rm --no-deps -T --entrypoint tar penplus-web czf - -C /app/private_media . > "${PREFIX}_media.tar.gz"
git rev-parse HEAD > "${PREFIX}_commit.txt" 2>/dev/null || true
chmod 600 "${PREFIX}"_*

find "$BACKUP_DIR" -maxdepth 1 -type f -name '20*_*' -mtime +"$KEEP_DAYS" -delete

echo "Backup done: ${PREFIX}_db.dump ($(du -h "${PREFIX}_db.dump" | cut -f1))" >&2
# Progress goes to stderr; stdout is only the database file, so other scripts can read it.
echo "${PREFIX}_db.dump"
