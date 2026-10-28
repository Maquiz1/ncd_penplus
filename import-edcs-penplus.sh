#!/bin/bash
#
# Import the old PEN-Plus EDCS data into NCD - PENPLUS STUDY on the server (until go-live).
# Run on the server in /opt/ncd-penplus, after copy-edcs-to-server.sh has put the dump in /root/edc.sql.gz.
#
# Default (replace): the EDCS patient data here is replaced by the new dump, so edits and deletions made in the
# old system since the last import are included too.
#   1. back up (backup-penplus.sh)
#   2. load the dump into a temporary MySQL container
#   3. remove the patient data imported last time (staff, facilities, catalogues and settings are kept);
#      refused if anything was entered directly in this system since the last import
#   4. import, refresh locations and data-quality checks; if anything fails, the backup is restored
#   5. always remove the MySQL container; delete the dump after a successful import
#
# Usage:  ./import-edcs-penplus.sh [dump.sql.gz] [--yes] [--add-only]
#   --yes        do not ask for confirmation (for unattended runs)
#   --add-only   keep the existing data and only add records not imported before (dry run first)
# Takes about an hour; run it inside tmux or screen so a dropped SSH connection does not stop it.
# After go-live, staff enter data directly here: stop using this script (the reset step refuses anyway).

set -euo pipefail
cd "$(dirname "$0")"

DUMP=/root/edc.sql.gz
ASSUME_YES=no
MODE=replace
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=yes ;;
        --add-only) MODE=add ;;
        -*) echo "Unknown option: $arg" >&2; exit 1 ;;
        *) DUMP="$arg" ;;
    esac
done

MYSQL_CONTAINER=penplus-edcs
MYSQL_IMAGE=mysql:8.4

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

confirm() {
    [ "$ASSUME_YES" = yes ] && return 0
    read -r -p "$1 [y/N] " answer
    [ "${answer,,}" = y ] || [ "${answer,,}" = yes ]
}

# The temporary MySQL joins the PEN-Plus network so the web container can reach it by name.
WEB_ID="$(docker compose ps -q penplus-web)"
NETWORK="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$WEB_ID" | awk '{print $1}')"
EDCS_PW="$(openssl rand -hex 16)"
EDCS_ENV=(-e EDCS_DB_HOST="$MYSQL_CONTAINER" -e EDCS_DB_PORT=3306 -e EDCS_DB_USER=root
          -e EDCS_DB_PASSWORD="$EDCS_PW" -e EDCS_DB_NAME=edc)

web() {
    docker compose exec -T "${EDCS_ENV[@]}" penplus-web "$@"
}

# 1. Check and backup -------------------------------------------------------------
if [ "$MODE" = replace ]; then
    echo "This replaces the EDCS patient data in NCD - PENPLUS STUDY with the new dump:"
    docker compose exec -T penplus-web python manage.py reset_imported_data --check
    confirm "Replace it now?" || { echo "Stopped. Nothing was changed; the dump is kept at $DUMP."; exit 0; }
fi
DB_BACKUP="$(./backup-penplus.sh before_import | tail -n1)"

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

restore_backup() {
    echo >&2
    echo "The import failed. Restoring the database from $DB_BACKUP ..." >&2
    docker compose exec -T penplus-db sh -c 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
        < "$DB_BACKUP" >/dev/null 2>&1 || true
    echo "Restored: the system is as it was before this import. The dump is kept at $DUMP." >&2
}

if [ "$MODE" = add ]; then
    # 3. Dry run, then add only new records ----------------------------------------
    echo
    echo "Dry run (imports inside a transaction, then rolls back; nothing is saved) ..."
    web python manage.py import_penplus_edcs --dry-run 2>&1 | tail -40
    echo
    confirm "Add these records now?" || { echo "Stopped. Nothing was imported; the dump is kept at $DUMP."; exit 0; }
else
    # 3. Remove the patient data from the last import ----------------------------------
    echo "Removing the patient data from the last import ..."
    if ! web python manage.py reset_imported_data; then
        echo "Nothing was changed. The dump is kept at $DUMP." >&2
        exit 1
    fi
fi

# 4. Import ----------------------------------------------------------------------
echo "Importing (about an hour) ..."
if ! { web python manage.py import_penplus_edcs 2>&1 | tail -40; } \
   || ! web python manage.py import_tz_locations 2>&1 | tail -3 \
   || ! web python manage.py detect_data_issues 2>&1 | tail -15; then
    restore_backup
    exit 1
fi

# 5. Clean up --------------------------------------------------------------------
cleanup
rm -f "$DUMP"
echo
echo "Import finished. The temporary MySQL and $DUMP are deleted."
echo "Backup from before the import: $DB_BACKUP"
echo "To undo the import:  docker compose exec -T penplus-db sh -c 'pg_restore --clean --if-exists -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"' < $DB_BACKUP"
