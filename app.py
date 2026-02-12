import os
import asyncio
import subprocess
from typing import Optional

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

# Redis + cache (если REDIS_URL не задан — работаем без кэша)
try:
    import redis.asyncio as redis
    from fastapi_cache import FastAPICache
    from fastapi_cache.backends.redis import RedisBackend
    from fastapi_cache.decorator import cache
except Exception:
    redis = None
    FastAPICache = None
    RedisBackend = None

    def cache(*args, **kwargs):  # no-op decorator
        def wrap(fn):
            return fn
        return wrap


# =========================
# ENV
# =========================
API_KEY = os.getenv("API_KEY")  # защищает всё, что НЕ /public/*
PROXY = os.getenv("CHIZHIK_PROXY")  # опционально: user:pass@host:port
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

# CORS
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://chizhick.ru,https://www.chizhick.ru,*")
ALLOWED_ORIGINS_LIST = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]

REDIS_URL = os.getenv("REDIS_URL")  # пример: redis://default:%29...@192.168.0.5:6379/0

# Camoufox warmup (Вариант B)
CAMOUFOX_WARMUP = os.getenv("CAMOUFOX_WARMUP", "1").lower() in ("1", "true", "yes", "on")
WARMUP_TIMEOUT_SEC = int(os.getenv("CAMOUFOX_WARMUP_TIMEOUT_SEC", "3600"))  # до 1 часа
MAX_CONCURRENCY = int(os.getenv("CHIZHIK_MAX_CONCURRENCY", "1"))  # чтобы не падал браузер по памяти

# =========================
# App
# =========================
app = FastAPI(title="Chizhik Catalog Backend", version="1.0.0")

app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS (если указано "*" — разрешаем всем; allow_credentials=False позволяет wildcard)
allow_all = "*" in ALLOWED_ORIGINS_LIST
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all else ALLOWED_ORIGINS_LIST,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_PATHS = {
    "/", "/health", "/health/",
    "/docs", "/openapi.json", "/redoc",
    "/favicon.ico",
}

# Warmup state
_ready_evt = asyncio.Event()
_ready_err: Optional[str] = None
_sem = asyncio.Semaphore(MAX_CONCURRENCY)


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    # preflight OPTIONS всегда пропускаем
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


async def _camoufox_fetch_if_needed():
    """
    Вариант B: не качаем camoufox на build, а качаем на старте.
    Делается в фоне, чтобы /health сразу был 200.
    """
    global _ready_err

    if not CAMOUFOX_WARMUP:
        _ready_evt.set()
        return

    try:
        # Пробуем "на всякий" импортировать camoufox — если пакета нет, будет ошибка
        import camoufox  # noqa: F401

        # Запускаем fetch (если уже скачано — обычно быстро и без докачки)
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
        raise HTTPException(status_code=503, detail="Browser warming up, try again in ~1-3 minutes")
    if _ready_err:
        raise HTTPException(status_code=503, detail=_ready_err)


@app.on_event("startup")
async def on_startup():
    # Redis cache init
    if REDIS_URL and redis and FastAPICache and RedisBackend:
        r = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        FastAPICache.init(RedisBackend(r), prefix="chizhik")

    # warmup в фоне
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
    # всегда 200 (иначе Timeweb завалит деплой)
    return {
        "ok": True,
        "cache": "redis" if REDIS_URL else "none",
        "warmup": "off" if not CAMOUFOX_WARMUP else ("ready" if _ready_evt.is_set() and not _ready_err else "starting"),
        "warmup_error": _ready_err,
    }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


# =========================
# PUBLIC API
# =========================
@app.get("/public/geo/cities")
@cache(expire=60 * 60 * 24)  # 24 часа
async def public_cities(search: str = Query(...), page: int = 1):
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async with _sem:
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Geolocation.cities_list(search_name=search, page=page)
            return r.json()

@app.get("/public/offers/active")
@cache(expire=60 * 10)  # 10 минут
async def public_offers_active():
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async with _sem:
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Advertising.active_inout()
            return r.json()

@app.get("/public/catalog/tree")
@cache(expire=60 * 60 * 12)  # 12 часов
async def public_catalog_tree(city_id: str):
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async with _sem:
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
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async with _sem:
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
    await _ensure_ready_or_503()
    from chizhik_api import ChizhikAPI

    async with _sem:
        async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
            r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
            return r.json()


# =========================
# PRIVATE
# =========================
@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}
