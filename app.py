import os
import asyncio
import logging
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Optional, Callable, Awaitable

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse, Response

import redis.asyncio as redis
from chizhik_api import ChizhikAPI

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("chizhik-backend")

API_KEY = os.getenv("API_KEY")

PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://chizhick.ru,https://www.chizhick.ru")
ALLOW_ALL_CORS = os.getenv("ALLOW_ALL_CORS", "0") == "1"

REDIS_URL = os.getenv("REDIS_URL")

# стабильность
CHIZHIK_CONCURRENCY = int(os.getenv("CHIZHIK_CONCURRENCY", "1"))
CHIZHIK_TIMEOUT_SEC = int(os.getenv("CHIZHIK_TIMEOUT_SEC", "60"))
CHIZHIK_RETRY = int(os.getenv("CHIZHIK_RETRY", "1"))  # 1 повтор после рестарта браузера

# TTL (секунды)
TTL_CITIES = 60 * 60 * 24          # 24 часа
TTL_TREE = 60 * 60 * 12            # 12 часов
TTL_PRODUCTS = 60 * 10             # 10 минут
TTL_OFFERS = 60 * 10               # 10 минут

# =========================
# globals
# =========================
rds: Optional[redis.Redis] = None

_api: Optional[ChizhikAPI] = None
_api_lock = asyncio.Lock()
sem = asyncio.Semaphore(max(1, CHIZHIK_CONCURRENCY))

_inflight: set[str] = set()
_inflight_lock = asyncio.Lock()


def j_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

def j_loads(s: str) -> Any:
    return json.loads(s)

def looks_like_playwright_crash(e: Exception) -> bool:
    s = str(e).lower()
    return ("page crashed" in s) or ("target closed" in s) or ("browser has been closed" in s)


async def ensure_api() -> ChizhikAPI:
    global _api
    async with _api_lock:
        if _api is None:
            _api = ChizhikAPI(proxy=PROXY, headless=HEADLESS)
            await _api.__aenter__()
        return _api

async def restart_api():
    global _api
    async with _api_lock:
        if _api is not None:
            try:
                await _api.__aexit__(None, None, None)
            except Exception:
                pass
        _api = ChizhikAPI(proxy=PROXY, headless=HEADLESS)
        await _api.__aenter__()


async def cache_get(key: str) -> Optional[Any]:
    if not rds:
        return None
    raw = await rds.get(key)
    if not raw:
        return None
    return j_loads(raw)

async def cache_set(key: str, value: Any, ttl: int):
    if not rds:
        return
    await rds.set(key, j_dumps(value), ex=ttl)


async def schedule_fill(key: str, ttl: int, fetcher: Callable[[], Awaitable[Any]]):
    async with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)

    async def job():
        try:
            data = await fetcher()
            await cache_set(key, data, ttl)
        except Exception as e:
            log.warning("fill failed for %s: %s", key, e)
        finally:
            async with _inflight_lock:
                _inflight.discard(key)

    asyncio.create_task(job())


async def fetch_with_retry(fetcher: Callable[[], Awaitable[Any]]):
    try:
        return await fetcher()
    except Exception as e:
        if CHIZHIK_RETRY > 0 and looks_like_playwright_crash(e):
            log.warning("Playwright crash detected, restarting session...")
            await restart_api()
            return await fetcher()
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rds
    if REDIS_URL:
        try:
            rds = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            await rds.ping()
            log.info("Redis connected")
        except Exception as e:
            rds = None
            log.warning("Redis connect failed: %s", e)

    yield

    # shutdown
    try:
        if rds:
            await rds.close()
    except Exception:
        pass

    global _api
    async with _api_lock:
        if _api is not None:
            try:
                await _api.__aexit__(None, None, None)
            except Exception:
                pass
            _api = None


app = FastAPI(title="Chizhik Backend", version="1.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=800)

if ALLOW_ALL_CORS:
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"]
    )
else:
    origins = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins if origins else ["*"],
        allow_credentials=False, allow_methods=["*"], allow_headers=["*"]
    )

PUBLIC_PATHS = {"/", "/health", "/health/", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/public"):
        return await call_next(request)

    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    return await call_next(request)


@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "chizhik-backend"}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    async with _inflight_lock:
        inflight = len(_inflight)
    return {
        "ok": True,
        "cache": "redis" if rds else "none",
        "inflight": inflight,
        "ts": int(time.time()),
    }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


# =========================
# PUBLIC API
# =========================

@app.get("/public/geo/cities")
async def public_cities(search: str = Query(...), page: int = 1):
    key = f"cities:{search}:{page}"
    cached = await cache_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    async def fetcher():
        api = await ensure_api()
        async with sem:
            r = await asyncio.wait_for(api.Geolocation.cities_list(search_name=search, page=page), timeout=CHIZHIK_TIMEOUT_SEC)
            return r.json()

    await schedule_fill(key, TTL_CITIES, lambda: fetch_with_retry(fetcher))
    raise HTTPException(status_code=503, detail="Каталог готовится, попробуйте ещё раз через 5–30 секунд")


@app.get("/public/offers/active")
async def public_offers_active():
    key = "offers:active"
    cached = await cache_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    async def fetcher():
        api = await ensure_api()
        async with sem:
            r = await asyncio.wait_for(api.Advertising.active_inout(), timeout=CHIZHIK_TIMEOUT_SEC)
            return r.json()

    await schedule_fill(key, TTL_OFFERS, lambda: fetch_with_retry(fetcher))
    raise HTTPException(status_code=503, detail="Каталог готовится, попробуйте ещё раз через 5–30 секунд")


@app.get("/public/catalog/tree")
async def public_catalog_tree(city_id: str):
    key = f"tree:{city_id}"
    cached = await cache_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    async def fetcher():
        api = await ensure_api()
        async with sem:
            r = await asyncio.wait_for(api.Catalog.tree(city_id=city_id), timeout=CHIZHIK_TIMEOUT_SEC)
            return r.json()

    await schedule_fill(key, TTL_TREE, lambda: fetch_with_retry(fetcher))
    raise HTTPException(status_code=503, detail="Каталог готовится, попробуйте ещё раз через 5–30 секунд")


@app.get("/public/catalog/products")
async def public_catalog_products(
    city_id: str,
    category_id: int,
    page: int = 1,
):
    key = f"products:{city_id}:{category_id}:{page}"
    cached = await cache_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    async def fetcher():
        api = await ensure_api()
        async with sem:
            r = await asyncio.wait_for(
                api.Catalog.products_list(city_id=city_id, category_id=category_id, page=page),
                timeout=CHIZHIK_TIMEOUT_SEC,
            )
            return r.json()

    await schedule_fill(key, TTL_PRODUCTS, lambda: fetch_with_retry(fetcher))
    raise HTTPException(status_code=503, detail="Каталог готовится, попробуйте ещё раз через 5–30 секунд")
