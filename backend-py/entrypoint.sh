#!/usr/bin/env sh
set -e

if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "Running migrations..."
    alembic upgrade head
    echo "Migrations done."

    echo "Running seed..."
    python -m app.infrastructure.db.seed
    echo "Seed done."
fi

echo "Starting application..."
exec "$@"
