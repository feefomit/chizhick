import os
from pathlib import Path

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chizhik_api import ChizhikAPI

app = FastAPI(title="Cenopad")

BASE_DIR = Path(__file__).resolve().parent
SITE_DIR = BASE_DIR / "site"

API_KEY = os.getenv("API_KEY")
PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

# Статика сайта
app.mount("/static", StaticFiles(directory=str(SITE_DIR)), name="static")

# Главная страница — HTML
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(str(SITE_DIR / "index.html"))

# Healthcheck (всегда без ключа)
@app.get("/health")
async def health():
    return {"ok": True}

# Защита ключом: пропускаем сайт/статику/public
@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if (
        path == "/" or path == "/health"
        or path.startswith("/static")
        or path.startswith("/public")
        or path in {"/docs", "/openapi.json", "/redoc"}
    ):
        return await call_next(request)

    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return await call_next(request)

# ===== Public API для сайта (БЕЗ ключа) =====

@app.get("/public/offers/active")
async def public_offers_active():
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()

@app.get("/public/geo/cities")
async def public_cities(search: str = Query(...), page: int = 1):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

@app.get("/public/catalog/tree")
async def public_catalog_tree(city_id: str | None = None):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.tree(city_id=city_id)
        return r.json()

@app.get("/public/catalog/products")
async def public_catalog_products(
    page: int = 1,
    category_id: int | None = None,
    city_id: str | None = None,
    search: str | None = None,
):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.products_list(
            page=page, category_id=category_id, city_id=city_id, search=search
        )
        return r.json()

@app.get("/public/product/info")
async def public_product_info(product_id: int, city_id: str | None = None):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
        return r.json()
