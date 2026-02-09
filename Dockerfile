# Образ Playwright уже содержит системные зависимости для браузера
FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

# ВАЖНО: кеши в доступное место (не /root), чтобы работало даже под случайным UID
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browser-cache \
    CAMOUFOX_CACHE_DIR=/opt/camoufox-cache \
    HOME=/tmp

RUN mkdir -p /opt/browser-cache /opt/camoufox-cache /tmp \
 && chmod -R 777 /opt/browser-cache /opt/camoufox-cache /tmp

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# Скачиваем Camoufox в образ (иначе он попытается качать при первом запуске)
RUN python -m camoufox fetch

COPY app.py /app/app.py

EXPOSE 8080
CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
