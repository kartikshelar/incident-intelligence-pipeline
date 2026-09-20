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
# M6 part 2: scripts/seed_corpus.py (Render's preDeployCommand) needs
# spike/corpus_manifest.json + spike/extraction_run_12.json (the frozen
# report it replays) + spike/raw/ (the 10 locally-available fixtures) —
# not the rest of spike/'s scratch output, so this is scoped rather than
# `COPY spike ./spike`.
COPY scripts/seed_corpus.py ./scripts/seed_corpus.py
COPY spike/corpus_manifest.json spike/extraction_run_12.json ./spike/
COPY spike/raw ./spike/raw

RUN pip install --no-cache-dir .

# Overridden by docker-compose per-service `command:`, and by render.yaml's
# per-service `dockerCommand:`.
CMD ["python", "-m", "app.worker.main"]
