#!/bin/sh
# Wait for PostgreSQL, then run the container's command (gunicorn, celery worker or celery beat).
# Migrations are run by deploy-penplus.sh, once, before the containers switch to new code.
set -e

python - <<'EOF'
import os, socket, sys, time
host, port = os.environ.get('DB_HOST', 'penplus-db'), int(os.environ.get('DB_PORT', '5432'))
for _ in range(60):
    try:
        socket.create_connection((host, port), timeout=2).close()
        sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit(f'Database {host}:{port} is not reachable.')
EOF

exec "$@"
