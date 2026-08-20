FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build deps for asyncpg / numpy wheels are already prebuilt, so this stays slim.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# Copy the static frontend so the FastAPI static mount can serve it.
COPY ../frontend ./frontend
RUN mkdir -p storage/recordings

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD curl -fs http://localhost:8000/api/health || exit 1

# start.sh: rewrite Render's postgresql:// → postgresql+asyncpg://, seed once,
# then hand off to uvicorn.
RUN printf '#!/bin/sh\n\
set -e\n\
# Render injects DATABASE_URL as postgresql://... — asyncpg needs +asyncpg scheme.\n\
if [ -n "$DATABASE_URL" ]; then\n\
  export DATABASE_URL=$(echo "$DATABASE_URL" | sed "s|^postgresql://|postgresql+asyncpg://|")\n\
fi\n\
# Seed demo data on first boot (no-op if users already exist).\n\
python -m app.seed || true\n\
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"\n\
' > /app/start.sh && chmod +x /app/start.sh

# One worker: the WebSocket hub keeps room state in memory. To scale out,
# put the hub behind Redis pub/sub and raise the worker count.
CMD ["/app/start.sh"]
