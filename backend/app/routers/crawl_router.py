"""Production-hardened crawl management API endpoints."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Document
from app.services import crawl_service
from app.auth import get_current_user

router = APIRouter(prefix="/api/crawl", tags=["crawl"])
logger = logging.getLogger(__name__)


# ============================================================================
# SCHEMAS
# ============================================================================

class CrawlConfigRequest(BaseModel):
    country: str = Field(..., min_length=2, max_length=10)
    max_pages: int = Field(default=1000, ge=1, le=10000)
    max_time: int = Field(default=60, ge=1, le=180, alias="max_minutes")
    seed_urls: List[str] = Field(..., min_length=1)
    policy_types: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list, alias="keyword_filters")

    class Config:
        populate_by_name = True

    @validator("seed_urls")
    def validate_seed_urls(cls, v):
        for url in v:
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"Invalid URL: {url}")
        return v

    @validator("keywords", pre=True)
    def validate_keywords(cls, v):
        if v is None:
            return []
        return [k.strip() for k in v if k and k.strip()]


class CrawlResponse(BaseModel):
    crawl_id: int
    status: str
    message: str
    active_crawls: int
    max_concurrent_crawls: int


class CrawlStatusResponse(BaseModel):
    id: int
    status: str
    country: str
    progress_pct: int
    pages_scanned: int
    pdfs_found: int
    pdfs_downloaded: int
    pdfs_filtered: int
    errors_count: int
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    class Config:
        from_attributes = True


# ============================================================================
# HELPER
# ============================================================================

def _start_crawl_logic(
    config: CrawlConfigRequest,
    background_tasks: BackgroundTasks,
    db: Session,
    current_user: User,
) -> CrawlResponse:
    """Shared logic for starting a crawl."""
    logger.info(
        f"Crawl start by {current_user.username}: country={config.country}, "
        f"seeds={len(config.seed_urls)}, max_pages={config.max_pages}"
    )

    can_start, reason = crawl_service.can_start_crawl()
    if not can_start:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Maximum concurrent crawls reached",
                "reason": reason,
                "active_crawls": crawl_service.get_active_crawl_count(),
            },
        )

    try:
        session = crawl_service.create_crawl_session(
            db=db,
            user=current_user,
            country=config.country,
            max_pages=config.max_pages,
            max_minutes=config.max_time,
            seed_urls=config.seed_urls,
            policy_types=config.policy_types,
            keyword_filters=config.keywords,
        )
        background_tasks.add_task(crawl_service.run_crawl_session, session.id)
        active = crawl_service.get_active_crawl_count()

        return CrawlResponse(
            crawl_id=session.id,
            status=session.status,
            message=f"Crawl session {session.id} started",
            active_crawls=active,
            max_concurrent_crawls=crawl_service.MAX_CONCURRENT_CRAWLS,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting crawl: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start crawl")


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("", response_model=CrawlResponse)
def start_crawl_root(
    config: CrawlConfigRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a new crawl session (POST /api/crawl)."""
    return _start_crawl_logic(config, background_tasks, db, current_user)


@router.post("/start", response_model=CrawlResponse)
def start_crawl(
    config: CrawlConfigRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a new crawl session (POST /api/crawl/start)."""
    return _start_crawl_logic(config, background_tasks, db, current_user)


@router.get("/{crawl_id}/status", response_model=CrawlStatusResponse)
def get_crawl_status(
    crawl_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get crawl session status."""
    session = crawl_service.get_crawl_status(db, crawl_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Crawl {crawl_id} not found")
    return CrawlStatusResponse(
        id=session.id,
        status=session.status,
        country=session.country,
        progress_pct=session.progress_pct or 0,
        pages_scanned=session.pages_scanned or 0,
        pdfs_found=session.pdfs_found or 0,
        pdfs_downloaded=session.pdfs_downloaded or 0,
        pdfs_filtered=session.pdfs_filtered or 0,
        errors_count=session.errors_count or 0,
        started_at=session.started_at.isoformat() if session.started_at else None,
        completed_at=session.completed_at.isoformat() if session.completed_at else None,
    )


@router.get("/{crawl_id}/results")
def get_crawl_results(
    crawl_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get documents produced by a crawl session."""
    session = crawl_service.get_crawl_status(db, crawl_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Crawl {crawl_id} not found")

    docs = db.query(Document).filter(Document.crawl_session_id == crawl_id).all()

    return {
        "crawl_id": crawl_id,
        "status": session.status,
        "total": len(docs),
        "documents": [
            {
                "id": d.id,
                "source_url": d.source_url,
                "insurer": d.insurer,
                "country": d.country,
                "policy_type": d.policy_type,
                "classification": d.classification,
                "confidence": d.confidence,
                "file_size": d.file_size,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ],
    }


@router.get("/sessions", response_model=List[CrawlStatusResponse])
def list_crawl_sessions(
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List crawl sessions for the current user."""
    from app.models import CrawlSession

    sessions = (
        db.query(CrawlSession)
        .filter(CrawlSession.user_id == current_user.id)
        .order_by(CrawlSession.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [
        CrawlStatusResponse(
            id=s.id,
            status=s.status,
            country=s.country,
            progress_pct=s.progress_pct or 0,
            pages_scanned=s.pages_scanned or 0,
            pdfs_found=s.pdfs_found or 0,
            pdfs_downloaded=s.pdfs_downloaded or 0,
            pdfs_filtered=s.pdfs_filtered or 0,
            errors_count=s.errors_count or 0,
            started_at=s.started_at.isoformat() if s.started_at else None,
            completed_at=s.completed_at.isoformat() if s.completed_at else None,
        )
        for s in sessions
    ]


@router.get("/active/count")
def get_active_count(current_user: User = Depends(get_current_user)):
    """Get number of currently active crawls."""
    active = crawl_service.get_active_crawl_count()
    mx = crawl_service.MAX_CONCURRENT_CRAWLS
    return {
        "active_crawls": active,
        "max_concurrent_crawls": mx,
        "available_slots": mx - active,
        "at_capacity": active >= mx,
    }
