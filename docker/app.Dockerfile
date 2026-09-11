# Read-Only reporting/export image, Python only.  Talks to Postgres as
# hail_app, which has SELECT on the reference and weather tables but none
# of hail_ingest's write path.  Same shape as ingest.Dockerfile, same type
# of container, but a different role.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts/

RUN useradd --create-home --uid 1000 app
USER app
