FROM mcr.microsoft.com/playwright/python:stable

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    CAMOUFOX_CACHE_DIR=/tmp/.cache/camoufox \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN mkdir -p /tmp/.cache/camoufox \
 && chmod -R 777 /tmp /tmp/.cache

COPY requirements.txt /app/requirements.txt
RUN pip install -U pip \
 && pip install -r /app/requirements.txt

# Ставим camoufox в тот же путь, который он ищет на рантайме
RUN python -m camoufox fetch || (sleep 3 && python -m camoufox fetch)

COPY app.py /app/app.py

EXPOSE 8080
CMD ["bash","-lc","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
