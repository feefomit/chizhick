import os
import time
import json
import asyncio
import logging
import subprocess
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

# ---- ENV ----
API_KEY = os.getenv("API_KEY", "").strip()

PROXY = os.getenv("CHIZHIK_PROXY", "").strip() or None
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

ALLOW_ALL_CORS = os.getenv("ALLOW_ALL_CORS", "0") == "1"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://chizhick.ru,https://www.chizhick.ru")
ALLOWED_ORIGINS_LIST = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]

REDIS_URL = os.getenv("REDIS_URL", "").strip() or None
CACHE_PREFIX = os.getenv("CACHE_PREFIX", "chizhik").strip()

CHIZHIK_CONCURRENCY = int(os.getenv("CHIZHIK_CONCURRENCY", "1"))
CHIZHIK_TIMEOUT_SEC = int(os.getenv("CHIZHIK_TIMEOUT_SEC", "60"))
CHIZHIK_RETRY = int(os.getenv("CHIZHIK_RETRY", "1"))

CAMOUFOX_WARMUP = os.getenv("CAMOUFOX_WARMUP", "1") == "1"
CAMOUFOX_WARMUP_ATTEMPTS = int(os.getenv("CAMOUFOX_WARMUP_ATTEMPTS", "5"))

TTL_CITIES = int(os.getenv("TTL_CITIES", str(60 * 60 * 24)))       # 24h
TTL_TREE = int(os.getenv("TTL_TREE", str(60 * 60 * 12)))           # 12h
TTL_PRODUCTS = int(os.getenv("TTL_PRODUCTS", str(60 * 10)))        # 10m
TTL_OFFERS = int(os.getenv("TTL_OFFERS", str(60 * 10)))            # 10m
TTL_PRODUCT_INFO = int(os.getenv("TTL_PRODUCT_INFO", str(60 * 60)))# 1h

# ---- JSON (orjson optional) ----
try:
    import orjson  # type: ignore
    def j_dumps(obj: Any) -> str:
        return orjson.dumps(obj).decode("utf-8")
    def j_loads(s: str) -> Any:
        return orjson.loads(s)
except Exception:
    def j_dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    def j_loads(s: str) -> Any:
        return json.loads(s)

# ---- globals ----
rds: Optional[redis.Redis] = None
CACHE_MODE = "none"

_api: Optional[ChizhikAPI] = None
_api_lock = asyncio.Lock()

sem = asyncio.Semaphore(max(1, CHIZHIK_CONCURRENCY))

camoufox_ready = False
camoufox_error: Optional[str] = None


def cache_key(*parts: Any) -> str:
    return f"{CACHE_PREFIX}:" + ":".join(str(p) for p in parts)


def looks_like_playwright_crash(e: Exception) -> bool:
    s = str(e).lower()
    return (
        "page crashed" in s
        or "target closed" in s
        or "browser has been closed" in s
        or "navigation" in s and "crash" in s
    )


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


async def redis_get(key: str) -> Optional[Any]:
    if not rds:
        return None
    try:
        raw = await rds.get(key)
        if not raw:
            return None
        return j_loads(raw)
    except Exception as e:
        log.warning("Redis get failed: %s", e)
        return None


async def redis_set(key: str, value: Any, ttl: int) -> None:
    if not rds:
        return
    try:
        await rds.set(key, j_dumps(value), ex=ttl)
    except Exception as e:
        log.warning("Redis set failed: %s", e)


async def ensure_api() -> ChizhikAPI:
    global _api
    async with _api_lock:
        if _api is None:
            _api = ChizhikAPI(proxy=PROXY, headless=HEADLESS)
            await _api.__aenter__()
        return _api


async def restart_api() -> None:
    global _api
    async with _api_lock:
        if _api is not None:
            try:
                await _api.__aexit__(None, None, None)
            except Exception:
                pass
        _api = ChizhikAPI(proxy=PROXY, headless=HEADLESS)
        await _api.__aenter__()


async def call_chizhik(fetcher: Callable[[ChizhikAPI], Awaitable[Any]]) -> Any:
    api = await ensure_api()
    try:
        async with sem:
            return await asyncio.wait_for(fetcher(api), timeout=CHIZHIK_TIMEOUT_SEC)
    except Exception as e:
        if CHIZHIK_RETRY > 0 and looks_like_playwright_crash(e):
            log.warning("Playwright crash detected, restarting session...")
            await restart_api()
            api2 = await ensure_api()
            async with sem:
                return await asyncio.wait_for(fetcher(api2), timeout=CHIZHIK_TIMEOUT_SEC)
        raise


def ensure_ready_or_503():
    # чтобы запросы не висели, пока camoufox качается
    if CAMOUFOX_WARMUP and not camoufox_ready:
        raise HTTPException(status_code=503, detail="Каталог готовится (первый запуск). Повторите через 10–60 секунд.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rds, CACHE_MODE, camoufox_ready

    # Redis (не блокируем старт надолго)
    if REDIS_URL:
        try:
            rds = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            await asyncio.wait_for(rds.ping(), timeout=3)
            CACHE_MODE = "redis"
            log.info("Redis connected")
        except Exception as e:
            rds = None
            CACHE_MODE = "none"
            log.warning("Redis connect failed: %s", e)

    # Camoufox warmup (в фоне)
    camoufox_ready = _camoufox_installed()
    if CAMOUFOX_WARMUP and not camoufox_ready:
        asyncio.create_task(_camoufox_warmup_task())

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

# CORS
if ALLOW_ALL_CORS:
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"],
        allow_credentials=False, allow_methods=["*"], allow_headers=["*"]
    )
else:
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS_LIST if ALLOWED_ORIGINS_LIST else ["*"],
        allow_credentials=False, allow_methods=["*"], allow_headers=["*"]
    )

PUBLIC_PATHS = {"/", "/health", "/health/", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    # ВАЖНО: /api/* всегда без ключа
    if path in PUBLIC_PATHS or path.startswith("/api/"):
        return await call_next(request)

    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    return await call_next(request)

# ---- service ----
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
        "ts": int(time.time()),
    }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

# =========================
# API (/api/*) — только JSON
# =========================

@app.get("/api/geo/cities")
async def api_geo_cities(search: str = Query(...), page: int = 1):
    key = cache_key("cities", search, page)
    cached = await redis_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    ensure_ready_or_503()

    async def fetch(api: ChizhikAPI):
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

    data = await call_chizhik(fetch)
    await redis_set(key, data, TTL_CITIES)
    return JSONResponse(data, headers={"X-Cache": "MISS"})


@app.get("/api/offers/active")
async def api_offers_active():
    key = cache_key("offers", "active")
    cached = await redis_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    ensure_ready_or_503()

    async def fetch(api: ChizhikAPI):
        r = await api.Advertising.active_inout()
        return r.json()

    data = await call_chizhik(fetch)
    await redis_set(key, data, TTL_OFFERS)
    return JSONResponse(data, headers={"X-Cache": "MISS"})


@app.get("/api/catalog/tree")
async def api_catalog_tree(city_id: str = Query(...)):
    key = cache_key("tree", city_id)
    cached = await redis_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    ensure_ready_or_503()

    async def fetch(api: ChizhikAPI):
        r = await api.Catalog.tree(city_id=city_id)
        return r.json()

    data = await call_chizhik(fetch)
    await redis_set(key, data, TTL_TREE)
    return JSONResponse(data, headers={"X-Cache": "MISS"})


@app.get("/api/catalog/products")
async def api_catalog_products(
    city_id: str = Query(...),
    category_id: int = Query(..., ge=1),
    page: int = Query(1, ge=1, le=50),
):
    key = cache_key("products", city_id, category_id, page)
    cached = await redis_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    ensure_ready_or_503()

    async def fetch(api: ChizhikAPI):
        r = await api.Catalog.products_list(city_id=city_id, category_id=category_id, page=page)
        return r.json()

    data = await call_chizhik(fetch)
    await redis_set(key, data, TTL_PRODUCTS)
    return JSONResponse(data, headers={"X-Cache": "MISS"})


@app.get("/api/product/info")
async def api_product_info(product_id: int = Query(..., ge=1), city_id: Optional[str] = None):
    key = cache_key("product_info", product_id, city_id or "none")
    cached = await redis_get(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    ensure_ready_or_503()

    async def fetch(api: ChizhikAPI):
        r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
        return r.json()

    data = await call_chizhik(fetch)
    await redis_set(key, data, TTL_PRODUCT_INFO)
    return JSONResponse(data, headers={"X-Cache": "MISS"})


@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}
