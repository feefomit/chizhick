# Образ Playwright уже содержит нужные системные зависимости для браузера
FROM mcr.microsoft.com/playwright/python:v1.50.0-nobley

WORKDIR /app

# ВАЖНО: кеши в доступные директории (на хостингах контейнер часто стартует не под root)
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CAMOUFOX_CACHE_DIR=/opt/camoufox-cache \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browser-cache \
    HOME=/tmp

RUN mkdir -p /opt/camoufox-cache /opt/browser-cache /tmp \
 && chmod -R 777 /opt/camoufox-cache /opt/browser-cache /tmp

# Python-зависимости
COPY requirements.txt /app/requirements.txt
RUN pip install -U pip \
 && pip install -r /app/requirements.txt

# Скачиваем camoufox в образ, чтобы на старте ничего не качалось
RUN python -m camoufox fetch

# Код приложения 
COPY app.py /app/app.py

# Timeweb ориентируется на EXPOSE
EXPOSE 8080

# Запуск (Timeweb обычно прокидывает PORT, но 8080 по умолчанию)
CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
