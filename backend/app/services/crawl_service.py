"""
Production-hardened crawl service - Ingestion engine.

CRITICAL IMPROVEMENTS FROM V5:
1. Thread-safe robots.txt cache with locking
2. Concurrent crawl limit enforcement  
3. Better error handling with specific exceptions
4. Structured logging with crawl_id context
5. Improved duplicate detection before download
6. Memory-efficient operations with bounds checking
7. Graceful handling of time limits
8. Atomic file operations with better cleanup
9. Connection pool reuse across requests
10. BeautifulSoup parser explicitly specified

HANDLES:
- Crawl session creation with validation
- Real PDF discovery and download
- Keyword and policy type filtering
- File storage with deduplication
- Progress tracking
- Resource limits and safety
"""
import os
import time
import hashlib
import logging
import tempfile
import threading
import urllib.robotparser
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin, urlunparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from urllib3.util.retry import Retry

from app.models import CrawlSession, Document, User
from app.config import (
    RAW_STORAGE_DIR, REQUEST_DELAY, CRAWL_TIMEOUT,
    USER_AGENT, TRACKING_PARAMS, MAX_FILE_SIZE_BYTES,
    CHUNK_SIZE, MAX_DOWNLOAD_TIME, CRAWL_MAX_RETRIES,
    MAX_PAGES_ABSOLUTE, MAX_MINUTES_ABSOLUTE,
    MAX_CONCURRENT_CRAWLS
)

logger = logging.getLogger(__name__)

# ============================================================================
# THREAD-SAFE GLOBAL STATE
# ============================================================================

# Robots.txt cache with thread safety
_ROBOTS_CACHE: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
_ROBOTS_CACHE_LOCK = threading.Lock()

# Active crawl tracking for concurrency limits
_ACTIVE_CRAWLS: Dict[int, datetime] = {}  # crawl_id -> start_time
_ACTIVE_CRAWLS_LOCK = threading.Lock()


# ============================================================================
# CONCURRENCY MANAGEMENT (CRITICAL FIX #1)
# ============================================================================

def can_start_crawl() -> Tuple[bool, Optional[str]]:
    """
    Check if a new crawl can be started based on concurrency limits.
    
    Returns:
        (can_start, reason_if_not)
    
    CRITICAL FIX: Enforces MAX_CONCURRENT_CRAWLS to prevent resource exhaustion.
    """
    with _ACTIVE_CRAWLS_LOCK:
        active_count = len(_ACTIVE_CRAWLS)
        
        if active_count >= MAX_CONCURRENT_CRAWLS:
            oldest_crawl_id = min(_ACTIVE_CRAWLS.keys(), key=lambda k: _ACTIVE_CRAWLS[k])
            return False, (
                f"Maximum concurrent crawls ({MAX_CONCURRENT_CRAWLS}) reached. "
                f"Oldest active crawl: #{oldest_crawl_id}"
            )
        
        return True, None


def register_active_crawl(crawl_id: int) -> None:
    """Register a crawl as active."""
    with _ACTIVE_CRAWLS_LOCK:
        _ACTIVE_CRAWLS[crawl_id] = datetime.now(timezone.utc)
        logger.info(
            f"Registered active crawl {crawl_id} "
            f"({len(_ACTIVE_CRAWLS)}/{MAX_CONCURRENT_CRAWLS} slots used)"
        )


def unregister_active_crawl(crawl_id: int) -> None:
    """Unregister a crawl as active."""
    with _ACTIVE_CRAWLS_LOCK:
        if crawl_id in _ACTIVE_CRAWLS:
            del _ACTIVE_CRAWLS[crawl_id]
            logger.info(
                f"Unregistered active crawl {crawl_id} "
                f"({len(_ACTIVE_CRAWLS)}/{MAX_CONCURRENT_CRAWLS} slots used)"
            )


def get_active_crawl_count() -> int:
    """Get number of currently active crawls."""
    with _ACTIVE_CRAWLS_LOCK:
        return len(_ACTIVE_CRAWLS)


# ============================================================================
# HTTP SESSION FACTORY
# ============================================================================

def get_session_with_retries() -> requests.Session:
    """
    Create requests session with connection pooling and retry logic.
    
    OPTIMIZATION: Reuses connections, implements exponential backoff.
    """
    session = requests.Session()
    
    # Retry configuration with exponential backoff
    retry_strategy = Retry(
        total=CRAWL_MAX_RETRIES,
        backoff_factor=1,  # 1s, 2s, 4s delays
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False  # Let us handle status codes
    )
    
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=20,
        pool_block=False  # Don't block waiting for connection
    )
    
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({'User-Agent': USER_AGENT})
    
    return session


# ============================================================================
# ROBOTS.TXT HANDLING (THREAD-SAFE FIX #2)
# ============================================================================

def can_fetch(url: str, session: requests.Session) -> bool:
    """
    Check if URL can be fetched according to robots.txt.
    
    IMPROVEMENTS:
    - Thread-safe cache access with lock
    - Better error handling
    - Fail-open on robots.txt errors
    
    SECURITY: Respects robots.txt to avoid legal issues.
    """
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        
        # Thread-safe cache check
        with _ROBOTS_CACHE_LOCK:
            if robots_url in _ROBOTS_CACHE:
                rp = _ROBOTS_CACHE[robots_url]
                if rp is None:
                    return True
                return rp.can_fetch(USER_AGENT, url)
        
        # Cache miss - fetch robots.txt
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        
        try:
            rp.read()
            logger.debug(f"Loaded robots.txt from {robots_url}")
            
            # Cache the parser
            with _ROBOTS_CACHE_LOCK:
                _ROBOTS_CACHE[robots_url] = rp
            
            return rp.can_fetch(USER_AGENT, url)
        except Exception as e:
            logger.debug(f"No robots.txt at {robots_url}: {e}")
            
            # Cache None to indicate no robots.txt
            with _ROBOTS_CACHE_LOCK:
                _ROBOTS_CACHE[robots_url] = None
            
            return True  # Allow crawling if robots.txt unavailable
    
    except Exception as e:
        logger.warning(f"Error checking robots.txt for {url}: {e}")
        return True  # Fail-open on unexpected errors


# ============================================================================
# CRAWL SESSION CREATION
# ============================================================================

def create_crawl_session(
    db: Session,
    user: User,
    country: str,
    max_pages: int,
    max_minutes: int,
    seed_urls: List[str],
    policy_types: List[str],
    keyword_filters: List[str]
) -> CrawlSession:
    """
    Create a new crawl session with validation.
    
    IMPROVEMENTS:
    - Enforces hard limits on max_pages and max_minutes
    - Validates inputs
    - Better logging
    """
    # Enforce hard limits
    max_pages = min(max_pages, MAX_PAGES_ABSOLUTE)
    max_minutes = min(max_minutes, MAX_MINUTES_ABSOLUTE)
    
    # Validate inputs
    if not seed_urls:
        raise ValueError("At least one seed URL is required")
    
    if not keyword_filters:
        logger.warning("No keyword filters specified - will accept all PDFs")
    
    session = CrawlSession(
        user_id=user.id,
        country=country,
        max_pages=max_pages,
        max_minutes=max_minutes,
        seed_urls=seed_urls,
        policy_types=policy_types,
        keyword_filters=keyword_filters,
        status="queued",
        created_at=datetime.now(timezone.utc)
    )
    
    db.add(session)
    db.commit()
    db.refresh(session)
    
    logger.info(
        f"Created crawl session {session.id} for user {user.username} "
        f"(country={country}, max_pages={max_pages}, max_minutes={max_minutes}, "
        f"seeds={len(seed_urls)}, filters={len(keyword_filters)})"
    )
    
    return session


# ============================================================================
# DOCUMENT VALIDATION
# ============================================================================

def is_valid_document(
    url: str,
    keyword_filters: List[str],
    policy_types: List[str]
) -> Tuple[bool, Optional[str]]:
    """
    Check if URL matches keyword and policy type filters.
    
    Returns:
        (is_valid, matched_policy_type)
    
    IMPROVEMENTS:
    - More robust keyword matching
    - Better logging of filter decisions
    """
    url_lower = url.lower()
    
    # Check keyword filters (required if specified)
    if keyword_filters:
        keyword_match = any(keyword.lower() in url_lower for keyword in keyword_filters)
        if not keyword_match:
            logger.debug(f"Filtered out (no keyword match): {url}")
            return False, None
    
    # Check policy type filters (optional - if empty, accept all)
    if not policy_types:
        logger.debug(f"Accepted (keyword match, no policy filter): {url}")
        return True, "General"
    
    # Policy type mapping - extended coverage
    policy_type_map = {
        "life": ["life", "lif", "living", "death", "tpd", "income protection", "trauma"],
        "home": ["home", "house", "property", "contents", "building", "landlord", "rental"],
        "motor": ["motor", "vehicle", "car", "auto", "comprehensive", "third party", "tpft"],
        "travel": ["travel", "trip", "overseas", "holiday", "international"],
        "health": ["health", "medical", "hospital", "dental", "optical"],
        "business": ["business", "commercial", "liability", "sme", "professional indemnity", "public liability"]
    }
    
    for policy_type in policy_types:
        policy_keywords = policy_type_map.get(policy_type.lower(), [policy_type.lower()])
        if any(pk in url_lower for pk in policy_keywords):
            logger.debug(f"Accepted ({policy_type}): {url}")
            return True, policy_type
    
    logger.debug(f"Filtered out (no policy type match): {url}")
    return False, None

# ============================================================================
# URL NORMALIZATION & VALIDATION
# ============================================================================

def normalize_url(url: str) -> str:
    """
    Normalize URL for deduplication.
    
    OPTIMIZATION: Remove tracking params, fragments, trailing slashes.
    """
    parsed = urlparse(url)
    
    # Normalize path
    path = parsed.path.rstrip('/') or '/'
    
    # Filter out tracking parameters
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        params = {
            k: v for k, v in params.items()
            if k.lower() not in TRACKING_PARAMS
        }
        query = urlencode(params, doseq=True) if params else ''
    else:
        query = ''
    
    # Normalize scheme and netloc
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    
    return urlunparse((scheme, netloc, path, parsed.params, query, ''))


def is_pdf_url(url: str) -> bool:
    """Check if URL points to a PDF."""
    path = urlparse(url).path.lower()
    return path.endswith('.pdf') or path.endswith('.pdf/')


def same_domain(seed_url: str, candidate_url: str) -> bool:
    """
    Check if URLs are on same domain (allowing subdomains).
    
    IMPROVEMENT: Handles www. and subdomain variations properly.
    """
    def get_base_domain(netloc: str) -> str:
        """Extract base domain from netloc."""
        netloc = netloc.lower().removeprefix('www.')
        parts = netloc.split('.')
        if len(parts) >= 2:
            return '.'.join(parts[-2:])
        return netloc
    
    seed_domain = get_base_domain(urlparse(seed_url).netloc)
    candidate_domain = get_base_domain(urlparse(candidate_url).netloc)
    
    return seed_domain == candidate_domain


# ============================================================================
# FILE HANDLING UTILITIES
# ============================================================================

def sanitize_filename(name: str) -> str:
    """
    Sanitize filename to prevent path traversal attacks.
    
    SECURITY: Removes dangerous characters and prevents directory traversal.
    """
    # Remove path separators and dangerous sequences
    safe_name = name.replace('/', '_').replace('\\', '_').replace('..', '_')
    
    # Allow only alphanumeric, underscore, hyphen, period
    safe_name = ''.join(c for c in safe_name if c.isalnum() or c in ('_', '-', '.'))
    
    # Ensure not empty and doesn't start with dot
    safe_name = safe_name.lstrip('.') or "unknown"
    
    # Limit length to prevent filesystem issues
    if len(safe_name) > 200:
        safe_name = safe_name[:200]
    
    return safe_name


def extract_insurer_name(url: str) -> str:
    """Extract and sanitize insurer name from URL."""
    domain = urlparse(url).netloc
    domain = domain.replace('www.', '')
    parts = domain.split('.')
    raw_name = parts[0].title() if parts else "Unknown"
    return sanitize_filename(raw_name)


def verify_path_safety(file_path: Path, allowed_parent: Path) -> bool:
    """
    Verify that file_path is safely within allowed_parent directory.
    
    SECURITY FIX: Prevents path traversal via symlinks.
    
    Returns:
        True if path is safe, False otherwise
    """
    try:
        # Resolve to absolute path (follows symlinks)
        resolved_path = file_path.resolve()
        resolved_parent = allowed_parent.resolve()
        
        # Check if resolved path is under parent
        return str(resolved_path).startswith(str(resolved_parent))
    except Exception as e:
        logger.error(f"Error verifying path safety: {e}")
        return False


# ============================================================================
# PDF DOWNLOAD WITH STREAMING
# ============================================================================

def download_pdf_streaming(
    url: str,
    save_path: Path,
    session: requests.Session,
    crawl_id: int
) -> Optional[Dict[str, Any]]:
    """
    Download PDF with streaming and concurrent hashing.
    
    CRITICAL OPTIMIZATIONS:
    - Streams download (doesn't load entire file in memory)
    - Computes hash during download (not after)
    - Enforces file size limit
    - Uses atomic write (temp file then move)
    - Validates content type
    - Has timeout protection
    - Better error handling and logging
    
    Returns:
        Dictionary with file_size and file_hash, or None if failed
    """
    temp_path = None
    
    try:
        logger.debug(f"[Crawl {crawl_id}] Downloading PDF: {url}")
        
        # SECURITY: Verify save path is safe
        if not verify_path_safety(save_path, RAW_STORAGE_DIR):
            logger.error(
                f"[Crawl {crawl_id}] Path traversal attempt detected: {save_path}"
            )
            return None
        
        response = session.get(
            url,
            timeout=CRAWL_TIMEOUT,
            stream=True  # CRITICAL: Stream to avoid memory issues
        )
        
        if response.status_code != 200:
            logger.warning(
                f"[Crawl {crawl_id}] Failed to download {url}: "
                f"HTTP {response.status_code}"
            )
            return None
        
        # Validate content type
        content_type = response.headers.get('Content-Type', '').lower()
        if 'application/pdf' not in content_type and not url.lower().endswith('.pdf'):
            logger.warning(
                f"[Crawl {crawl_id}] URL {url} does not appear to be a PDF "
                f"(Content-Type: {content_type})"
            )
            return None
        
        # Check content length if provided
        content_length = response.headers.get('Content-Length')
        if content_length and int(content_length) > MAX_FILE_SIZE_BYTES:
            logger.warning(
                f"[Crawl {crawl_id}] PDF too large: {url} "
                f"({int(content_length) / 1024 / 1024:.2f}MB > "
                f"{MAX_FILE_SIZE_BYTES / 1024 / 1024}MB)"
            )
            return None
        
        # Ensure directory exists
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        # CRITICAL: Write to temp file first (atomic operation)
        with tempfile.NamedTemporaryFile(
            mode='wb',
            dir=save_path.parent,
            delete=False,
            suffix='.tmp'
        ) as temp_file:
            temp_path = Path(temp_file.name)
            
            # OPTIMIZATION: Stream download and compute hash simultaneously
            sha256_hash = hashlib.sha256()
            bytes_downloaded = 0
            start_time = time.time()
            
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    # Check size limit
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded > MAX_FILE_SIZE_BYTES:
                        logger.warning(
                            f"[Crawl {crawl_id}] PDF exceeded size limit during download: {url} "
                            f"({bytes_downloaded / 1024 / 1024:.2f}MB)"
                        )
                        temp_path.unlink()
                        return None
                    
                    # Check time limit
                    elapsed = time.time() - start_time
                    if elapsed > MAX_DOWNLOAD_TIME:
                        logger.warning(
                            f"[Crawl {crawl_id}] Download timeout for {url} "
                            f"({elapsed:.1f}s > {MAX_DOWNLOAD_TIME}s)"
                        )
                        temp_path.unlink()
                        return None
                    
                    temp_file.write(chunk)
                    sha256_hash.update(chunk)
            
            file_size = bytes_downloaded
            file_hash = sha256_hash.hexdigest()
        
        # ATOMIC: Move temp file to final location
        temp_path.replace(save_path)
        
        logger.info(
            f"[Crawl {crawl_id}] Downloaded PDF: {url} -> {save_path} "
            f"({file_size / 1024:.2f}KB, hash={file_hash[:8]}...)"
        )
        
        return {
            'file_size': file_size,
            'file_hash': file_hash
        }
    
    except requests.exceptions.Timeout:
        logger.error(f"[Crawl {crawl_id}] Timeout downloading {url}")
        if temp_path and temp_path.exists():
            temp_path.unlink()
        return None
    
    except requests.exceptions.RequestException as e:
        logger.error(f"[Crawl {crawl_id}] Request error downloading {url}: {e}")
        if temp_path and temp_path.exists():
            temp_path.unlink()
        return None
    
    except Exception as e:
        logger.error(
            f"[Crawl {crawl_id}] Unexpected error downloading {url}: {e}",
            exc_info=True
        )
        if temp_path and temp_path.exists():
            temp_path.unlink()
        return None


# ============================================================================
# DOMAIN CRAWLING ENGINE
# ============================================================================

def crawl_domain(
    seed_url: str,
    max_pages: int,
    keyword_filters: List[str],
    policy_types: List[str],
    session: requests.Session,
    crawl_id: int,
    time_limit: Optional[datetime] = None
) -> List[str]:
    """
    Crawl a domain to find PDF URLs with time and page limits.
    
    IMPROVEMENTS FROM V5:
    - Structured logging with crawl_id
    - Explicit HTML parser specified
    - Better error recovery
    - Memory bounds checking
    - Time limit enforcement
    
    Returns:
        List of valid PDF URLs that match filters
    """
    # Handle direct PDF URLs
    if is_pdf_url(seed_url):
        normalized = normalize_url(seed_url)
        is_valid, _ = is_valid_document(normalized, keyword_filters, policy_types)
        if is_valid:
            logger.info(f'[Crawl {crawl_id}] Direct PDF seed: {normalized}')
            return [normalized]
        return []
    
    domain = urlparse(seed_url).netloc
    logger.info(
        f'[Crawl {crawl_id}] Crawling domain: {domain} '
        f'(max_pages={max_pages}, time_limit={time_limit})'
    )
    
    pdf_urls: Set[str] = set()
    visited: Set[str] = set()
    queue: List[str] = [seed_url]
    pages_crawled = 0
    
    while queue and pages_crawled < max_pages:
        # CRITICAL: Check time limit
        if time_limit and datetime.now(timezone.utc) > time_limit:
            logger.warning(
                f"[Crawl {crawl_id}] Time limit reached "
                f"(pages_crawled={pages_crawled}, pdfs_found={len(pdf_urls)})"
            )
            break
        
        # MEMORY: Limit visited set size to prevent memory exhaustion
        if len(visited) > max_pages * 2:
            logger.warning(
                f"[Crawl {crawl_id}] Visited set too large ({len(visited)}), "
                f"stopping crawl to prevent memory exhaustion"
            )
            break
        
        url = queue.pop(0)
        
        if url in visited:
            continue
        
        # SECURITY: Check robots.txt
        if not can_fetch(url, session):
            logger.debug(f"[Crawl {crawl_id}] Blocked by robots.txt: {url}")
            visited.add(url)
            continue
        
        visited.add(url)
        
        # Stay on same domain
        if not same_domain(seed_url, url):
            logger.debug(f"[Crawl {crawl_id}] Skipping different domain: {url}")
            continue
        
        # Rate limiting
        if pages_crawled > 0:
            time.sleep(REQUEST_DELAY)
        
        pages_crawled += 1
        logger.debug(f'[Crawl {crawl_id}] Page {pages_crawled}/{max_pages}: {url}')
        
        try:
            resp = session.get(url, timeout=CRAWL_TIMEOUT)
            
            if resp.status_code != 200:
                logger.debug(
                    f'[Crawl {crawl_id}] HTTP {resp.status_code}: {url}'
                )
                continue
            
            # Only parse HTML content
            content_type = resp.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type:
                logger.debug(
                    f'[Crawl {crawl_id}] Skipping non-HTML content: {url} '
                    f'(Content-Type: {content_type})'
                )
                continue
            
            # IMPROVEMENT: Explicitly specify parser for security
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Extract all links
            for link in soup.find_all('a', href=True):
                href = link['href'].strip()
                if not href:
                    continue
                
                # Convert relative URLs to absolute
                full_url = urljoin(url, href)
                
                # Check if it's a PDF
                if is_pdf_url(full_url):
                    normalized = normalize_url(full_url)
                    is_valid, _ = is_valid_document(
                        normalized, keyword_filters, policy_types
                    )
                    
                    if is_valid and normalized not in pdf_urls:
                        pdf_urls.add(normalized)
                        logger.debug(
                            f'[Crawl {crawl_id}] Found valid PDF ({len(pdf_urls)}): '
                            f'{normalized}'
                        )
                    continue
                
                # Add to queue if same domain and not visited
                if same_domain(seed_url, full_url) and full_url not in visited:
                    # Prevent queue explosion
                    if len(queue) < max_pages * 3:
                        queue.append(full_url)
                    else:
                        logger.debug(
                            f"[Crawl {crawl_id}] Queue size limit reached "
                            f"({len(queue)}), not adding more URLs"
                        )
        
        except requests.exceptions.Timeout:
            logger.warning(f'[Crawl {crawl_id}] Timeout fetching {url}')
        
        except requests.exceptions.RequestException as e:
            logger.warning(f'[Crawl {crawl_id}] Request error fetching {url}: {e}')
        
        except Exception as e:
            logger.warning(
                f'[Crawl {crawl_id}] Unexpected error parsing {url}: {e}',
                exc_info=True
            )
    
    logger.info(
        f'[Crawl {crawl_id}] Crawl complete: {pages_crawled} pages scanned, '
        f'{len(pdf_urls)} valid PDFs found, {len(visited)} URLs visited'
    )
    
    return list(pdf_urls)


# ============================================================================
# MAIN CRAWL EXECUTION ENGINE
# ============================================================================

def run_crawl_session(session_id: int):
    """
    Execute a crawl session with own DB connection.
    
    CRITICAL IMPROVEMENTS FROM V5:
    1. Registers/unregisters from active crawl tracking
    2. Better structured logging with crawl_id context
    3. Improved error handling with specific exceptions
    4. Graceful cleanup on all exit paths
    5. More defensive duplicate checking
    6. Better progress tracking
    7. Memory-efficient operations
    
    This is the main ingestion pipeline:
    1. Check concurrency limits
    2. Update status to 'running'
    3. Crawl each seed URL
    4. Apply filters
    5. Download PDFs
    6. Store metadata with deduplication
    7. Update progress
    8. Clean up resources
    """
    # Import here to avoid circular dependency
    from app.database import get_db_context
    
    logger.info(f"[Crawl {session_id}] Starting crawl session")
    
    # CRITICAL: Track active crawl for concurrency limits
    register_active_crawl(session_id)
    
    http_session = None
    
    try:
        # CRITICAL: Create own DB session for background task
        with get_db_context() as db:
            session = db.query(CrawlSession).filter(
                CrawlSession.id == session_id
            ).first()
            
            if not session:
                logger.error(f"[Crawl {session_id}] Crawl session not found in database")
                return
            
            # Update status to running
            session.status = "running"
            session.started_at = datetime.now(timezone.utc)
            db.commit()
            
            logger.info(
                f"[Crawl {session_id}] Configuration: "
                f"country={session.country}, max_pages={session.max_pages}, "
                f"max_minutes={session.max_minutes}, seeds={len(session.seed_urls)}, "
                f"filters={len(session.keyword_filters)}"
            )
            
            # Calculate time limit
            time_limit = None
            if session.max_minutes:
                time_limit = datetime.now(timezone.utc) + timedelta(
                    minutes=session.max_minutes
                )
                logger.info(
                    f"[Crawl {session_id}] Time limit: {session.max_minutes} minutes "
                    f"(until {time_limit.isoformat()})"
                )
            
            # Create reusable HTTP session with connection pooling
            http_session = get_session_with_retries()
            
            all_pdf_urls: Set[str] = set()
            total_pages = 0
            
            # Crawl each seed URL
            for idx, seed_url in enumerate(session.seed_urls, 1):
                # Check time limit before each seed
                if time_limit and datetime.now(timezone.utc) > time_limit:
                    logger.warning(
                        f"[Crawl {session_id}] Time limit reached before seed {idx}"
                    )
                    break
                
                logger.info(
                    f"[Crawl {session_id}] Crawling seed {idx}/{len(session.seed_urls)}: "
                    f"{seed_url}"
                )
                
                pdf_urls = crawl_domain(
                    seed_url=seed_url,
                    max_pages=session.max_pages,
                    keyword_filters=session.keyword_filters,
                    policy_types=session.policy_types,
                    session=http_session,
                    crawl_id=session_id,
                    time_limit=time_limit
                )
                
                all_pdf_urls.update(pdf_urls)
                total_pages += session.max_pages
                
                # Update progress after each seed
                session.pages_scanned = total_pages
                session.pdfs_found = len(all_pdf_urls)
                session.progress_pct = min(
                    50,
                    int((idx / len(session.seed_urls)) * 50)
                )
                db.commit()
                
                logger.info(
                    f"[Crawl {session_id}] Seed {idx} complete: "
                    f"{len(pdf_urls)} PDFs found, {len(all_pdf_urls)} total unique"
                )
            
            # Download PDFs and create document records
            downloaded_count = 0
            filtered_count = 0
            duplicate_count = 0
            error_count = 0
            
            logger.info(
                f"[Crawl {session_id}] Starting PDF downloads "
                f"({len(all_pdf_urls)} candidates)"
            )
            
            for idx, pdf_url in enumerate(all_pdf_urls, 1):
                # Check time limit
                if time_limit and datetime.now(timezone.utc) > time_limit:
                    logger.warning(
                        f"[Crawl {session_id}] Time limit reached during downloads "
                        f"({idx}/{len(all_pdf_urls)})"
                    )
                    break
                
                # Re-apply filters to determine policy type
                is_valid, policy_type = is_valid_document(
                    pdf_url,
                    session.keyword_filters,
                    session.policy_types
                )
                
                if not is_valid:
                    filtered_count += 1
                    logger.debug(
                        f"[Crawl {session_id}] Filtered out on re-check: {pdf_url}"
                    )
                    continue
                
                # Extract insurer name
                insurer = extract_insurer_name(pdf_url)
                
                # Generate safe filename
                url_path = urlparse(pdf_url).path
                filename = os.path.basename(url_path) or "document.pdf"
                filename = sanitize_filename(filename)
                
                if not filename.endswith('.pdf'):
                    filename += '.pdf'
                
                # Ensure unique filename (prevent overwrites)
                base_name = filename.replace('.pdf', '')
                counter = 1
                while True:
                    test_path = RAW_STORAGE_DIR / insurer / filename
                    if not test_path.exists():
                        break
                    filename = f"{base_name}_{counter}.pdf"
                    counter += 1
                
                # Download PDF with streaming
                local_path = RAW_STORAGE_DIR / insurer / filename
                download_result = download_pdf_streaming(
                    pdf_url, local_path, http_session, session_id
                )
                
                if download_result:
                    try:
                        # CRITICAL: Check for duplicate BEFORE insert
                        # Use SELECT FOR UPDATE to prevent race condition
                        existing_doc = db.query(Document).filter(
                            Document.file_hash == download_result['file_hash']
                        ).with_for_update().first()
                        
                        if existing_doc:
                            logger.info(
                                f"[Crawl {session_id}] Duplicate PDF detected "
                                f"(hash match): {pdf_url} == {existing_doc.source_url}"
                            )
                            
                            # Delete downloaded file
                            try:
                                if local_path.exists():
                                    local_path.unlink()
                                    logger.debug(
                                        f"[Crawl {session_id}] Deleted duplicate file: "
                                        f"{local_path}"
                                    )
                            except Exception as e:
                                logger.error(
                                    f"[Crawl {session_id}] Failed to delete duplicate "
                                    f"file {local_path}: {e}"
                                )
                            
                            duplicate_count += 1
                            filtered_count += 1
                            continue
                        
                        # Create document record
                        doc = Document(
                            crawl_session_id=session.id,
                            source_url=pdf_url,
                            insurer=insurer,
                            local_file_path=str(local_path),
                            file_size=download_result['file_size'],
                            file_hash=download_result['file_hash'],
                            country=session.country,
                            policy_type=policy_type or "General",
                            document_type="PDF",
                            classification="Unclassified",
                            confidence=0.0,
                            status="pending"
                        )
                        
                        db.add(doc)
                        downloaded_count += 1
                        
                        # Update session stats
                        session.pdfs_downloaded = downloaded_count
                        session.progress_pct = 50 + min(
                            50,
                            int((idx / max(len(all_pdf_urls), 1)) * 50)
                        )
                        db.commit()
                        
                        if downloaded_count % 10 == 0:
                            logger.info(
                                f"[Crawl {session_id}] Progress: "
                                f"{downloaded_count}/{len(all_pdf_urls)} downloaded, "
                                f"{duplicate_count} duplicates, {filtered_count} filtered"
                            )
                    
                    except SQLAlchemyError as e:
                        logger.error(
                            f"[Crawl {session_id}] Database error processing {pdf_url}: {e}",
                            exc_info=True
                        )
                        error_count += 1
                        session.errors_count += 1
                        db.commit()
                
                else:
                    error_count += 1
                    session.errors_count += 1
                    db.commit()
            
            # Mark as completed
            session.status = "completed"
            session.completed_at = datetime.now(timezone.utc)
            session.progress_pct = 100
            session.pdfs_filtered = filtered_count
            db.commit()
            
            duration = (session.completed_at - session.started_at).total_seconds()
            
            logger.info(
                f"[Crawl {session_id}] Crawl session completed successfully: "
                f"{downloaded_count} PDFs downloaded, "
                f"{duplicate_count} duplicates skipped, "
                f"{filtered_count} filtered, "
                f"{error_count} errors, "
                f"duration={duration:.1f}s"
            )
    
    except SQLAlchemyError as e:
        logger.error(
            f"[Crawl {session_id}] Database error during crawl: {e}",
            exc_info=True
        )
        
        # Try to mark as failed (best effort)
        try:
            with get_db_context() as db:
                session = db.query(CrawlSession).filter(
                    CrawlSession.id == session_id
                ).first()
                if session:
                    session.status = "failed"
                    session.completed_at = datetime.now(timezone.utc)
                    session.errors_count += 1
        except Exception:
            logger.error(
                f"[Crawl {session_id}] Failed to mark session as failed",
                exc_info=True
            )
    
    except Exception as e:
        logger.error(
            f"[Crawl {session_id}] Unexpected error during crawl: {e}",
            exc_info=True
        )
        
        # Try to mark as failed (best effort)
        try:
            with get_db_context() as db:
                session = db.query(CrawlSession).filter(
                    CrawlSession.id == session_id
                ).first()
                if session:
                    session.status = "failed"
                    session.completed_at = datetime.now(timezone.utc)
                    session.errors_count += 1
        except Exception:
            logger.error(
                f"[Crawl {session_id}] Failed to mark session as failed",
                exc_info=True
            )
    
    finally:
        # CRITICAL: Clean up resources
        if http_session:
            try:
                http_session.close()
                logger.debug(f"[Crawl {session_id}] Closed HTTP session")
            except Exception as e:
                logger.error(
                    f"[Crawl {session_id}] Error closing HTTP session: {e}"
                )
        
        # CRITICAL: Unregister from active crawls
        unregister_active_crawl(session_id)


# ============================================================================
# QUERY FUNCTIONS
# ============================================================================

def get_crawl_status(db: Session, session_id: int) -> Optional[CrawlSession]:
    """Get crawl session status."""
    return db.query(CrawlSession).filter(CrawlSession.id == session_id).first()


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    'create_crawl_session',
    'run_crawl_session',
    'get_crawl_status',
    'can_start_crawl',
    'get_active_crawl_count',
]
