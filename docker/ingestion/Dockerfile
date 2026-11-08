# aquarius-ingestion — Kafka -> TimescaleDB consumer (service `ingestion`) and migration runner
# (service `db-migrate`, same image). Profile `pipeline` in docker/compose.*.yml.
# Build context = repo root (schemas live in Data-Pipeline/), see Dockerfile.dockerignore.
# Migrations are baked in, so the pull-only VM needs no repo files.
FROM python:3.12-slim

WORKDIR /app
COPY docker/ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY Data-Pipeline/schemas/ /app/schemas/
COPY docker/db/migrations/ /app/migrations/
COPY docker/ingestion/aquarius_ingest/ /app/aquarius_ingest/

ENV SCHEMA_DIR=/app/schemas MIGRATIONS_DIR=/app/migrations PYTHONUNBUFFERED=1
CMD ["python", "-m", "aquarius_ingest.main"]
