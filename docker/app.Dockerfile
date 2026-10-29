# Read-Only reporting/export image, Python only.  Talks to Postgres as
# hail_app, which has SELECT on the reference and weather tables but none
# of hail_ingest's write path.  Same shape as ingest.Dockerfile, same type
# of container, but a different role.

FROM python:3.12-slim AS app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hailsys ./hailsys/
COPY scripts ./scripts/

RUN useradd --create-home --uid 1000 app
USER app

# Test-only stage:  the app image plus Node, so the test/js/ checks run with the 
# unittest suite.  web and app build target "app", so Node never
# reaches the running server
FROM app AS test
USER root
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*
USER app
