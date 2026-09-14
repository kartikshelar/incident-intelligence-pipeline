FROM python:3.12-slim

WORKDIR /srv

# System deps for psycopg (binary wheel is used, but libpq keeps things safe
# across platforms) and for building nothing else in M1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

# Overridden by docker-compose per-service `command:`.
CMD ["python", "-m", "app.worker.main"]
