# Ingest image, Python only
# -- this image talks to Postgres over the hailnet network and has no use for server binaries
# -- does not derive from postgis/postgis


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts/

RUN useradd --create-home --uid 1000 ingest
USER ingest



