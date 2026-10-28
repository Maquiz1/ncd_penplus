#!/bin/bash
#
# Import the old PEN-Plus EDCS data into NCD - PENPLUS STUDY on the server.
# Run on the server in /opt/ncd-penplus, after copy-edcs-to-server.sh has put the dump in /root/edc.sql.gz.
#
#   1. back up the PEN-Plus database
#   2. load the dump into a temporary MySQL container
#   3. dry run (nothing saved), then ask before the real import
#   4. import, refresh locations and data-quality checks
#   5. always remove the MySQL container; delete the dump after a successful import
#
# Usage:  ./import-edcs-penplus.sh [dump.sql.gz] [--yes]
#   --yes   skip the question after the dry run (for unattended runs)
# Safe to re-run with a newer dump: records already imported are recognised and not duplicated.
# Takes about 1-1.5 hours (dry run + import); run it inside tmux or screen so a dropped SSH connection does not stop it.

set -euo pipefail
cd "$(dirname "$0")"

DUMP=/root/edc.sql.gz
ASSUME_YES=no
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=yes ;;
        *) DUMP="$arg" ;;
    esac
done

MYSQL_CONTAINER=penplus-edcs
MYSQL_IMAGE=mysql:8.4
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/penplus}"
STAMP="$(date +%F_%H%M)"

if [ ! -s "$DUMP" ]; then
    echo "No EDCS dump at $DUMP. Copy it first: run ./copy-edcs-to-server.sh on your computer." >&2
    exit 1
fi
if ! gzip -t "$DUMP" 2>/dev/null; then
    echo "$DUMP is not a complete .gz file (copy interrupted?). Copy it again." >&2
    exit 1
fi
echo "Found $DUMP: $(du -h "$DUMP" | cut -f1), copied $(date -r "$DUMP" '+%d %b %Y %H:%M')."
if ! docker compose ps --status running --services | grep -qx penplus-web; then
    echo "PEN-Plus is not running. Start it first: ./deploy-penplus.sh" >&2
    exit 1
fi

cleanup() {
    docker rm -fv "$MYSQL_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The temporary MySQL joins the PEN-Plus network so the web container can reach it by name.
WEB_ID="$(docker compose ps -q penplus-web)"
NETWORK="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$WEB_ID" | awk '{print $1}')"
EDCS_PW="$(openssl rand -hex 16)"
EDCS_ENV=(-e EDCS_DB_HOST="$MYSQL_CONTAINER" -e EDCS_DB_PORT=3306 -e EDCS_DB_USER=root
          -e EDCS_DB_PASSWORD="$EDCS_PW" -e EDCS_DB_NAME=edc)

web() {
    docker compose exec -T "${EDCS_ENV[@]}" penplus-web "$@"
}

# 1. Backup ----------------------------------------------------------------------
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
echo "Backing up the PEN-Plus database to $BACKUP_DIR/before_import_$STAMP.dump ..."
docker compose exec -T penplus-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    > "$BACKUP_DIR/before_import_$STAMP.dump"
if [ ! -s "$BACKUP_DIR/before_import_$STAMP.dump" ]; then
    echo "Backup is empty. Stopping before any change is made." >&2
    exit 1
fi

# 2. Temporary MySQL with the dump -------------------------------------------------
echo "Starting a temporary MySQL ($MYSQL_IMAGE) on network $NETWORK ..."
cleanup
docker run -d --name "$MYSQL_CONTAINER" --network "$NETWORK" -e MYSQL_ROOT_PASSWORD="$EDCS_PW" "$MYSQL_IMAGE" >/dev/null
for i in $(seq 1 60); do
    # Answers on TCP only once the first-start initialisation is finished.
    if docker exec -e MYSQL_PWD="$EDCS_PW" "$MYSQL_CONTAINER" mysql -uroot -h127.0.0.1 -e 'SELECT 1' >/dev/null 2>&1; then
        break
    fi
    if [ "$i" = 60 ]; then
        echo "MySQL did not start. Logs:" >&2
        docker logs --tail 20 "$MYSQL_CONTAINER" >&2
        exit 1
    fi
    sleep 3
done

echo "Loading $DUMP into MySQL (a few minutes) ..."
gunzip -c "$DUMP" | docker exec -i -e MYSQL_PWD="$EDCS_PW" "$MYSQL_CONTAINER" mysql -uroot
VISITS="$(docker exec -e MYSQL_PWD="$EDCS_PW" "$MYSQL_CONTAINER" mysql -uroot -N -e 'SELECT COUNT(*) FROM edc.visit')"
echo "EDCS loaded: $VISITS visits."

# MySQL 8 logins need the cryptography package (in the image from 9853466 on; installed here for older images).
if ! web python -c 'import cryptography' 2>/dev/null; then
    echo "Installing cryptography in the web container ..."
    docker compose exec -T -u root penplus-web pip install -q cryptography==44.0.2
fi

# 3. Dry run ---------------------------------------------------------------------
echo
echo "Dry run (imports inside a transaction, then rolls back; nothing is saved) ..."
web python manage.py import_penplus_edcs --dry-run 2>&1 | tail -40
echo
if [ "$ASSUME_YES" != yes ]; then
    read -r -p "Run the real import now? [y/N] " answer
    if [ "${answer,,}" != y ] && [ "${answer,,}" != yes ]; then
        echo "Stopped. Nothing was imported; the dump is kept at $DUMP."
        exit 0
    fi
fi

# 4. Import ----------------------------------------------------------------------
echo "Importing (about an hour; the dry run takes about as long) ..."
web python manage.py import_penplus_edcs 2>&1 | tail -40
echo "Refreshing locations and data-quality checks ..."
web python manage.py import_tz_locations 2>&1 | tail -3
web python manage.py detect_data_issues 2>&1 | tail -15

# 5. Clean up --------------------------------------------------------------------
cleanup
rm -f "$DUMP"
echo
echo "Import finished. The temporary MySQL and $DUMP are deleted."
echo "Backup from before the import: $BACKUP_DIR/before_import_$STAMP.dump"
echo "To undo the import:  docker compose exec -T penplus-db sh -c 'pg_restore --clean --if-exists -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"' < $BACKUP_DIR/before_import_$STAMP.dump"
