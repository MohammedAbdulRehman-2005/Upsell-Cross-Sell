#!/usr/bin/env sh
set -e

echo "=========================================================="
echo " Starting Plural WhatsApp Upsell & Cross-Sell Service"
echo " Environment: ${ENVIRONMENT:-development}"
echo " Port:        ${PORT:-8000}"
echo "=========================================================="

echo "==> Step 1: Running Alembic database migrations..."
alembic upgrade head
echo "==> Alembic migrations completed successfully."

echo "==> Step 2: Ensuring product catalogue and recommendation mappings are seeded..."
python -m app.scripts.seed_catalogue --apply-synthetic-fixture || echo "==> Catalogue seed step completed or skipped."

TARGET_PORT="${PORT:-8000}"
echo "==> Step 3: Starting Uvicorn server on 0.0.0.0:${TARGET_PORT}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${TARGET_PORT}"
