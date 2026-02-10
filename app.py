import os
from fastapi import FastAPI, Query, Header, HTTPException

app = FastAPI(title="Chizhik API wrapper")

PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"
API_KEY = os.getenv("API_KEY")

def check_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os

from chizhik_api import ChizhikAPI

app = FastAPI(title="Chizhik site + API")

BASE_DIR = Path(__file__).resolve().parent
SITE_DIR = BASE_DIR / "site"

API_KEY = os.getenv("API_KEY")
PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

# статика
app.mount("/static", StaticFiles(directory=str(SITE_DIR)), name="static")

# главная страница сайта
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(str(SITE_DIR / "index.html"))

# healthcheck должен оставаться JSON
@app.get("/health")
async def health():
    return {"ok": True}

# защита ключом — НЕ блокируем /, /health, /static и /public
@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path == "/" or path == "/health" or path.startswith("/static") or path.startswith("/public") \
       or path in {"/docs", "/openapi.json", "/redoc"}:
        return await call_next(request)

    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return await call_next(request)

# public API для сайта
@app.get("/public/geo/cities")
async def public_cities(search: str = Query(...), page: int = 1):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()


@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/offers/active")
async def active_offers(x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    from chizhik_api import ChizhikAPI  # ленивый импорт (ускоряет старт)
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()

@app.get("/geo/cities")
async def cities(search: str = Query(...), page: int = 1, x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Статика: /static/app.js, /static/styles.css и т.п.
app.mount("/static", StaticFiles(directory="site"), name="static")

# Главная страница сайта
@app.get("/", include_in_schema=False)
async def site_index():
    return FileResponse("site/index.html")
