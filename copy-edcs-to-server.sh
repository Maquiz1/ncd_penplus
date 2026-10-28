#!/bin/bash
#
# Copy an EDCS database dump to the server, ready for import-edcs-penplus.sh.
# Run on your computer (not on the server).
#
# Usage:  ./copy-edcs-to-server.sh [dump.sql.gz] [user@server]
# Defaults: documentations/Database/edc.sql.gz  root@31.97.187.66
#
# The dump is copied to /root/edc.sql.gz (outside the project folder, readable by root only).
# import-edcs-penplus.sh deletes it after a successful import.

set -euo pipefail
cd "$(dirname "$0")"

DUMP="${1:-documentations/Database/edc.sql.gz}"
SERVER="${2:-root@31.97.187.66}"
REMOTE=/root/edc.sql.gz

if [ ! -s "$DUMP" ]; then
    echo "Dump not found: $DUMP" >&2
    exit 1
fi
if ! gzip -t "$DUMP" 2>/dev/null; then
    echo "$DUMP is not a valid .gz file." >&2
    exit 1
fi
if ! zcat "$DUMP" | head -40 | grep -q 'CREATE DATABASE.*`edc`'; then
    echo "Warning: $DUMP does not create the 'edc' database; the import expects it." >&2
fi

EXISTING="$(ssh "$SERVER" "[ -f $REMOTE ] && ls -l --time-style='+%d %b %Y %H:%M' $REMOTE || true")"
if [ -n "$EXISTING" ]; then
    echo "The server already has a dump that was not imported yet:"
    echo "  $EXISTING"
    read -r -p "Replace it with $DUMP? [y/N] " answer
    if [ "${answer,,}" != y ] && [ "${answer,,}" != yes ]; then
        echo "Kept the existing dump. To import it, run on the server: cd /opt/ncd-penplus && ./import-edcs-penplus.sh"
        exit 0
    fi
fi

echo "Copying $DUMP ($(du -h "$DUMP" | cut -f1)) to $SERVER:$REMOTE ..."
scp "$DUMP" "$SERVER:$REMOTE.part"
ssh "$SERVER" "chmod 600 $REMOTE.part && mv $REMOTE.part $REMOTE && ls -l $REMOTE"

echo
echo "Done. On the server run:  cd /opt/ncd-penplus && ./import-edcs-penplus.sh"
