# Built from the repo root (see docker-compose.yml) so schemas/ can be copied in.
FROM python:3.12-slim

WORKDIR /app
COPY ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY schemas/ /app/schemas/
COPY ingestion/aquarius_ingest/ /app/aquarius_ingest/

ENV SCHEMA_DIR=/app/schemas PYTHONUNBUFFERED=1
CMD ["python", "-m", "aquarius_ingest.main"]
