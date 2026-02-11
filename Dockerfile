# Образ Playwright уже содержит системные зависимости для браузеров
# (если у тебя была проблема с typing.override на Python 3.10 — используй -noble)
FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

WORKDIR /app

# ВАЖНО: кэши в доступные директории (на хостингах контейнер часто стартует не под root)
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    CAMOUFOX_CACHE_DIR=/tmp/camoufox-cache \
    PLAYWRIGHT_BROWSERS_PATH=/tmp/browser-cache

RUN mkdir -p /tmp/camoufox-cache /tmp/browser-cache /tmp \
 && chmod -R 777 /tmp/camoufox-cache /tmp/browser-cache /tmp

# Python-зависимости (отдельный слой — при изменении кода не перескачиваются)
COPY requirements.txt /app/requirements.txt
RUN pip install -U pip \
 && pip install -r /app/requirements.txt

# Код приложения
COPY app.py /app/app.py

# (опционально) если отдаёшь статический фронт этим же контейнером:
# COPY static /app/static

# Timeweb ориентируется на EXPOSE
EXPOSE 8080

# Запуск (Timeweb часто задаёт PORT)
CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
