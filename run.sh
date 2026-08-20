#!/usr/bin/env bash
# One-command local start: Postgres must already be running (or use docker compose).
set -euo pipefail
cd "$(dirname "$0")/backend"

[ -f .env ] || cp .env.example .env

python3 -m pip install -q -r requirements.txt
python3 -m app.seed                     # no-op if the database already has users
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
