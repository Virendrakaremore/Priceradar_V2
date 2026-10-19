# PriceRadar Backend — Setup

Python 3.12 recommended. Two processes: **web** (Flask API) and **worker** (monitoring). The browser never scrapes.

## Local
```bash
cd Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium            # only needed for JS-rendered pages
cp .env.example .env                   # fill in SMTP_* (placeholders only in the example)
alembic upgrade head                   # create/upgrade the schema
python -m flask --app wsgi run -p 5000 # terminal 1: API   (or: gunicorn wsgi:app)
python -m priceradar.worker            # terminal 2: monitor
python -m pytest                       # 74 backend tests
```
Frontend: `cd Frontend && cp .env.example .env && npm install && npm start` (tests: `CI=true npm test -- --watchAll=false`, build: `npm run build`).

## Configuration (env vars, see `.env.example`)
| Var | Purpose |
|---|---|
| `DATABASE_URL` | `sqlite:///priceradar.db` (dev) or `postgresql://…` (production) |
| `CORS_ORIGINS` | comma-separated frontend origins |
| `API_TOKEN` | optional bearer token for the API (do NOT embed in the frontend; use a reverse proxy if you need it) |
| `SMTP_HOST/PORT/USERNAME/PASSWORD/USE_TLS/USE_SSL`, `EMAIL_FROM` | mail; Gmail = 587 + STARTTLS + App Password. Legacy `EMAIL_USER`/`EMAIL_PASS` still work |
| `MONITOR_INTERVAL_SECONDS` | requested interval (default 15) |
| `RETAILER_MIN_INTERVAL_SECONDS` | enforced floor per retailer (default 60). The *effective* interval is `max(requested, floor)` and is shown in the UI |
| `WORKER_CONCURRENCY`, `PER_RETAILER_CONCURRENCY`, `FETCH_TIMEOUT_SECONDS`, `RESPECT_ROBOTS`, `USE_PLAYWRIGHT` | worker tuning |

## Architecture
- `priceradar/retailers/` one adapter per retailer (URL rules, parsing). JSON-LD/OpenGraph first, CSS selectors as fallback.
- `fetcher.py` HTTP first, Chromium only if no price found. robots.txt respected; no stealth, no proxy rotation, no CAPTCHA bypass — blocks are reported.
- `security.py` SSRF protection: allow-listed retailer hosts only, per-hop redirect validation, public-IP check, no IP literals/odd ports.
- `matching.py` conservative matching: GTIN or model number required for "verified"; storage/RAM/size/bundle conflicts reject; colour differences downgrade to "possible".
- `ingest.py` one place that writes observations. Failures never overwrite the last price; implausible jumps (≥40%) need two identical readings; unchanged prices are not re-stored (15-min heartbeat).
- `monitor.py` DB-locked claims (no overlapping jobs), per-retailer concurrency, exponential backoff, circuit breaker, graceful shutdown. Scale: a single worker suits tens–low hundreds of offers at ≥30–60 s; beyond that run more workers (locks are in the DB) — Redis/Celery only becomes necessary at much larger scale.
- `alerts.py` + `notifier.py` rule evaluation with arm/re-arm, cooldowns, unique `(rule, event_key)` dedupe; HTML+text mail; retries with backoff; permanent errors marked dead.
- `analytics.py` time-weighted stats, classification, target suggestions.

## API (v1, JSON; errors are `{success:false,error:{code,message}}`)
`POST /api/v1/products/identify` → job; `GET /jobs/<id>`; `GET /products`, `GET|PATCH|DELETE /products/<id>`; `POST /products/<id>/offers|refresh`; `GET /products/<id>/history|insights|target-suggestions`; `GET|POST /alerts`, `PATCH|DELETE /alerts/<id>`; `GET /notifications`; `GET|PUT /settings`; `POST /settings/test-email`; `GET /dashboard|deals|retailers|monitoring/status|health`.
Legacy routes kept: `/api/health`, `/api/expand`, `/api/product`, `/api/price` (now returns the **stored** verified price instead of scraping per request; `/api/set-alert`, `/api/delete-alert`, `/api/alerts` are replaced by `/api/v1/alerts`). Removed: `/api/debug*` (unauthenticated, wrote files to disk).

## Deployment (not yet performed — untested on any host)
Backend web + worker (Render/Railway/Fly/VPS): use `Procfile` or `Dockerfile` (Playwright base image includes Chromium). Use managed PostgreSQL; run `alembic upgrade head` on release. Frontend: static host (Vercel/Netlify) with `REACT_APP_API_BASE_URL` set at build time and the host added to `CORS_ORIGINS`. Back up with `pg_dump` (daily) — price history is the irreplaceable data. Costs: one small web + one worker instance + Postgres; no paid third-party API is required.

## Troubleshooting
- *Monitoring page says worker down*: start `python -m priceradar.worker` against the same `DATABASE_URL`.
- *Offer shows "Blocked"*: the retailer served a challenge/403; it is retried with backoff. There is no bypass by design.
- *No other retailers found*: search pages are often disallowed by robots.txt or JS-gated; add the other store's link manually on the product page.
- *Test email fails*: check SMTP_* values; Gmail needs an App Password; port 465 needs `SMTP_USE_SSL=true`, `SMTP_USE_TLS=false`.
