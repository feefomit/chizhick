import os
import json
import asyncio
from typing import Optional, Dict, Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

# --- FIX для библиотек, которые ожидают typing.override в Py<3.12
import typing
if not hasattr(typing, "override"):
    def override(fn):  # type: ignore
        return fn
    typing.override = override  # type: ignore

# Redis + cache (если REDIS_URL не задан — работаем без кэша)
try:
    import redis.asyncio as redis
except Exception:
    redis = None


# =========================
# ENV
# =========================
API_KEY = os.getenv("API_KEY")  # защищает /private/*
PROXY = os.getenv("CHIZHIK_PROXY")  # опционально: http://user:pass@host:port
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://chizhick.ru,https://www.chizhick.ru"
)
ALLOWED_ORIGINS_LIST = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]

REDIS_URL = os.getenv("REDIS_URL")  # redis://user:pass@host:6379/0
UPSTREAM_TIMEOUT_SEC = int(os.getenv("UPSTREAM_TIMEOUT_SEC", "80"))

# TTL
TTL_GEO_SEC = int(os.getenv("TTL_GEO_SEC", str(24 * 60 * 60)))      # 24h
TTL_OFFERS_SEC = int(os.getenv("TTL_OFFERS_SEC", str(10 * 60)))     # 10m
TTL_TREE_SEC = int(os.getenv("TTL_TREE_SEC", str(24 * 60 * 60)))    # 24h
TTL_PRODUCTS_SEC = int(os.getenv("TTL_PRODUCTS_SEC", str(5 * 60)))  # 5m
TTL_PRODUCT_INFO_SEC = int(os.getenv("TTL_PRODUCT_INFO_SEC", str(60 * 60)))  # 1h

# =========================
# APP
# =========================
app = FastAPI(title="Chizhik Catalog Backend", version="2.0.0")

# gzip для JSON
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS для фронта на другом домене
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS_LIST if ALLOWED_ORIGINS_LIST else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_PATHS = {"/", "/health", "/health/", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

# Redis client
rds = None

# locks чтобы не строить одно и то же параллельно
locks: Dict[str, asyncio.Lock] = {}

def _lock(key: str) -> asyncio.Lock:
    if key not in locks:
        locks[key] = asyncio.Lock()
    return locks[key]


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    # OPTIONS (CORS preflight) всегда пропускаем
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    # public: /, /health, /api/*
    if path in PUBLIC_PATHS or path.startswith("/api"):
        return await call_next(request)

    # private: /private/*
    if path.startswith("/private"):
        if API_KEY and request.headers.get("X-API-Key") != API_KEY:
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    return await call_next(request)


@app.on_event("startup")
async def on_startup():
    global rds
    if REDIS_URL and redis is not None:
        # decode_responses=True чтобы get/set работали со строками
        rds = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)


# =========================
# helpers: redis json cache
# =========================
async def cache_get_json(key: str) -> Optional[Any]:
    if not rds:
        return None
    try:
        v = await rds.get(key)
        if not v:
            return None
        return json.loads(v)
    except Exception:
        return None

async def cache_set_json(key: str, data: Any, ttl: int):
    if not rds:
        return
    try:
        await rds.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)
    except Exception:
        pass


# =========================
# health
# =========================
@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "chizhik-backend"}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    out = {"ok": True, "cache": "redis" if rds else "none"}
    return out

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


# =========================
# upstream wrapper
# =========================
async def with_timeout(coro):
    return await asyncio.wait_for(coro, timeout=UPSTREAM_TIMEOUT_SEC)


# =========================
# API endpoints (public)
# =========================

@app.get("/api/geo/cities")
async def geo_cities(search: str = Query(...), page: int = 1):
    key = f"geo:cities:{search}:{page}"
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await with_timeout(api.Geolocation.cities_list(search_name=search, page=page))
        data = r.json()

    await cache_set_json(key, data, TTL_GEO_SEC)
    return data


@app.get("/api/offers/active")
async def offers_active():
    key = "offers:active"
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await with_timeout(api.Advertising.active_inout())
        data = r.json()

    await cache_set_json(key, data, TTL_OFFERS_SEC)
    return data


# --------- CATALOG TREE (главная боль)
async def _build_tree(city_id: str) -> Any:
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await with_timeout(api.Catalog.tree(city_id=city_id))
        data = r.json()
    await cache_set_json(f"catalog:tree:{city_id}", data, TTL_TREE_SEC)
    return data

@app.get("/api/catalog/tree")
async def catalog_tree(city_id: str):
    key = f"catalog:tree:{city_id}"

    # 1) быстрый путь — отдаём кэш
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    # 2) если кэша нет — не даём запросу висеть: запускаем сборку в фоне и возвращаем 202
    lk = _lock(key)
    if lk.locked():
        return JSONResponse({"status": "building"}, status_code=202)

    async with lk:
        # вдруг другой запрос уже собрал
        cached2 = await cache_get_json(key)
        if cached2 is not None:
            return cached2

        # запускаем сборку, но НЕ блокируем клиент надолго
        # (чтобы не было ERR_TIMED_OUT на стороне браузера)
        asyncio.create_task(_build_tree(city_id))
        return JSONResponse({"status": "building"}, status_code=202)


@app.get("/api/catalog/products")
async def catalog_products(
    city_id: str,
    page: int = 1,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
):
    key = f"catalog:products:{city_id}:{category_id}:{search}:{page}"
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await with_timeout(api.Catalog.products_list(
            page=page,
            category_id=category_id,
            city_id=city_id,
            search=search,
        ))
        data = r.json()

    await cache_set_json(key, data, TTL_PRODUCTS_SEC)
    return data


@app.get("/api/product/info")
async def product_info(product_id: int, city_id: Optional[str] = None):
    key = f"product:info:{product_id}:{city_id}"
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await with_timeout(api.Catalog.Product.info(product_id=product_id, city_id=city_id))
        data = r.json()

    await cache_set_json(key, data, TTL_PRODUCT_INFO_SEC)
    return data


# =========================
# PRIVATE (под API_KEY)
# =========================
@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}

@app.post("/private/cache/clear")
async def private_cache_clear(prefix: str = "catalog:"):
    if not rds:
        return {"ok": False, "detail": "redis disabled"}
    # очень простой вариант очистки по маске (может быть медленным на большом ключспейсе)
    keys = await rds.keys(f"{prefix}*")
    if keys:
        await rds.delete(*keys)
    return {"ok": True, "deleted": len(keys)}
