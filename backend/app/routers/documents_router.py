"""
Production-hardened document management API endpoints.

CRITICAL IMPROVEMENTS FROM V5:
1. Streaming ZIP downloads (no disk I/O)
2. Pagination support for large datasets
3. Better error handling
4. Short-lived download tokens (future enhancement)
5. Structured logging
"""
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services import document_service
from app.auth import get_current_user, get_current_user_optional

router = APIRouter(prefix="/api/documents", tags=["documents"])
logger = logging.getLogger(__name__)

# ============================================================================
# SCHEMAS
# ============================================================================

class DocumentResponse(BaseModel):
    """Document metadata response."""
    id: int
    source_url: str
    insurer: str
    local_file_path: str
    file_size: int = None
    country: str
    policy_type: str
    document_type: str
    classification: str
    confidence: float
    status: str
    created_at: str
    
    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    """Paginated document list response."""
    documents: List[DocumentResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class DocumentStatsResponse(BaseModel):
    """Document statistics."""
    total_documents: int
    total_crawl_sessions: int
    completed_sessions: int
    running_sessions: int
    failed_sessions: int
    total_storage_bytes: int
    total_storage_mb: float


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get document metadata by ID.
    
    Returns:
        DocumentResponse with metadata
    
    Raises:
        404 if document not found
    """
    doc = document_service.get_document_by_id(db, document_id)
    
    if not doc:
        logger.warning(
            f"Document {document_id} requested by {current_user.username} not found"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found"
        )
    
    return DocumentResponse(
        id=doc.id,
        source_url=doc.source_url,
        insurer=doc.insurer,
        local_file_path=doc.local_file_path,
        file_size=doc.file_size,
        country=doc.country,
        policy_type=doc.policy_type,
        document_type=doc.document_type,
        classification=doc.classification,
        confidence=doc.confidence,
        status=doc.status,
        created_at=doc.created_at.isoformat()
    )


@router.get("", response_model=DocumentListResponse)
def list_documents(
    crawl_session_id: Optional[int] = Query(None, description="Filter by crawl session ID"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum documents to return"),
    offset: int = Query(0, ge=0, description="Number of documents to skip"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List documents with pagination support.
    
    IMPROVEMENT: Added pagination to handle large datasets efficiently.
    
    Args:
        crawl_session_id: Optional filter by crawl session
        limit: Max results per page (1-1000)
        offset: Skip this many results
    
    Returns:
        DocumentListResponse with documents and pagination metadata
    """
    # Get total count (for pagination metadata)
    total = document_service.get_document_count(db, crawl_session_id)
    
    # Get paginated documents
    docs = document_service.get_all_documents(
        db,
        crawl_session_id=crawl_session_id,
        limit=limit,
        offset=offset
    )
    
    # Build response
    document_responses = [
        DocumentResponse(
            id=doc.id,
            source_url=doc.source_url,
            insurer=doc.insurer,
            local_file_path=doc.local_file_path,
            file_size=doc.file_size,
            country=doc.country,
            policy_type=doc.policy_type,
            document_type=doc.document_type,
            classification=doc.classification,
            confidence=doc.confidence,
            status=doc.status,
            created_at=doc.created_at.isoformat()
        )
        for doc in docs
    ]
    
    return DocumentListResponse(
        documents=document_responses,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(docs)) < total
    )


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    token: Optional[str] = Query(None, description="Optional download token (query param auth)"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Download a single document file.
    
    Supports both header and query param authentication.
    Query param auth allows direct browser downloads but has security implications.
    
    NOTE: Query param tokens should be short-lived (future enhancement).
    
    Returns:
        FileResponse with PDF file
    
    Raises:
        401 if not authenticated
        404 if document or file not found
    """
    # If no user from header, try token from query param
    if not current_user and token:
        from app.auth import decode_token
        from app.models import User as UserModel
        try:
            payload = decode_token(token)
            username = payload.get("sub")
            if username:
                current_user = db.query(UserModel).filter(
                    UserModel.username == username
                ).first()
        except Exception as e:
            logger.warning(f"Invalid download token: {e}")
    
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    doc = document_service.get_document_by_id(db, document_id)
    
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found"
        )
    
    file_path = document_service.get_document_file_path(doc)
    
    if not file_path:
        logger.error(
            f"Document {document_id} file missing: {doc.local_file_path}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found on disk"
        )
    
    # Generate safe filename
    filename = f"{doc.insurer}_{doc.policy_type}_{file_path.name}"
    
    logger.info(
        f"User {current_user.username} downloading document {document_id}: {filename}"
    )
    
    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=filename
    )


@router.get("/download-all/zip")
def download_all_documents(
    crawl_session_id: Optional[int] = Query(None, description="Filter by crawl session ID"),
    token: Optional[str] = Query(None, description="Optional download token"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Download all documents as a streaming ZIP file.
    
    CRITICAL IMPROVEMENT: Uses streaming response instead of writing ZIP to disk.
    
    Features:
    - Generates ZIP in memory
    - Streams to client
    - No disk I/O
    - Handles large datasets efficiently
    
    Supports both header and query param authentication.
    Optionally filter by crawl_session_id.
    
    Returns:
        StreamingResponse with ZIP file
    
    Raises:
        401 if not authenticated
        404 if no documents found
    """
    # If no user from header, try token from query param
    if not current_user and token:
        from app.auth import decode_token
        from app.models import User as UserModel
        try:
            payload = decode_token(token)
            username = payload.get("sub")
            if username:
                current_user = db.query(UserModel).filter(
                    UserModel.username == username
                ).first()
        except Exception as e:
            logger.warning(f"Invalid download token for ZIP: {e}")
    
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    logger.info(
        f"User {current_user.username} downloading ZIP "
        f"(crawl_session_id={crawl_session_id})"
    )
    
    # Create streaming ZIP generator
    zip_stream = document_service.create_download_zip_stream(db, crawl_session_id)
    
    if not zip_stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No documents found to download"
        )
    
    # Generate filename
    zip_filename = f"policycheck_documents_{crawl_session_id or 'all'}.zip"
    
    # CRITICAL: Return StreamingResponse instead of FileResponse
    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"'
        }
    )


@router.get("/stats/summary", response_model=DocumentStatsResponse)
def get_document_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get document and crawl statistics.
    
    Returns:
        DocumentStatsResponse with system statistics
    """
    stats = document_service.get_document_stats(db)
    
    return DocumentStatsResponse(**stats)
