FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

# Run as an unprivileged user; the SQLite data dir must be writable by it.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Hosts like Render inject a PORT env var and expect the app to bind to it.
# PORT provides the fallback for docker compose and local runs.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health/live', timeout=4).status == 200 else 1)"

# Apply schema migrations, then serve. AUTO_CREATE_SCHEMA is false in the
# container so Alembic (not create_all) is the single source of schema truth.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app \
      --host 0.0.0.0 --port \"${PORT:-8000}\" --proxy-headers --forwarded-allow-ips '*'" ]
