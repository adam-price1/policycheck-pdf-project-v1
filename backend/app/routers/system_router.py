"""System management API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models import User
from app.services import document_service
from app.auth import get_current_user

router = APIRouter(prefix="/api/system", tags=["system"])


class ResetResponse(BaseModel):
    """System reset response."""
    status: str
    crawl_sessions_deleted: int
    documents_deleted: int
    storage_items_deleted: int
    storage_directories_deleted: int
    message: str


@router.delete("/reset", response_model=ResetResponse)
def reset_system(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Reset the entire system.
    
    WARNING: This will:
    - Delete all crawl sessions
    - Delete all documents (DB records)
    - Delete all downloaded PDF files
    - Recreate empty storage structure
    
    Only admins can perform this operation.
    """
    # Check if user is admin
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can reset the system"
        )
    
    # Perform reset
    result = document_service.reset_system(db)
    
    return ResetResponse(
        status=result["status"],
        crawl_sessions_deleted=result["crawl_sessions_deleted"],
        documents_deleted=result["documents_deleted"],
        storage_items_deleted=result["storage_items_deleted"],
        storage_directories_deleted=result["storage_items_deleted"],
        message=f"System reset completed. Deleted {result['crawl_sessions_deleted']} crawl sessions, "
                f"{result['documents_deleted']} documents, and {result['storage_items_deleted']} storage items."
    )


@router.get("/health")
def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy", "service": "PolicyCheck v6"}
