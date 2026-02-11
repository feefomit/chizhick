# Playwright Python образ (вариант Ubuntu 24.04 / Python 3.12)
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

# зависимости (отдельным слоем — быстрее пересборки)
COPY requirements.txt /app/requirements.txt
RUN pip install -U pip \
 && pip install -r /app/requirements.txt

# Скачиваем camoufox в образ (с повторами)
RUN bash -lc 'set -euo pipefail; \
  echo "Camoufox fetch..."; \
  for i in 1 2 3 4 5; do \
    echo "Attempt $i"; \
    python -m camoufox fetch && break || true; \
    sleep $((i*20)); \
  done; \
  python -m camoufox path >/dev/null'


# код
COPY app.py /app/app.py

EXPOSE 8080
CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
