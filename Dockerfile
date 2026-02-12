# syntax=docker/dockerfile:1.6
FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CAMOUFOX_CACHE_DIR=/opt/camoufox-cache \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browser-cache \
    HOME=/tmp

RUN mkdir -p /opt/camoufox-cache /opt/browser-cache /tmp \
 && chmod -R 777 /opt/camoufox-cache /opt/browser-cache /tmp

COPY requirements.txt /app/requirements.txt

# кеш pip (если у Timeweb включён BuildKit — ускоряет пересборки)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U pip && pip install -r /app/requirements.txt

COPY app.py /app/app.py

EXPOSE 8080

CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
