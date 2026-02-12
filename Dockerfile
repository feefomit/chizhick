FROM mcr.microsoft.com/playwright/python:stable

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/opt/home \
    XDG_CACHE_HOME=/opt/xdg-cache \
    CAMOUFOX_CACHE_DIR=/opt/camoufox-cache \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN mkdir -p /opt/home /opt/xdg-cache /opt/camoufox-cache \
 && chmod -R 777 /opt/home /opt/xdg-cache /opt/camoufox-cache

COPY requirements.txt /app/requirements.txt
RUN pip install -U pip \
 && pip install -r /app/requirements.txt

# ВАЖНО: ставим Camoufox в /opt/camoufox-cache (а не в /tmp)
RUN python -m camoufox fetch || (sleep 3 && python -m camoufox fetch)

COPY app.py /app/app.py

EXPOSE 8080
CMD ["bash","-lc","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
