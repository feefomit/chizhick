from fastapi import FastAPI
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}


import os
from fastapi import FastAPI, Query, Header, HTTPException
from chizhik_api import ChizhikAPI

app = FastAPI(title="Chizhik API wrapper")

PROXY = os.getenv("CHIZHIK_PROXY")  # опционально
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"
API_KEY = os.getenv("API_KEY")  # задай в панели Timeweb (рекомендую)

def check_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/offers/active")
async def offers_active(x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()

@app.get("/geo/cities")
async def geo_cities(search: str = Query(...), page: int = 1, x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()

@app.get("/catalog/tree")
async def catalog_tree(x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.tree()
        return r.json()

@app.get("/catalog/products")
async def catalog_products(category_id: int, page: int = 1, x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Catalog.products_list(category_id=category_id, page=page)
        return r.json()
