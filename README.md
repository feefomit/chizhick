# Chizhik backend (FastAPI)

## Endpoints
- GET /health
- GET /public/geo/cities?search=...&page=1
- GET /public/offers/active
- GET /public/catalog/tree?city_id=<UUID>
- GET /public/catalog/products?city_id=<UUID>&category_id=<id>&page=1
- GET /public/product/info?product_id=<id>&city_id=<UUID>

## Env variables (Timeweb App Platform)
- API_KEY (optional) — защитит НЕ public ручки (private)
- ALLOWED_ORIGINS — домены фронта, через запятую
  пример: https://chizhick.ru,https://www.chizhick.ru
- REDIS_URL — подключение к Redis
  пример: redis://<user>:<password>@<host>:6379/0
- CHIZHIK_PROXY (optional) — прокси
- CHIZHIK_HEADLESS (optional) — true/false

## Важно про порты экспортеров
node_exporter (9100) и redis_exporter (9308) — это метрики мониторинга, НЕ порт Redis.
Redis обычно 6379 (или порт, который указан в настройках Redis).
