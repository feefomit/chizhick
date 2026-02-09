import os
from fastapi import FastAPI, Query, Header, HTTPException

app = FastAPI(title="Chizhik API wrapper")

PROXY = os.getenv("CHIZHIK_PROXY")
HEADLESS = os.getenv("CHIZHIK_HEADLESS", "true").lower() == "true"
API_KEY = os.getenv("API_KEY")

def check_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.get("/")
async def root():
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/offers/active")
async def active_offers(x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    from chizhik_api import ChizhikAPI  # ленивый импорт (ускоряет старт)
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Advertising.active_inout()
        return r.json()

@app.get("/geo/cities")
async def cities(search: str = Query(...), page: int = 1, x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    from chizhik_api import ChizhikAPI
    async with ChizhikAPI(proxy=PROXY, headless=HEADLESS) as api:
        r = await api.Geolocation.cities_list(search_name=search, page=page)
        return r.json()
