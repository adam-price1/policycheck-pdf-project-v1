"""PolicyCheck v6 - Production-Hardened Ingestion Platform."""
import logging
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import (
    CORS_ORIGINS,
    RAW_STORAGE_DIR,
    LOG_LEVEL,
    LOG_FORMAT,
    IS_PRODUCTION,
    validate_configuration,
)
from app.database import (
    init_db,
    check_database_health,
    dispose_engine,
    get_pool_status,
)
from app.services import crawl_service
from app.routers import (
    auth_router,
    crawl_router,
    documents_router,
    system_router,
    stats_router,
    audit_router,
)

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

if IS_PRODUCTION:
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# ============================================================================
# GRACEFUL SHUTDOWN
# ============================================================================

shutdown_event = False


def signal_handler(signum, frame):
    global shutdown_event
    logger.warning(f"Received {signal.Signals(signum).name}, shutting down...")
    shutdown_event = True


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================================================
# LIFESPAN
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("PolicyCheck v6 starting")
    logger.info("=" * 60)

    validate_configuration()
    init_db()

    if not check_database_health():
        raise RuntimeError("Database health check failed after init")

    logger.info("Startup complete")
    yield

    logger.info("Shutting down...")
    dispose_engine()
    logger.info("Shutdown complete")


# ============================================================================
# APP
# ============================================================================

app = FastAPI(
    title="PolicyCheck v6",
    description="Policy document ingestion platform",
    version="6.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ============================================================================
# CORS — allow all origins that are configured
# ============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ============================================================================
# ROUTERS
# ============================================================================

app.include_router(auth_router.router)
app.include_router(crawl_router.router)
app.include_router(documents_router.router)
app.include_router(system_router.router)
app.include_router(stats_router.router)
app.include_router(audit_router.router)

# ============================================================================
# ROOT ENDPOINTS
# ============================================================================


@app.get("/")
def root():
    return {
        "service": "PolicyCheck v6",
        "version": "6.0.0",
        "status": "operational",
    }


@app.get("/health")
def health():
    if shutdown_event:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "shutting_down"},
        )
    try:
        db_healthy = check_database_health()
        pool_stats = get_pool_status()
        active_crawls = crawl_service.get_active_crawl_count()
        health_data = {
            "status": "healthy" if db_healthy else "degraded",
            "version": "6.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": {
                "connected": db_healthy,
                "pool": pool_stats,
            },
            "services": {
                "crawl": {
                    "active": active_crawls,
                    "capacity": crawl_service.MAX_CONCURRENT_CRAWLS,
                }
            },
            "storage": {
                "available": RAW_STORAGE_DIR.exists(),
                "path": str(RAW_STORAGE_DIR),
            },
        }

        if db_healthy:
            return health_data

        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=health_data,
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


@app.get("/ready")
def readiness():
    if shutdown_event:
        return JSONResponse(status_code=503, content={"ready": False})
    return {"ready": True}


# ============================================================================
# DEV SERVER
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
