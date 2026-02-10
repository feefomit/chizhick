import os
from typing import Optional

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from chizhik_api import ChizhikAPI

# =========================
# Настройки через env
# =========================
API_KEY = os.getenv("API_KEY")  # закрывает "не public" эндпоинты
PROXY = os.getenv("CHIZHIK_PROXY")  # опционально: user:pass@host:port или http://...
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

app = FastAPI(title="Cenopad Backend API", version="1.0.0")

# =========================
# CORS (для отдельного фронтенда на другом домене)
# =========================
# Чтобы не пересобирать бэкенд каждый раз при смене домена фронта — оставляем "*".
# Тут безопасно, потому что мы открываем только /public/* (они и так публичные),
# а приватные эндпоинты всё равно под API_KEY.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# API-key защита (не трогаем /public, /health и swagger)
# =========================
@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    path = request.url.path

    # Публичные/служебные — без ключа
    if (
        path in {"/", "/health", "/docs", "/openapi.json", "/redoc"}
        or path.startswith("/public")
    ):
        return await call_next(request)

    # Всё остальное — под ключом (если ключ задан)
    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return await call_next(request)

# =========================
# Служебные
# =========================
@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "cenopad-backend"}

@app.get("/health", include_in_schema=False)
async def health():
    return {"ok": True}

# =========================
# PUBLIC API (для сайта, без ключа)
# =========================
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
async def public_catalog_tree(city_id: Optional[str] = None):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.tree(city_id=city_id)
        return r.json()

@app.get("/public/catalog/products")
async def public_catalog_products(
    page: int = 1,
    category_id: Optional[int] = None,
    city_id: Optional[str] = None,
    search: Optional[str] = None,
):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.products_list(
            page=page,
            category_id=category_id,
            city_id=city_id,
            search=search,
        )
        return r.json()

@app.get("/public/product/info")
async def public_product_info(product_id: int, city_id: Optional[str] = None):
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
        return r.json()

# =========================
# (опционально) PRIVATE API — если тебе нужны закрытые ручки "для себя"
# Эти эндпоинты будут требовать X-API-Key (см. middleware выше).
# =========================
@app.get("/private/offers/active")
async def private_offers_active():
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()
