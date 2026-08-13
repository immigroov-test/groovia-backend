FROM python:3.13-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y build-essential \
 && apt-get autoremove -y

COPY --chown=app:app \
     main.py config.py content.py schema.py \
     ./
COPY --chown=app:app core ./core
COPY --chown=app:app db ./db
COPY --chown=app:app services ./services
COPY --chown=app:app ai ./ai
COPY --chown=app:app routers ./routers

USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

CMD ["sh", "-c", "uvicorn main:api --host 0.0.0.0 --port ${PORT:-8000}"]
