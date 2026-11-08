#!/bin/bash
#
# Copy the issued data dictionary versions to the server, where the Codebook page offers them for download.
# They are kept out of git. Run on your computer (not on the server).
#
# Usage:  ./copy-dictionary-to-server.sh [user@server]
# Default: root@31.97.187.66

set -euo pipefail
cd "$(dirname "$0")"

SERVER="${1:-root@31.97.187.66}"
REMOTE_DIR=/opt/ncd-penplus/data_dictionary

shopt -s nullglob
FILES=(data_dictionary/NCD_PENPlus_Data_Dictionary_v*.xlsx)
if [ ${#FILES[@]} -eq 0 ]; then
    echo "No dictionary in data_dictionary/. Run: venv/bin/python manage.py build_dictionary" >&2
    exit 1
fi

echo "Copying ${#FILES[@]} file(s) to $SERVER:$REMOTE_DIR ..."
ssh "$SERVER" "mkdir -p $REMOTE_DIR && chmod 755 $REMOTE_DIR"
for FILE in "${FILES[@]}"; do
    NAME=$(basename "$FILE")
    scp -q "$FILE" "$SERVER:$REMOTE_DIR/$NAME.part"
    # World-readable because the container runs as an unprivileged user. It holds variables and codes, no subject data.
    ssh "$SERVER" "chmod 644 $REMOTE_DIR/$NAME.part && mv $REMOTE_DIR/$NAME.part $REMOTE_DIR/$NAME"
    echo "  $NAME"
done
echo "Done. Earlier versions now show on the Codebook page."
