import os
from typing import Optional

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# =========================
# ENV настройки
# =========================
API_KEY = os.getenv("API_KEY")  # защищает всё, что НЕ /public/*
PROXY = os.getenv("CHIZHIK_PROXY")  # опционально (http://user:pass@host:port)
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

app = FastAPI(title="Cenopad Backend", version="1.0.0")

# =========================
# CORS для отдельного фронтенда (chizhick.ru)
# Оставляем "*" чтобы не пересобирать бэкенд при смене домена фронта.
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# API-key защита
# Всё, что не /public/* и не служебное — требует X-API-Key
# =========================
@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    path = request.url.path

    # Служебные и public — без ключа
    # ВАЖНО: /health и /health/ (без редиректов)
    if (
        path in {"/", "/health", "/health/", "/docs", "/openapi.json", "/redoc"}
        or path.startswith("/public")
    ):
        return await call_next(request)

    # Остальное — под ключом (если ключ задан)
    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return await call_next(request)

# =========================
# Служебные (без ключа)
# =========================
@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "cenopad-backend"}

# Два маршрута, чтобы healthcheck не ловил 307 redirect из-за слэша
@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    return {"ok": True}

# =========================
# PUBLIC API (для сайта, без ключа)
# =========================

@app.get("/public/offers/active")
async def public_offers_active():
    # ленивый импорт — чтобы /health отвечал моментально
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
# PRIVATE (опционально) — для админки/скриптов (требует X-API-Key)
# =========================
@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}
