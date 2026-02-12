import os

# --- PY<3.12 compatibility: provide typing.override ---
import typing
if not hasattr(typing, "override"):
    def override(func):
        return func
    typing.override = override
# ------------------------------------------------------

import asyncio
import subprocess
import logging
import json
import time
from typing import Optional, Any

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("chizhik-backend")

# =========================
# ENV
# =========================
API_KEY = (os.getenv("API_KEY") or "").strip()  # защищает только НЕ /api и НЕ /public
PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://chizhick.ru,https://www.chizhick.ru")
ALLOWED_ORIGINS_LIST = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]

REDIS_URL = os.getenv("REDIS_URL")  # redis://default:%29...@192.168.0.5:6379/0

# Warmup camoufox (вариант B)
CAMOUFOX_WARMUP = os.getenv("CAMOUFOX_WARMUP", "1").lower() in ("1", "true", "yes", "on")
WARMUP_TIMEOUT_SEC = int(os.getenv("CAMOUFOX_WARMUP_TIMEOUT_SEC", "3600"))

# Ограничение параллельности и таймауты
MAX_CONCURRENCY = int(os.getenv("CHIZHIK_MAX_CONCURRENCY", "1"))
CHIZHIK_TIMEOUT_SEC = int(os.getenv("CHIZHIK_TIMEOUT_SEC", "45"))

# Tree ускорение
TREE_TTL = int(os.getenv("TREE_TTL", str(60 * 60 * 12)))  # 12 часов
TREE_BUILD_TIMEOUT = int(os.getenv("TREE_BUILD_TIMEOUT", "20"))  # 20 сек

# =========================
# Cache (fastapi-cache2)
# =========================
cache_backend_name = "none"

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

    def cache(*args, **kwargs):  # no-op decorator
        def wrap(fn):
            return fn
        return wrap

# Redis client for manual caching (tree)
rds = None  # type: ignore

# In-memory fallback cache for tree (если Redis нет)
_local_tree_cache: dict[str, tuple[float, Any]] = {}

# =========================
# App
# =========================
app = FastAPI(title="Chizhik Catalog Backend", version="1.0.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS
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

# /api и /public всегда доступны без ключа
PUBLIC_PREFIXES = ("/api", "/public")

_ready_evt = asyncio.Event()
_ready_err: Optional[str] = None
_sem = asyncio.Semaphore(MAX_CONCURRENCY)


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
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


async def _camoufox_fetch_if_needed():
    """Вариант B: camoufox fetch на старте в фоне. /health всегда 200."""
    global _ready_err

    if not CAMOUFOX_WARMUP:
        _ready_evt.set()
        return

    if _camoufox_installed():
        _ready_evt.set()
        return

    try:
        import camoufox  # noqa: F401

        proc = await asyncio.create_subprocess_exec(
            "python", "-m", "camoufox", "fetch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )

        try:
            await asyncio.wait_for(proc.wait(), timeout=WARMUP_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            proc.kill()
            _ready_err = f"camoufox fetch timeout after {WARMUP_TIMEOUT_SEC}s"
            _ready_evt.set()
            return

        if proc.returncode != 0:
            out = b""
            if proc.stdout:
                out = await proc.stdout.read()
            _ready_err = f"camoufox fetch failed, code={proc.returncode}, out={out[:500].decode('utf-8','ignore')}"
            _ready_evt.set()
            return

        _ready_evt.set()

    except Exception as e:
        _ready_err = f"warmup error: {e}"
        _ready_evt.set()


async def _ensure_ready_or_503():
    if not CAMOUFOX_WARMUP:
        return
    if not _ready_evt.is_set():
        raise HTTPException(status_code=503, detail="Browser warming up, retry in 10–60s")
    if _ready_err:
        raise HTTPException(status_code=503, detail=_ready_err)


def _looks_like_browser_crash(e: Exception) -> bool:
    s = str(e).lower()
    return ("page crashed" in s) or ("target closed" in s) or ("browser has been closed" in s)


async def _call_chizhik(fn):
    """Обёртка: любые проблемы upstream превращаем в 503 (а не 500)."""
    try:
        async with _sem:
            return await asyncio.wait_for(fn(), timeout=CHIZHIK_TIMEOUT_SEC)
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Upstream timeout. Retry in 10–30s")
    except Exception as e:
        log.exception("Upstream error: %s", e)
        if _looks_like_browser_crash(e):
            raise HTTPException(status_code=503, detail="Upstream browser crashed. Retry in 10–30s")
        raise HTTPException(status_code=503, detail="Upstream error. Retry in 10–30s")


async def cache_get_json(key: str):
    """Redis -> json; fallback -> in-memory TTL."""
    now = time.time()

    if rds:
        try:
            v = await rds.get(key)
            if v:
                return json.loads(v)
        except Exception:
            pass

    item = _local_tree_cache.get(key)
    if not item:
        return None
    exp, data = item
    if exp < now:
        _local_tree_cache.pop(key, None)
        return None
    return data


async def cache_set_json(key: str, data: Any, ttl: int):
    """Сохраняем в Redis (если есть), иначе в память."""
    now = time.time()
    _local_tree_cache[key] = (now + ttl, data)

    if rds:
        try:
            await rds.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)
        except Exception:
            pass


async def _prefetch_tree(city_id: str):
    """Фоновая сборка дерева и запись в кэш."""
    from chizhik_api import ChizhikAPI

    async def run():
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Catalog.tree(city_id=city_id)
            return r.json()

    try:
        data = await _call_chizhik(run)
        await cache_set_json(f"tree:{city_id}", data, TREE_TTL)
    except Exception:
        pass


@app.on_event("startup")
async def on_startup():
    """Важное: инициализируем FastAPICache всегда (Redis или Memory), чтобы @cache не давал 500."""
    global rds, cache_backend_name

    # 1) Redis
    if REDIS_URL and redis and FastAPICache and RedisBackend:
        try:
            rds = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            await asyncio.wait_for(rds.ping(), timeout=3)
            FastAPICache.init(RedisBackend(rds), prefix="chizhik")
            cache_backend_name = "redis"
            log.info("Cache backend: redis")
        except Exception as e:
            log.warning("Redis init failed, fallback to memory: %s", e)
            rds = None

    # 2) Fallback memory
    if (cache_backend_name == "none") and FastAPICache and InMemoryBackend:
        FastAPICache.init(InMemoryBackend(), prefix="chizhik")
        cache_backend_name = "memory"
        log.info("Cache backend: memory")

    # Warmup в фоне
    asyncio.create_task(_camoufox_fetch_if_needed())


# =========================
# Service
# =========================
@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "chizhik-backend"}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    return {
        "ok": True,
        "cache": cache_backend_name,
        "warmup": "off" if not CAMOUFOX_WARMUP else ("ready" if _ready_evt.is_set() and not _ready_err else "starting"),
        "warmup_error": _ready_err,
    }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


# =========================
# API (дублируем /api и /public)
# =========================

@app.get("/api/geo/cities")
@app.get("/public/geo/cities")
@cache(expire=60 * 60 * 24)
async def cities(search: str = Query(...), page: int = 1):
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async def run():
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Geolocation.cities_list(search_name=search, page=page)
            return r.json()

    return await _call_chizhik(run)


@app.get("/api/offers/active")
@app.get("/public/offers/active")
@cache(expire=60 * 10)
async def offers_active():
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async def run():
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Advertising.active_inout()
            return r.json()

    return await _call_chizhik(run)


@app.get("/api/catalog/tree")
@app.get("/public/catalog/tree")
async def catalog_tree(city_id: str):
    """
    Быстрое дерево:
    - Redis HIT -> сразу
    - Redis MISS -> пытаемся собрать за TREE_BUILD_TIMEOUT сек
    - не успели -> 202 и сборка в фоне (следующий запрос будет HIT)
    """
    await _ensure_ready_or_503()

    key = f"tree:{city_id}"
    cached = await cache_get_json(key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    from chizhik_api import ChizhikAPI

    async def build():
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Catalog.tree(city_id=city_id)
            return r.json()

    try:
        data = await asyncio.wait_for(_call_chizhik(build), timeout=TREE_BUILD_TIMEOUT)
        await cache_set_json(key, data, TREE_TTL)
        return JSONResponse(data, headers={"X-Cache": "MISS"})
    except asyncio.TimeoutError:
        asyncio.create_task(_prefetch_tree(city_id))
        return JSONResponse(
            {"detail": "Building categories tree, retry in 5-15 seconds"},
            status_code=202
        )
    except HTTPException as e:
        # уже нормализовано в 503, просто пробрасываем
        raise e


@app.get("/api/catalog/products")
@app.get("/public/catalog/products")
@cache(expire=60 * 5)
async def catalog_products(
    city_id: str,
    page: int = 1,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
):
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async def run():
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Catalog.products_list(
                page=page,
                category_id=category_id,
                city_id=city_id,
                search=search,
            )
            return r.json()

    return await _call_chizhik(run)


@app.get("/api/product/info")
@app.get("/public/product/info")
@cache(expire=60 * 60)
async def product_info(product_id: int, city_id: Optional[str] = None):
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async def run():
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
            return r.json()

    return await _call_chizhik(run)


# =========================
# PRIVATE (под API_KEY)
# ==========================
@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}
