import os
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

API_KEY = os.getenv("API_KEY")
PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

app = FastAPI(title="Chizhik Catalog Backend", version="1.0.0")

PUBLIC_PATHS = {
    "/", "/health", "/health/",
    "/docs", "/openapi.json", "/redoc",
    "/favicon.ico",
}

@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    # ВАЖНО: разрешаем preflight
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    if path in PUBLIC_PATHS or path.startswith("/public"):
        return await call_next(request)

    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    return await call_next(request)

# CORS ДОЛЖЕН БЫТЬ СНАРУЖИ guard, поэтому добавляем его ПОСЛЕ middleware выше
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://chizhick.ru",
        "https://www.chizhick.ru",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    return {"ok": True}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return JSONResponse({}, status_code=204)

# ===== Public API =====

@app.get("/public/geo/cities")
async def public_cities(search: str = Query(...), page: int = 1):
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

@app.get("/public/offers/active")
async def public_offers_active():
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()

@app.get("/public/catalog/tree")
async def public_catalog_tree(city_id: str):
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.tree(city_id=city_id)
        return r.json()

@app.get("/public/catalog/products")
async def public_catalog_products(
    city_id: str,
    page: int = 1,
    category_id: Optional[int] = None,
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
