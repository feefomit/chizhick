import os
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

# =========================
# ENV настройки
# =========================
API_KEY = os.getenv("API_KEY")  # защищает все НЕ /public/*
PROXY = os.getenv("CHIZHIK_PROXY")  # опционально
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

app = FastAPI(title="Cenopad Backend", version="1.0.0")

# =========================
# CORS (для фронта на другом домене, напр. chizhick.ru)
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # можно заменить на ["https://chizhick.ru"] позже
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# API-key guard
# ВАЖНО: в middleware НЕ делаем raise HTTPException — возвращаем Response
# =========================
PUBLIC_PATHS = {
    "/", "/health", "/health/",
    "/docs", "/openapi.json", "/redoc",
}

@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    path = request.url.path

    # Всё публичное — без ключа
    if path in PUBLIC_PATHS or path.startswith("/public"):
        return await call_next(request)

    # Всё остальное — под ключом (если ключ задан)
    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    return await call_next(request)

# =========================
# Служебные
# =========================
@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "cenopad-backend"}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    return {"ok": True}

# =========================
# PUBLIC API (для сайта, без ключа)
# Ленивый импорт ChizhikAPI — чтобы /health отвечал мгновенно
# =========================
@app.get("/public/offers/active")
async def public_offers_active():
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()

@app.get("/public/geo/cities")
async def public_cities(search: str = Query(...), page: int = 1):
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

@app.get("/public/catalog/tree")
async def public_catalog_tree(city_id: Optional[str] = None):
    from chizhik_api import ChizhikAPI
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
    from chizhik_api import ChizhikAPI
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
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
        return r.json()

# =========================
# PRIVATE (по желанию) — требует X-API-Key
# =========================
@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}
