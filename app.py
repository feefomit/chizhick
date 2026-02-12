import os
os.environ.setdefault("HOME", "/opt/home")
os.environ.setdefault("XDG_CACHE_HOME", "/opt/xdg-cache")
os.environ.setdefault("CAMOUFOX_CACHE_DIR", "/opt/camoufox-cache")

import json
import asyncio
import logging
from typing import Optional, Any, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

# --- env (дублируем, чтобы платформы не ломали HOME)
os.environ.setdefault("HOME", "/tmp")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
os.environ.setdefault("CAMOUFOX_CACHE_DIR", "/tmp/.cache/camoufox")

logger = logging.getLogger("chizhik-backend")
logging.basicConfig(level=logging.INFO)

API_KEY = os.getenv("API_KEY")  # защищает /private/*
PROXY = os.getenv("CHIZHIK_PROXY")  # опционально
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://chizhick.ru,https://www.chizhick.ru")
ALLOWED_ORIGINS_LIST = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]

REDIS_URL = os.getenv("REDIS_URL")
CHIZHIK_TIMEOUT_SEC = int(os.getenv("CHIZHIK_TIMEOUT_SEC", "80"))

TTL_GEO_SEC = int(os.getenv("TTL_GEO_SEC", str(24 * 60 * 60)))
TTL_OFFERS_SEC = int(os.getenv("TTL_OFFERS_SEC", str(10 * 60)))
TTL_TREE_SEC = int(os.getenv("TTL_TREE_SEC", str(24 * 60 * 60)))
TTL_PRODUCTS_SEC = int(os.getenv("TTL_PRODUCTS_SEC", str(5 * 60)))
TTL_PRODUCT_INFO_SEC = int(os.getenv("TTL_PRODUCT_INFO_SEC", str(60 * 60)))

# Redis
rds = None
try:
    import redis.asyncio as redis
except Exception:
    redis = None

# Один "живой" ChizhikAPI на всё приложение
_api = None
_api_lock = asyncio.Lock()
_warmup_state = {"status": "starting", "error": None}


PUBLIC_PATHS = {"/", "/health", "/health/", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

def _cache_key(*parts: Any) -> str:
    return ":".join(str(p) for p in parts)

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

async def cache_lock(key: str, ttl: int = 90) -> bool:
    """Дешёвый распределённый lock в Redis (чтобы не строить одно и то же параллельно)."""
    if not rds:
        return True  # без Redis считаем, что lock получен
    try:
        return bool(await rds.set(key, "1", nx=True, ex=ttl))
    except Exception:
        return True

async def cache_unlock(key: str):
    if not rds:
        return
    try:
        await rds.delete(key)
    except Exception:
        pass

async def _ensure_api():
    """Поднимаем ChizhikAPI один раз и держим открытым."""
    global _api
    if _api is not None:
        return _api

    from chizhik_api import ChizhikAPI
    _api = ChizhikAPI(proxy=PROXY, headless=HEADLESS)
    await _api.__aenter__()  # прогрев + запуск браузера
    return _api

async def _reset_api():
    global _api
    if _api is None:
        return
    try:
        await _api.__aexit__(None, None, None)
    except Exception:
        pass
    _api = None

async def _call_chizhik(fn, *, retry_restart: bool = True):
    """
    Все вызовы к chizhik_api через один lock:
    - не плодим браузеры
    - при падении/краше рестартим и пробуем 1 раз
    """
    async with _api_lock:
        try:
            api = await _ensure_api()
            return await asyncio.wait_for(fn(api), timeout=CHIZHIK_TIMEOUT_SEC)
        except Exception as e:
            msg = str(e)
            logger.error("Upstream error: %s", msg)

            if retry_restart:
                await _reset_api()
                try:
                    api = await _ensure_api()
                    return await asyncio.wait_for(fn(api), timeout=CHIZHIK_TIMEOUT_SEC)
                except Exception as e2:
                    logger.error("Upstream error after restart: %s", str(e2))
                    raise
            raise

async def _warmup_task():
    global _warmup_state
    try:
        await _call_chizhik(lambda api: api.Advertising.active_inout(), retry_restart=True)
        _warmup_state["status"] = "ready"
        _warmup_state["error"] = None
    except Exception as e:
        _warmup_state["status"] = "error"
        _warmup_state["error"] = str(e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rds
    if REDIS_URL and redis is not None:
        rds = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)

    # прогрев в фоне (не блокирует старт/health)
    asyncio.create_task(_warmup_task())

    yield

    await _reset_api()
    try:
        if rds:
            await rds.aclose()
    except Exception:
        pass

app = FastAPI(title="Chizhik Catalog Backend", version="3.0.0", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS_LIST if ALLOWED_ORIGINS_LIST else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    # ИСПРАВЛЕНО: изменен префикс с /api на /public
    if path in PUBLIC_PATHS or path.startswith("/public"):
        return await call_next(request)

    if path.startswith("/private") and API_KEY and request.headers.get("X-API-Key") != API_KEY:
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    return await call_next(request)

@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "chizhik-backend"}

@app.get("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
async def health():
    return {
        "ok": True,
        "cache": "redis" if rds else "none",
        "warmup": _warmup_state["status"],
        "warmup_error": _warmup_state["error"],
    }

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


# -------- PUBLIC API --------

@app.get("/public/geo/cities")
async def geo_cities(search: str = Query(...), page: int = 1):
    key = _cache_key("geo", "cities", search, page)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    async def run(api):
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

    try:
        data = await _call_chizhik(run)
        await cache_set_json(key, data, TTL_GEO_SEC)
        return data
    except Exception as e:
        return JSONResponse({"detail": "Upstream error", "error": str(e)}, status_code=503)


@app.get("/public/offers/active")
async def offers_active():
    key = "offers:active"
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    async def run(api):
        r = await api.Advertising.active_inout()
        return r.json()

    try:
        data = await _call_chizhik(run)
        await cache_set_json(key, data, TTL_OFFERS_SEC)
        return data
    except Exception as e:
        return JSONResponse({"detail": "Upstream error", "error": str(e)}, status_code=503)


@app.get("/public/catalog/tree")
async def catalog_tree(city_id: str):
    key = _cache_key("catalog", "tree", city_id)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    lock_key = _cache_key("lock", "tree", city_id)
    if not await cache_lock(lock_key, ttl=120):
        # уже строится другим запросом
        return JSONResponse({"status": "building"}, status_code=202)

    try:
        async def run(api):
            r = await api.Catalog.tree(city_id=city_id)
            return r.json()

        data = await _call_chizhik(run)
        await cache_set_json(key, data, TTL_TREE_SEC)
        return data
    except Exception as e:
        return JSONResponse({"detail": "Upstream error", "error": str(e)}, status_code=503)
    finally:
        await cache_unlock(lock_key)


@app.get("/public/catalog/products")
async def catalog_products(
    city_id: str,
    page: int = 1,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
):
    key = _cache_key("catalog", "products", city_id, category_id, search, page)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    # защита от "кликов" по одной и той же категории
    lock_key = _cache_key("lock", "products", city_id, category_id, search, page)
    if not await cache_lock(lock_key, ttl=60):
        return JSONResponse({"status": "building"}, status_code=202)

    try:
        async def run(api):
            r = await api.Catalog.products_list(
                page=page,
                category_id=category_id,
                city_id=city_id,
                search=search,
            )
            return r.json()

        data = await _call_chizhik(run)
        await cache_set_json(key, data, TTL_PRODUCTS_SEC)
        return data
    except Exception as e:
        return JSONResponse({"detail": "Upstream error", "error": str(e)}, status_code=503)
    finally:
        await cache_unlock(lock_key)


@app.get("/public/product/info")
async def product_info(product_id: int, city_id: Optional[str] = None):
    key = _cache_key("product", "info", product_id, city_id)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    async def run(api):
        r = await api.Catalog.Product.info(product_id=product_id, city_id=city_id)
        return r.json()

    try:
        data = await _call_chizhik(run)
        await cache_set_json(key, data, TTL_PRODUCT_INFO_SEC)
        return data
    except Exception as e:
        return JSONResponse({"detail": "Upstream error", "error": str(e)}, status_code=503)


# -------- PRIVATE API --------

@app.get("/private/ping")
async def private_ping():
    return {"ok": True, "private": True}
