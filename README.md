# PolicyCheck v6 

## Quick Start

```bash
cd policycheck-fixed

# 2. Start everything with Docker Compose
docker-compose up --build

# 3. Open in browser
#    Frontend:  http://localhost
#    API Docs:  http://localhost/docs
#    Backend:   http://localhost:8000
```

## Creating the Admin User

On first run the database is empty. Register via the UI or create a user via the API:

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123","name":"Admin User","role":"admin"}'
```

Then log in at **http://localhost/login** with `admin / admin123`.

## What Was Fixed

### Backend
- **Added** `/api/stats/pipeline` and `/api/stats/dashboard` endpoints (were 404)
- **Added** `/api/audit-log` endpoint (was 404)
- **Added** `POST /api/crawl` + `GET /api/crawl/{id}/results` endpoints
- **Fixed** CORS — all localhost origins allowed, plus nginx proxy eliminates cross-origin issues
- **Fixed** `requirements.txt` — pinned `bcrypt==4.0.1` to prevent build failures
- **Fixed** `main.py` — mounts all 6 routers, `/docs` always enabled
- **Fixed** `.env` — credentials now match `docker-compose.yml` defaults

### Frontend
- **Fixed** API client — uses **relative URLs** (`""` baseURL) so nginx proxies all `/api/` calls; no CORS issues
- **Fixed** TypeScript types — `CrawlStatusResponse` matches backend (`id` not `crawl_id`, `errors_count` not `errors`, no `activity_log`)
- **Fixed** Dashboard — safe optional chaining on `stats.stages`; shows "No data yet" instead of crashing
- **Fixed** CrawlPage — correct field references, no crash on undefined `.length`
- **Fixed** Progress page — correct field names
- **Fixed** All pages — safe `?.` and `?? []` fallbacks on API responses
- **Fixed** Login — redirects to `/dashboard` (was going to missing `/setup`)
- **Fixed** App.tsx — all routes registered including `/register`, `/setup`, `/funnel`
- **Fixed** Layout nav — includes all page links

### Docker
- **Fixed** `.env` — `MYSQL_USER=policycheck` matches compose default
- **Fixed** `SECRET_KEY` — 32+ chars to pass validation
- **Fixed** `docker-compose.yml` — healthcheck uses matching credentials
- **Fixed** `nginx.conf` — proxies `/api/`, `/health`, `/docs`, `/redoc`

## Architecture

```
Browser → :80 Nginx → serves React SPA
                    → proxies /api/* → :8000 FastAPI → MySQL :3306
```

## API Endpoints

| Method | Path                        | Auth | Description              |
|--------|-----------------------------|------|--------------------------|
| GET    | /health                     | No   | Health check             |
| POST   | /api/auth/login             | No   | Login → JWT              |
| POST   | /api/auth/register          | No   | Register user            |
| GET    | /api/auth/me                | Yes  | Current user info        |
| GET    | /api/stats/pipeline         | Yes  | Pipeline funnel stats    |
| GET    | /api/stats/dashboard        | Yes  | Dashboard summary        |
| GET    | /api/audit-log              | Yes  | Audit log entries        |
| POST   | /api/crawl                  | Yes  | Start crawl              |
| POST   | /api/crawl/start            | Yes  | Start crawl (alias)      |
| GET    | /api/crawl/{id}/status      | Yes  | Crawl progress           |
| GET    | /api/crawl/{id}/results     | Yes  | Crawl documents          |
| GET    | /api/documents              | Yes  | List documents           |
| GET    | /api/documents/{id}         | Yes  | Document detail          |

## Stopping

```bash
docker-compose down          # stop containers
docker-compose down -v       # stop + delete database volume
```
