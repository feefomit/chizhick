import os
import asyncio
import logging
import subprocess
from typing import Optional

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

# cache backends
try:
    import redis.asyncio as redis
    from fastapi_cache import FastAPICache
    from fastapi_cache.backends.redis import RedisBackend
    from fastapi_cache.backends.inmemory import InMemoryBackend
    from fastapi_cache.decorator import cache
except Exception:
    redis = None
    FastAPICache = None
    RedisBackend = None
    InMemoryBackend = None

    def cache(*args, **kwargs):
        def wrap(fn):
            return fn
        return wrap


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("chizhik-backend")

API_KEY = os.getenv("API_KEY")
PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://chizhick.ru,https://www.chizhick.ru")
ALLOWED_ORIGINS_LIST = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]
ALLOW_ALL_CORS = os.getenv("ALLOW_ALL_CORS", "0") == "1"

REDIS_URL = os.getenv("REDIS_URL")

CHIZHIK_CONCURRENCY = int(os.getenv("CHIZHIK_CONCURRENCY", "1"))
CHIZHIK_TIMEOUT_SEC = int(os.getenv("CHIZHIK_TIMEOUT_SEC", "90"))

CAMOUFOX_WARMUP = os.getenv("CAMOUFOX_WARMUP", "1") == "1"
CAMOUFOX_WARMUP_ATTEMPTS = int(os.getenv("CAMOUFOX_WARMUP_ATTEMPTS", "5"))

app = FastAPI(title="Chizhik Catalog Backend", version="1.0.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)

if ALLOW_ALL_CORS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS_LIST if ALLOWED_ORIGINS_LIST else ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

PUBLIC_PATHS = {"/", "/health", "/health/", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

sem = asyncio.Semaphore(max(1, CHIZHIK_CONCURRENCY))

CACHE_MODE = "none"  # redis | memory | none
camoufox_ready = False
camoufox_error: Optional[str] = None


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


def _camoufox_installed() -> bool:
    try:
        p = subprocess.run(["python", "-m", "camoufox", "path"], capture_output=True, text=True)
        return p.returncode == 0
    except Exception:
        return False


async def _camoufox_warmup_task():
    global camoufox_ready, camoufox_error

    if _camoufox_installed():
        camoufox_ready = True
        return

    log.warning("Camoufox not found. Downloading in background...")
    for i in range(1, CAMOUFOX_WARMUP_ATTEMPTS + 1):
        try:
            p = subprocess.run(["python", "-m", "camoufox", "fetch"])
            if p.returncode == 0 and _camoufox_installed():
                camoufox_ready = True
                camoufox_error = None
                log.info("Camoufox downloaded and ready")
                return
        except Exception as e:
            camoufox_error = str(e)

        await asyncio.sleep(i * 20)

    camoufox_ready = False
    camoufox_error = camoufox_error or "camoufox fetch failed"
    log.warning("Camoufox warmup failed: %s", camoufox_error)


@app.on_event("startup")
async def on_startup():
    global CACHE_MODE

    # cache init
    if FastAPICache is not None:
        if REDIS_URL and redis is not None and RedisBackend is not None:
            try:
                r = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
                await r.ping()
                FastAPICache.init(RedisBackend(r), prefix="chizhik")
                CACHE_MODE = "redis"
                log.info("Cache: redis enabled")
            except Exception as e:
                if InMemoryBackend is not None:
                    FastAPICache.init(InMemoryBackend(), prefix="chizhik")
                    CACHE_MODE = "memory"
                log.warning("Redis not available, cache fallback to memory: %s", e)
        else:
            if InMemoryBackend is not None:
                FastAPICache.init(InMemoryBackend(), prefix="chizhik")
                CACHE_MODE = "memory"
                log.info("Cache: memory enabled")
            else:
                CACHE_MODE = "none"

    # camoufox warmup
    if CAMOUFOX_WARMUP:
        asyncio.create_task(_camoufox_warmup_task())


@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "chizhik-backend"}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    return {
        "ok": True,
        "cache": CACHE_MODE,
        "camoufox_ready": camoufox_ready,
        "camoufox_error": camoufox_error,
    }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


def _ensure_ready():
    if CAMOUFOX_WARMUP and not camoufox_ready:
        raise HTTPException(status_code=503, detail="Browser warming up, retry soon")


@app.get("/public/geo/cities")
@cache(expire=60 * 60 * 24)
async def public_cities(search: str = Query(...), page: int = 1):
    _ensure_ready()
    from chizhik_api import ChizhikAPI
    async with sem:
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await asyncio.wait_for(
                api.Geolocation.cities_list(search_name=search, page=page),
                timeout=CHIZHIK_TIMEOUT_SEC,
            )
            return r.json()

@app.get("/public/offers/active")
@cache(expire=60 * 10)
async def public_offers_active():
    _ensure_ready()
    from chizhik_api import ChizhikAPI
    async with sem:
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await asyncio.wait_for(api.Advertising.active_inout(), timeout=CHIZHIK_TIMEOUT_SEC)
            return r.json()

@app.get("/public/catalog/tree")
@cache(expire=60 * 60 * 12)
async def public_catalog_tree(city_id: str):
    _ensure_ready()
    from chizhik_api import ChizhikAPI
    async with sem:
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await asyncio.wait_for(api.Catalog.tree(city_id=city_id), timeout=CHIZHIK_TIMEOUT_SEC)
            return r.json()

@app.get("/public/catalog/products")
@cache(expire=60 * 5)
async def public_catalog_products(
    city_id: str,
    page: int = 1,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
):
    _ensure_ready()
    from chizhik_api import ChizhikAPI
    async with sem:
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await asyncio.wait_for(
                api.Catalog.products_list(page=page, category_id=category_id, city_id=city_id, search=search),
                timeout=CHIZHIK_TIMEOUT_SEC,
            )
            return r.json()
