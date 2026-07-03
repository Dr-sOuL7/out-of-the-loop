# ---------------------------------------------------------------------------
# Out of the Loop -- Telegram bot (persistent long-polling worker).
# Runs as an outbound-only process: no inbound HTTP port is exposed.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Make both `python run.py` and `python -m ootl` importable.
    PYTHONPATH=/app/src \
    # Store the SQLite DB on a mountable volume so stats survive restarts.
    DATABASE_PATH=/data/ootl.db

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application.
COPY src ./src
COPY run.py pyproject.toml README.md ./

# Run as a non-root user and give it ownership of the data volume.
RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /data \
    && chown -R botuser:botuser /data /app
USER botuser

VOLUME ["/data"]

# Long-polling worker (no port to expose).
CMD ["python", "run.py"]
