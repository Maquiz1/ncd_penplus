#!/bin/bash
#
# Restore NCD - PENPLUS CLINIC from a backup made by backup-penplus.sh. Run on the server in /opt/ncd-penplus.
#
# Usage:
#   ./restore-penplus.sh                  list the backups (newest first) and choose one; Enter = the latest
#   ./restore-penplus.sh latest           the newest backup (not counting the before_restore safety backups)
#   ./restore-penplus.sh 2026-10-28       the newest backup of that day
#   ./restore-penplus.sh FILE_db.dump     that file
# Options:
#   --with-media   also put back the uploaded files (reports, attachments) from the same backup
#   --yes          do not ask for confirmation
#
# Before restoring, the current state is backed up (label before_restore), so a restore can itself be undone.
# The app is stopped during the restore and started again afterwards; migrations then bring the database up to
# the current code.

set -euo pipefail
cd "$(dirname "$0")"

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/penplus}"
CHOICE=""
WITH_MEDIA=no
ASSUME_YES=no
for arg in "$@"; do
    case "$arg" in
        --with-media) WITH_MEDIA=yes ;;
        --yes) ASSUME_YES=yes ;;
        -*) echo "Unknown option: $arg" >&2; exit 1 ;;
        *) CHOICE="$arg" ;;
    esac
done

mapfile -t BACKUPS < <(ls -1t "$BACKUP_DIR"/*_db.dump 2>/dev/null || true)
if [ "${#BACKUPS[@]}" -eq 0 ]; then
    echo "No backups found in $BACKUP_DIR." >&2
    exit 1
fi
# "Latest" skips this script's own safety backups, so running it twice does not undo the first restore.
LATEST=1
for i in "${!BACKUPS[@]}"; do
    case "${BACKUPS[$i]}" in *_before_restore_db.dump) ;; *) LATEST=$((i + 1)); break ;; esac
done

describe() {  # one line per backup: size and code version
    local file="$1" commit="${1%_db.dump}_commit.txt" media="${1%_db.dump}_media.tar.gz"
    printf '%s  (%s%s%s)' "$(basename "$file")" "$(du -h "$file" | cut -f1)" \
        "$([ -s "$commit" ] && echo ", code $(cut -c1-7 "$commit")")" "$([ -f "$media" ] && echo ", with files")"
}

# Pick the backup ------------------------------------------------------------------
if [ -z "$CHOICE" ]; then
    echo "Backups in $BACKUP_DIR (newest first):"
    for i in "${!BACKUPS[@]}"; do
        printf '  %2d. %s%s\n' "$((i + 1))" "$(describe "${BACKUPS[$i]}")" "$([ "$((i + 1))" = "$LATEST" ] && echo '   <- latest')"
        [ "$i" -ge 19 ] && { echo "  … (only the 20 newest shown)"; break; }
    done
    read -r -p "Restore which one? [Enter = $LATEST] " number
    number="${number:-$LATEST}"
    if ! [[ "$number" =~ ^[0-9]+$ ]] || [ "$number" -lt 1 ] || [ "$number" -gt "${#BACKUPS[@]}" ]; then
        echo "Not a number from the list." >&2
        exit 1
    fi
    DUMP="${BACKUPS[$((number - 1))]}"
elif [ "$CHOICE" = latest ]; then
    DUMP="${BACKUPS[$((LATEST - 1))]}"
elif [ -f "$CHOICE" ]; then
    DUMP="$CHOICE"
else
    DUMP="$(printf '%s\n' "${BACKUPS[@]}" | grep -F "/$CHOICE" | head -n1 || true)"
    if [ -z "$DUMP" ]; then
        echo "No backup matches \"$CHOICE\". Run ./restore-penplus.sh without arguments to see the list." >&2
        exit 1
    fi
fi
MEDIA="${DUMP%_db.dump}_media.tar.gz"
COMMIT_FILE="${DUMP%_db.dump}_commit.txt"

echo
echo "Restore: $(describe "$DUMP")"
if [ -f "$COMMIT_FILE" ] && [ "$(cat "$COMMIT_FILE")" != "$(git rev-parse HEAD 2>/dev/null)" ]; then
    echo "Note: this backup was made with code $(cut -c1-7 "$COMMIT_FILE"); the server now runs $(git rev-parse --short HEAD)."
    echo "      Migrations will bring the database up to the current code after the restore."
fi
[ "$WITH_MEDIA" = yes ] && [ ! -f "$MEDIA" ] && { echo "This backup has no uploaded-files archive." >&2; exit 1; }
if [ "$WITH_MEDIA" = yes ]; then
    echo "The database and the uploaded files will be replaced: everything entered after this backup is lost."
else
    echo "The database will be replaced: everything entered after this backup is lost (uploaded files are kept)."
fi
if [ "$ASSUME_YES" != yes ]; then
    read -r -p "Continue? [y/N] " answer
    [ "${answer,,}" = y ] || [ "${answer,,}" = yes ] || { echo "Stopped. Nothing was changed."; exit 0; }
fi

# Safety backup of the current state ------------------------------------------------
SAFETY="$(./backup-penplus.sh before_restore | tail -n1)"

# Restore ---------------------------------------------------------------------------
echo "Stopping the app during the restore ..."
docker compose stop penplus-web penplus-worker penplus-beat >/dev/null
restart() { docker compose up -d >/dev/null; }
trap restart EXIT

echo "Restoring the database (all or nothing: on an error the database stays as it was) ..."
docker compose exec -T penplus-db sh -c 'pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < "$DUMP"

if [ "$WITH_MEDIA" = yes ]; then
    echo "Restoring uploaded files ..."
    docker compose run --rm --no-deps -T --entrypoint sh penplus-web -c \
        'find /app/private_media -mindepth 1 -delete && tar xzf - -C /app/private_media' < "$MEDIA"
fi

echo "Bringing the database up to the current code ..."
docker compose run --rm penplus-web python manage.py migrate --noinput | tail -n 3

restart
trap - EXIT
echo
echo "Restored from $(basename "$DUMP")."
echo "To undo this restore:  ./restore-penplus.sh $SAFETY"
