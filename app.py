import os
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

# Redis + cache (если REDIS_URL не задан — работаем без кэша)
try:
    import redis.asyncio as redis
    from fastapi_cache import FastAPICache
    from fastapi_cache.backends.redis import RedisBackend
    from fastapi_cache.decorator import cache
except Exception:  # если не установлены зависимости, приложение все равно стартует
    redis = None
    FastAPICache = None
    RedisBackend = None

    def cache(*args, **kwargs):  # no-op decorator
        def wrap(fn):
            return fn
        return wrap


# =========================
# ENV настройки
# =========================
API_KEY = os.getenv("API_KEY")  # защищает всё, что НЕ /public/*
PROXY = os.getenv("CHIZHIK_PROXY")  # опционально: http://user:pass@host:port
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

# CORS: перечисли домены через запятую, иначе будут дефолты
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://chizhick.ru,https://www.chizhick.ru")
ALLOWED_ORIGINS_LIST = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]

REDIS_URL = os.getenv("REDIS_URL")  # пример: redis://user:pass@host:6379/0

app = FastAPI(title="Chizhik Catalog Backend", version="1.0.0")

# Сжатие JSON — меньше трафика, чуть быстрее
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS (важно для фронта на другом домене)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS_LIST if ALLOWED_ORIGINS_LIST else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_PATHS = {
    "/", "/health", "/health/",
    "/docs", "/openapi.json", "/redoc",
    "/favicon.ico",
}


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    # ВАЖНО: preflight OPTIONS всегда пропускаем, иначе CORS ломается
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    # public + служебные — без ключа
    if path in PUBLIC_PATHS or path.startswith("/public"):
        return await call_next(request)

    # остальное — под ключом (если ключ задан)
    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    return await call_next(request)


@app.on_event("startup")
async def on_startup():
    # Инициализация Redis-кэша (если задан REDIS_URL)
    if not REDIS_URL:
        return
    if redis is None or FastAPICache is None or RedisBackend is None:
        return

    r = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    FastAPICache.init(RedisBackend(r), prefix="chizhik")


# =========================
# Служебные
# =========================
@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "chizhik-backend"}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    # health всегда 200, даже если Redis недоступен
    out = {"ok": True}
    out["cache"] = "redis" if REDIS_URL else "none"
    return out

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


# =========================
# PUBLIC API (для сайта)
# =========================

@app.get("/public/geo/cities")
@cache(expire=60 * 60 * 24)  # 24 часа
async def public_cities(search: str = Query(...), page: int = 1):
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

@app.get("/public/offers/active")
@cache(expire=60 * 10)  # 10 минут
async def public_offers_active():
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()

@app.get("/public/catalog/tree")
@cache(expire=60 * 60 * 12)  # 12 часов
async def public_catalog_tree(city_id: str):
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.tree(city_id=city_id)
        return r.json()

@app.get("/public/catalog/products")
@cache(expire=60 * 5)  # 5 минут
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

@app.get("/public/product/info")
@cache(expire=60 * 60)  # 1 час
async def public_product_info(product_id: int, city_id: Optional[str] = None):
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
        return r.json()


# =========================
# PRIVATE (опционально) — проверка кэша / админские ручки
# =========================
@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}
