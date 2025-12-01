"""
GitLab MCP Server
Complete MCP server for GitLab operations including PR/MR processing,
ADR document creation, and pipeline job management.
"""

import os
import json
import asyncio
from typing import Dict, List, Optional, Any, Set, Final
from datetime import datetime
from pathlib import Path
import aiohttp
import re  
import logging  
# Import PolyMCP library
from polymcp import expose_tools_inprocess, expose_tools_http
from dotenv import load_dotenv  
import urllib.parse

# Security Constants
MAX_QUERY_LENGTH: Final[int] = 200
MAX_LOG_LINES: Final[int] = 1000
MAX_LOG_SIZE: Final[int] = 1024 * 1024  # 1MB
REQUEST_TIMEOUT_TOTAL: Final[int] = 30
REQUEST_TIMEOUT_CONNECT: Final[int] = 10
MAX_RESULTS_CAP: Final[int] = 100

# Load .env file at module level
load_dotenv()  

# Setup logger
logger = logging.getLogger(__name__) 
# ============================================================================
# Configuration
# ============================================================================

def _validate_and_encode_project_id(project_id: str, allow_empty: bool = False) -> str:
    """
    Validate and URL-encode project ID to prevent injection attacks.
    
    Args:
        project_id: The project ID or path to validate
        allow_empty: If True, returns empty string for None/empty input
        
    Returns:
        URL-encoded project ID safe for use in API paths
        
    Raises:
        ValueError: If project_id is invalid or contains unsafe characters
    """
    if not project_id:
        if allow_empty:
            return ""
        raise ValueError("Project ID is required")
    
    project_id = str(project_id).strip()
    
    if len(project_id) > 500:
        raise ValueError("Project ID too long (max 500 characters)")
    
    if not re.match(r'^[\w\-./]+$', project_id):
        raise ValueError(f"Invalid characters in project ID. Allowed: alphanumeric, dash, underscore, dot, slash")
    
    if '..' in project_id:
        raise ValueError("Invalid project ID: path traversal not allowed")
    
    return urllib.parse.quote(project_id, safe='')


def _sanitize_for_log(value: str, max_length: int = 100) -> str:
    """
    Sanitize a value for safe logging (remove potential secrets).
    
    Args:
        value: The value to sanitize
        max_length: Maximum length to include in log
        
    Returns:
        Sanitized string safe for logging
    """
    if not value:
        return ""
    
    sanitized = str(value)[:max_length]
    
    sensitive_patterns = ['token', 'password', 'secret', 'key', 'auth']
    for pattern in sensitive_patterns:
        if pattern in sanitized.lower():
            sanitized = re.sub(
                rf'({pattern}["\s:=]+)[^\s"&]+',
                r'\1[REDACTED]',
                sanitized,
                flags=re.IGNORECASE
            )
    
    if len(str(value)) > max_length:
        sanitized += "..."
    
    return sanitized

class GitLabConfig:
    """GitLab configuration management with all .env settings."""
    
    def __init__(self):
        # GitLab API Settings
        self.base_url = os.getenv("GITLAB_BASE_URL", "https://gitlab.com/api/v4").rstrip('/')
        self.token = os.getenv("GITLAB_TOKEN", "")
        self.default_project_id = os.getenv("GITLAB_PROJECT_ID", "")
        
        # Security Settings
        self.safe_mode = os.getenv("SAFE_MODE", "true").lower() == "true"
        self.dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
        self.allowed_projects = self._parse_allowed_projects()
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        
        # Server Settings
        self.host = os.getenv("HOST", "127.0.0.1")
        self.port = int(os.getenv("PORT", "8000"))
        self.verbose = os.getenv("VERBOSE", "false").lower() == "true"
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        
        # PolyMCP Settings
        self.llm_provider = os.getenv("LLM_PROVIDER", "").lower() or None
        self.llm_model = os.getenv("LLM_MODEL", "")
        self.llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.1"))
        
        # Setup logging
        self._setup_logging()
        # Validate critical settings
        if not self.token:
            logger.error("GITLAB_TOKEN not set in environment!")
            raise ValueError("GITLAB_TOKEN is required. Set it in .env file")
        
        if len(self.token) < 20:
            logger.error("GITLAB_TOKEN appears invalid (too short)")
            raise ValueError("GITLAB_TOKEN appears invalid - token too short")
        
    def _parse_allowed_projects(self):
        """Parse comma-separated allowed projects."""
        projects_str = os.getenv("ALLOWED_PROJECTS", "")
        if not projects_str:
            return set()  # Empty = no projects allowed
        if projects_str.strip() == "*":
            return {"*"}  # Wildcard
        return set(p.strip() for p in projects_str.split(',') if p.strip())
    
    def _setup_logging(self):
        """Setup logging based on configuration."""
        import logging
        log_level = getattr(logging, self.log_level, logging.INFO)
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
    @property
    def headers(self) -> Dict[str, str]:
        """
        Generate fresh headers for GitLab API requests.
        Note: Headers are generated fresh each time to avoid token caching issues.
        """
        return {
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json",
            "User-Agent": "GitLab-MCP-Server/1.0"
        }
    
    def is_project_allowed(self, project_id: str) -> bool:
        """
        Check if project is allowed based on configuration.
        - Empty ALLOWED_PROJECTS = deny all (secure by default)
        - ALLOWED_PROJECTS=* = allow all
        - ALLOWED_PROJECTS=proj1,proj2 = allow only listed
        """
        if not self.allowed_projects:  # Empty = deny all
            logger.warning("No projects allowed (ALLOWED_PROJECTS is empty)")
            return False
        if "*" in self.allowed_projects:  # Wildcard = allow all
            return True
        return project_id in self.allowed_projects

config = GitLabConfig()

# ============================================================================
# Rate Limiting
# ============================================================================

from collections import defaultdict
from time import time

class RateLimiter:
    def __init__(self, max_requests=60, time_window=60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = defaultdict(list)
    
    def is_allowed(self, key: str = "global") -> bool:
        now = time()
        # Remove old requests
        self.requests[key] = [r for r in self.requests[key] if now - r < self.time_window]
        
        if len(self.requests[key]) >= self.max_requests:
            return False
        
        self.requests[key].append(now)
        return True

rate_limiter = RateLimiter()

# ============================================================================
# GitLab API Client
# ============================================================================

class GitLabClient:
    """Async GitLab API client with retry logic and security features."""
    
    def __init__(self, base_url: str = None, token: str = None):
        self.base_url = (base_url or config.base_url).rstrip('/')
        self.token = token or config.token
        self.max_retries = config.max_retries
        
    @property
    def headers(self) -> Dict[str, str]:
        """Generate fresh headers for each request - never cache tokens."""
        return {
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json",
            "User-Agent": "GitLab-MCP-Server/1.0"
        }
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Make async request to GitLab API with retry logic and security."""
        if not rate_limiter.is_allowed():
            logger.warning("Local rate limit exceeded, waiting...")
            await asyncio.sleep(1)
        
        url = f"{self.base_url}{endpoint}"
        kwargs['headers'] = {**self.headers, **kwargs.get('headers', {})}
        
        if config.verbose:
            safe_url = _sanitize_for_log(url, 200)
            logger.debug(f"Request: {method} {safe_url}")
        
        retries = 0
        last_error = None
        
        timeout = aiohttp.ClientTimeout(
            total=REQUEST_TIMEOUT_TOTAL,
            connect=REQUEST_TIMEOUT_CONNECT
        )
        
        while retries < self.max_retries:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(method, url, **kwargs) as response:
                        if response.status == 429:
                            retry_after = int(response.headers.get('Retry-After', 60))
                            retry_after = min(retry_after, 300)
                            if config.verbose:
                                logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                            await asyncio.sleep(retry_after)
                            retries += 1
                            continue
                        
                        if response.status == 401:
                            logger.error("Authentication failed - check GITLAB_TOKEN")
                            raise aiohttp.ClientResponseError(
                                response.request_info,
                                response.history,
                                status=401,
                                message="Authentication failed"
                            )
                        
                        if response.status == 403:
                            logger.error(f"Access denied to: {_sanitize_for_log(endpoint)}")
                            raise aiohttp.ClientResponseError(
                                response.request_info,
                                response.history,
                                status=403,
                                message="Access denied"
                            )
                        
                        if response.status == 404:
                            raise aiohttp.ClientResponseError(
                                response.request_info,
                                response.history,
                                status=404,
                                message="Resource not found"
                            )
                        
                        response.raise_for_status()
                        
                        content_type = response.headers.get('Content-Type', '')
                        if 'application/json' in content_type:
                            return await response.json()
                        else:
                            return {"content": await response.text()}
                            
            except asyncio.TimeoutError:
                last_error = Exception("Request timed out")
                retries += 1
                if retries < self.max_retries:
                    wait_time = min(2 ** retries, 30)
                    logger.warning(f"Request timed out, retrying in {wait_time}s... (attempt {retries}/{self.max_retries})")
                    await asyncio.sleep(wait_time)
                    
            except aiohttp.ClientResponseError as e:
                if e.status in [401, 403, 404]:
                    raise
                last_error = e
                retries += 1
                if retries < self.max_retries:
                    wait_time = min(2 ** retries, 30)
                    logger.warning(f"Request failed ({e.status}), retrying in {wait_time}s... (attempt {retries}/{self.max_retries})")
                    await asyncio.sleep(wait_time)
                    
            except aiohttp.ClientError as e:
                last_error = e
                retries += 1
                if retries < self.max_retries:
                    wait_time = min(2 ** retries, 30)
                    if config.verbose:
                        logger.warning(f"Request failed, retrying in {wait_time}s... (attempt {retries}/{self.max_retries})")
                    await asyncio.sleep(wait_time)
        
        logger.error(f"API request failed after {self.max_retries} retries")
        if last_error:
            raise last_error
        raise Exception("Request failed after maximum retries")
    
    async def get(self, endpoint: str, params: Dict = None) -> Dict:
        """GET request."""
        return await self._request("GET", endpoint, params=params)
    
    async def post(self, endpoint: str, json_data: Dict = None) -> Dict:
        """POST request."""
        return await self._request("POST", endpoint, json=json_data)
    
    async def put(self, endpoint: str, json_data: Dict = None) -> Dict:
        """PUT request."""
        return await self._request("PUT", endpoint, json=json_data)
    
    async def delete(self, endpoint: str, params: Dict = None) -> Dict:
        """DELETE request."""
        return await self._request("DELETE", endpoint, params=params)
    
# ============================================================================
# Security Helpers
# ============================================================================

def check_safe_mode(operation_type: str = "write"):
    """Decorator to check safe mode for destructive operations."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            if config.safe_mode and operation_type in ["write", "delete", "merge"]:
                logger.warning(f"Safe mode enabled. Blocking {func.__name__} operation.")
                return {
                    "status": "blocked",
                    "message": f"Operation {func.__name__} blocked by safe mode",
                    "safe_mode": True,
                    "hint": "Set SAFE_MODE=false in .env to enable this operation"
                }
            return await func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator

def dry_run_mode(func):
    """Decorator to simulate operations in dry run mode."""
    async def wrapper(*args, **kwargs):
        if config.dry_run:
            logger.info(f"Dry run mode: Simulating {func.__name__}")
            # Extract meaningful info from args/kwargs
            info = {}
            if 'project_id' in kwargs:
                info['project_id'] = kwargs['project_id']
            if 'mr_iid' in kwargs:
                info['mr_iid'] = kwargs['mr_iid']
            return {
                "status": "dry_run",
                "message": f"Dry run simulation of {func.__name__}",
                "operation": func.__name__,
                "parameters": info,
                "dry_run": True,
                "hint": "Set DRY_RUN=false in .env to execute this operation"
            }
        return await func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper

def check_project_access(func):
    """Decorator to check if project is in allowed list and validate project ID."""
    async def wrapper(*args, **kwargs):
        project_id = kwargs.get('project_id')
        if not project_id and len(args) > 0:
            project_id = args[0] if isinstance(args[0], str) else None
        
        if project_id:
            try:
                validated_project_id = _validate_and_encode_project_id(project_id)
                
                if 'project_id' in kwargs:
                    kwargs['project_id'] = validated_project_id
                elif len(args) > 0 and isinstance(args[0], str):
                    args = (validated_project_id,) + args[1:]
                    
            except ValueError as e:
                logger.warning(f"Invalid project ID rejected: {_sanitize_for_log(str(project_id))}")
                return {
                    "status": "error",
                    "error": str(e),
                    "hint": "Project ID should contain only alphanumeric characters, dashes, underscores, dots, and slashes"
                }
        
        decoded_project_id = urllib.parse.unquote(project_id) if project_id else None
        if decoded_project_id and config.allowed_projects and not config.is_project_allowed(decoded_project_id):
            logger.warning(f"Access denied to project {_sanitize_for_log(decoded_project_id)}")
            return {
                "status": "error",
                "error": f"Access denied. Project is not in allowed list",
                "hint": "Add project to ALLOWED_PROJECTS in .env or leave empty to allow all"
            }
        
        return await func(*args, **kwargs)
    
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper

# ============================================================================
# Helper Functions
# ============================================================================

async def _get_next_adr_number(client: GitLabClient, project_id: str, path: str = "docs/adr") -> str:
    """
    Generate unique ADR number with collision prevention.
    Security: Read-only operation, no decorators needed.
    """
    try:
        # Get existing ADR files
        params = {"path": path, "ref": "main", "per_page": 100}
        files = await client.get(f"/projects/{project_id}/repository/tree", params)
        
        # Extract all ADR numbers for today
        today = datetime.now().strftime("%Y%m%d")
        today_count = 0
        
        for file in files:
            if file["type"] == "blob" and file["name"].startswith("ADR-"):
                # Match patterns like ADR-20241215-01-title.md
                if match := re.search(r"ADR-(\d{8})(?:-(\d{2}))?", file["name"]):
                    date_part = match.group(1)
                    if date_part == today:
                        seq = int(match.group(2)) if match.group(2) else 1
                        today_count = max(today_count, seq)
        
        # Generate number
        if today_count >= 1:
            return f"{today}-{today_count + 1:02d}"
        return today
        
    except Exception as e:
        if config.verbose:
            logger.warning(f"Could not check existing ADRs: {e}, using timestamp")
        # Fallback to timestamp to ensure uniqueness
        return datetime.now().strftime("%Y%m%d-%H%M%S")

# ============================================================================
# Merge Request Tools
# ============================================================================
@check_project_access
async def list_merge_requests(
    project_id: str,
    state: str = "opened",
    scope: str = "all",
    author_username: Optional[str] = None,
    assignee_username: Optional[str] = None
) -> Dict[str, Any]:
    """
    List merge requests for a GitLab project.
    
    Args:
        project_id: GitLab project ID or path (e.g., "group/project")
        state: State of MRs to retrieve (opened, closed, merged, all)
        scope: Return MRs for the given scope (created_by_me, assigned_to_me, all)
        author_username: Filter by author username
        assignee_username: Filter by assignee username
    
    Returns:
        Dictionary containing list of merge requests with their details
    """
    client = GitLabClient(config.base_url, config.token)
    
    valid_states = ["opened", "closed", "merged", "all"]
    valid_scopes = ["created_by_me", "assigned_to_me", "all"]
    
    if state not in valid_states:
        return {"status": "error", "error": f"Invalid state. Must be one of: {valid_states}"}
    if scope not in valid_scopes:
        return {"status": "error", "error": f"Invalid scope. Must be one of: {valid_scopes}"}
    
    params = {
        "state": state,
        "scope": scope,
        "per_page": MAX_RESULTS_CAP
    }
    
    if author_username:
        if not re.match(r'^[\w\-._]+$', author_username):
            return {"status": "error", "error": "Invalid author_username format"}
        params["author_username"] = author_username
    if assignee_username:
        if not re.match(r'^[\w\-._]+$', assignee_username):
            return {"status": "error", "error": "Invalid assignee_username format"}
        params["assignee_username"] = assignee_username
    
    try:
        mrs = await client.get(f"/projects/{project_id}/merge_requests", params)
        
        formatted_mrs = []
        for mr in mrs:
            formatted_mrs.append({
                "id": mr["id"],
                "iid": mr["iid"],
                "title": mr["title"],
                "description": (mr.get("description", "") or "")[:500],
                "state": mr["state"],
                "author": mr["author"]["username"],
                "assignee": mr.get("assignee", {}).get("username") if mr.get("assignee") else None,
                "source_branch": mr["source_branch"],
                "target_branch": mr["target_branch"],
                "web_url": mr["web_url"],
                "created_at": mr["created_at"],
                "updated_at": mr["updated_at"],
                "has_conflicts": mr.get("has_conflicts", False),
                "work_in_progress": mr.get("work_in_progress", False)
            })
        
        return {
            "status": "success",
            "count": len(formatted_mrs),
            "merge_requests": formatted_mrs
        }
    
    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            return {"status": "error", "error": "Authentication failed. Check your GitLab token."}
        elif e.status == 403:
            return {"status": "error", "error": "Access denied to this project."}
        elif e.status == 404:
            return {"status": "error", "error": "Project not found."}
        else:
            logger.error(f"GitLab API error: {e.status}")
            return {"status": "error", "error": "GitLab API request failed."}
    except asyncio.TimeoutError:
        return {"status": "error", "error": "Request timed out. Please try again."}
    except Exception as e:
        logger.exception("Unexpected error in list_merge_requests")
        return {"status": "error", "error": "An unexpected error occurred."}

@check_project_access
async def get_merge_request_details(
    project_id: str,
    mr_iid: int,
    include_changes: bool = False,
    include_discussions: bool = False
) -> Dict[str, Any]:
    """
    Get detailed information about a specific merge request.
    
    Args:
        project_id: GitLab project ID or path
        mr_iid: Merge request internal ID
        include_changes: Include file changes/diffs
        include_discussions: Include comments and discussions
    
    Returns:
        Detailed merge request information
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Get MR details
        mr = await client.get(f"/projects/{project_id}/merge_requests/{mr_iid}")
        
        result = {
            "status": "success",
            "merge_request": {
                "id": mr["id"],
                "iid": mr["iid"],
                "title": mr["title"],
                "description": mr.get("description", ""),
                "state": mr["state"],
                "author": mr["author"],
                "assignee": mr.get("assignee"),
                "source_branch": mr["source_branch"],
                "target_branch": mr["target_branch"],
                "merge_status": mr.get("merge_status"),
                "pipeline": mr.get("pipeline"),
                "approvals": mr.get("approvals_before_merge"),
                "web_url": mr["web_url"]
            }
        }
        
        # Get changes if requested
        if include_changes:
            changes = await client.get(f"/projects/{project_id}/merge_requests/{mr_iid}/changes")
            result["changes"] = {
                "files_changed": len(changes.get("changes", [])),
                "changes": changes.get("changes", [])
            }
        
        # Get discussions if requested
        if include_discussions:
            discussions = await client.get(f"/projects/{project_id}/merge_requests/{mr_iid}/discussions")
            result["discussions"] = discussions
        
        return result
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

@check_project_access
async def search_code(
    project_id: str,
    query: str,
    ref: str = "main",
    path: Optional[str] = None,
    max_results: int = 20
) -> Dict[str, Any]:
    """
    Search code in a GitLab project.
    Security: Read-only operation, only project access check.
    
    Args:
        project_id: GitLab project ID
        query: Search query string
        ref: Branch/tag/commit to search (default: main)
        path: Limit search to specific path
        max_results: Maximum results to return (capped at 100)
    
    Returns:
        Search results with file content and matches
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        if not project_id and config.default_project_id:
            project_id = config.default_project_id
        
        if not query or not query.strip():
            return {"status": "error", "error": "Search query is required"}
            
        if len(query) > MAX_QUERY_LENGTH:
            return {"status": "error", "error": f"Search query too long (max {MAX_QUERY_LENGTH} characters)"}
        
        query = query.strip()
        query = re.sub(r'[\'";\\]', '', query)
        
        if not re.match(r'^[\w\-._/]+$', ref):
            return {"status": "error", "error": "Invalid ref format"}
        
        if path:
            if '..' in path or path.startswith('/'):
                return {"status": "error", "error": "Invalid path format"}
            if not re.match(r'^[\w\-._/]+$', path):
                return {"status": "error", "error": "Invalid path characters"}
        
        max_results = min(max(1, max_results), MAX_RESULTS_CAP)
        
        params = {
            "scope": "blobs",
            "search": query,
            "ref": ref,
            "per_page": max_results
        }
        
        if path:
            params["in"] = path
        
        if config.verbose:
            logger.info(f"Searching for '{_sanitize_for_log(query)}' in project")
            
        results = await client.get(f"/projects/{project_id}/search", params)
        
        formatted_results = []
        for result in results[:max_results]:
            data = result.get("data", "")
            if len(data) > 500:
                data = data[:500] + "... (truncated)"
                
            formatted_results.append({
                "filename": result["filename"],
                "path": result["path"],
                "ref": result["ref"],
                "data": data,
                "project_id": result["project_id"]
            })
        
        return {
            "status": "success",
            "query": query,
            "ref": ref,
            "count": len(formatted_results),
            "results": formatted_results
        }
    
    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            return {"status": "error", "error": "Authentication failed."}
        elif e.status == 403:
            return {"status": "error", "error": "Access denied."}
        elif e.status == 404:
            return {"status": "error", "error": "Project not found."}
        else:
            logger.error(f"Search API error: {e.status}")
            return {"status": "error", "error": "Search request failed."}
    except asyncio.TimeoutError:
        return {"status": "error", "error": "Search timed out."}
    except Exception as e:
        logger.exception("Unexpected error in search_code")
        return {"status": "error", "error": "An unexpected error occurred."}

@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def approve_merge_request(
    project_id: str,
    mr_iid: int,
    approval_comment: Optional[str] = None
) -> Dict[str, Any]:
    """
    Approve a merge request.
    
    Args:
        project_id: GitLab project ID or path
        mr_iid: Merge request internal ID
        approval_comment: Optional comment with approval
    
    Returns:
        Approval status
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        if config.verbose:
            logger.info(f"Approving MR {mr_iid} in project {project_id}")
            
        # Approve the MR
        result = await client.post(f"/projects/{project_id}/merge_requests/{mr_iid}/approve")
        
        # Add comment if provided
        if approval_comment:
            await client.post(
                f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
                {"body": approval_comment}
            )
        
        return {
            "status": "success",
            "message": f"Merge request {mr_iid} approved",
            "approval_details": result
        }
    
    except Exception as e:
        logger.error(f"Failed to approve MR {mr_iid}: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }
    
@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def create_merge_request(
    project_id: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str = "",
    assignee_id: Optional[int] = None,
    reviewer_ids: Optional[List[int]] = None,
    labels: Optional[List[str]] = None,
    remove_source_branch: bool = True,
    squash: bool = False,
    draft: bool = False
) -> Dict[str, Any]:
    """
    Create a new merge request with full security checks.
    
    Args:
        project_id: GitLab project ID or path
        source_branch: Source branch name
        target_branch: Target branch name (usually main/master)
        title: MR title
        description: MR description in Markdown
        assignee_id: User ID to assign
        reviewer_ids: List of reviewer user IDs
        labels: List of label names
        remove_source_branch: Delete source branch when merged
        squash: Squash commits when merging
        draft: Create as draft/WIP
    
    Returns:
        Created merge request details
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Security: Use default project if not specified and configured
        if not project_id and config.default_project_id:
            project_id = config.default_project_id
            
        if not project_id:
            return {
                "status": "error",
                "error": "Project ID required but not provided or configured"
            }
        
        data = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": f"Draft: {title}" if draft else title,
            "description": description,
            "remove_source_branch": remove_source_branch,
            "squash": squash
        }
        
        # Optional fields
        if assignee_id:
            data["assignee_id"] = assignee_id
        if reviewer_ids:
            data["reviewer_ids"] = reviewer_ids
        if labels:
            data["labels"] = ",".join(labels)
        
        if config.verbose:
            logger.info(f"Creating MR in project {project_id}: {title}")
            
        result = await client.post(f"/projects/{project_id}/merge_requests", data)
        
        return {
            "status": "success",
            "merge_request": {
                "id": result["id"],
                "iid": result["iid"],
                "web_url": result["web_url"],
                "title": result["title"],
                "state": result["state"],
                "source_branch": result["source_branch"],
                "target_branch": result["target_branch"]
            }
        }
    except Exception as e:
        logger.error(f"Failed to create MR: {str(e)}")
        return {"status": "error", "error": str(e)}

@check_project_access
@check_safe_mode("merge")
@dry_run_mode
async def merge_merge_request(
    project_id: str,
    mr_iid: int,
    squash: bool = False,
    remove_source_branch: bool = True,
    merge_commit_message: Optional[str] = None
) -> Dict[str, Any]:
    """
    Merge a merge request.
    
    Args:
        project_id: GitLab project ID or path
        mr_iid: Merge request internal ID
        squash: Squash commits when merging
        remove_source_branch: Delete source branch after merge
        merge_commit_message: Custom merge commit message
    
    Returns:
        Merge status
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        if config.verbose:
            logger.info(f"Merging MR {mr_iid} in project {project_id}")
            
        data = {
            "squash": squash,
            "should_remove_source_branch": remove_source_branch
        }
        
        if merge_commit_message:
            data["merge_commit_message"] = merge_commit_message
        
        result = await client.put(
            f"/projects/{project_id}/merge_requests/{mr_iid}/merge",
            data
        )
        
        return {
            "status": "success",
            "message": f"Merge request {mr_iid} merged successfully",
            "merge_details": result
        }
    
    except Exception as e:
        logger.error(f"Failed to merge MR {mr_iid}: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }
    
@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def rebase_merge_request(
    project_id: str,
    mr_iid: int,
    skip_ci: bool = False
) -> Dict[str, Any]:
    """
    Rebase a merge request onto the target branch.
    Security: Protected by safe_mode and project access checks.
    
    Args:
        project_id: GitLab project ID or path
        mr_iid: Merge request internal ID
        skip_ci: Skip CI pipeline after rebase
    
    Returns:
        Rebase status and commit SHA
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Use default project if not specified
        if not project_id and config.default_project_id:
            project_id = config.default_project_id
            
        data = {"skip_ci": skip_ci} if skip_ci else {}
        
        if config.verbose:
            logger.info(f"Rebasing MR {mr_iid} in project {project_id}")
        
        result = await client.put(
            f"/projects/{project_id}/merge_requests/{mr_iid}/rebase",
            data
        )
        
        return {
            "status": "success",
            "message": f"Merge request {mr_iid} rebased successfully",
            "rebase_in_progress": result.get("rebase_in_progress", False),
            "merge_error": result.get("merge_error")
        }
    except Exception as e:
        logger.error(f"Failed to rebase MR {mr_iid}: {str(e)}")
        return {"status": "error", "error": str(e)}

# ============================================================================
# ADR (Architecture Decision Record) Tools
# ============================================================================

async def create_adr_document( 
    title: str,
    status: str,
    context: str,
    decision: str,
    consequences: str,
    alternatives: Optional[str] = None,
    related_decisions: Optional[List[str]] = None,
    participants: Optional[List[str]] = None,
    save_path: Optional[str] = None,
    project_id: Optional[str] = None 
) -> Dict[str, Any]:
    """
    Create an Architecture Decision Record (ADR) document in Markdown format.
    Security: Local operation, no API calls unless project_id specified.
    
    Args:
        title: ADR title
        status: Decision status (proposed, accepted, deprecated, superseded)
        context: Context and problem statement
        decision: The decision that was made
        consequences: Positive and negative consequences
        alternatives: Alternative solutions considered
        related_decisions: List of related ADR numbers or titles
        participants: List of participants in the decision
        save_path: Optional path to save the ADR file
        project_id: Optional project ID for unique numbering check
    
    Returns:
        Dictionary with ADR content and save status
    """
    # Generate ADR number - use API if project_id provided
    if project_id and not config.dry_run:
        try:
            client = GitLabClient(config.base_url, config.token)
            adr_number = await _get_next_adr_number(client, project_id)
        except Exception as e:
            logger.warning(f"Could not get ADR number from GitLab: {e}")
            adr_number = datetime.now().strftime("%Y%m%d-%H%M%S")
    else:
        adr_number = datetime.now().strftime("%Y%m%d")
    
    # Build ADR content (rest stays the same)
    adr_content = f"""# ADR-{adr_number}: {title}

## Status
{status.upper()}

## Date
{datetime.now().strftime("%Y-%m-%d")}

## Context
{context}

## Decision
{decision}

## Consequences
{consequences}
"""
    
    if alternatives:
        adr_content += f"""
## Alternatives Considered
{alternatives}
"""
    
    if related_decisions:
        adr_content += f"""
## Related Decisions
"""
        for related in related_decisions:
            adr_content += f"- {related}\n"
    
    if participants:
        adr_content += f"""
## Participants
"""
        for participant in participants:
            adr_content += f"- {participant}\n"
    
    # Add metadata section
    adr_content += f"""
---
*This ADR was generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""
    
    result = {
        "status": "success",
        "adr_number": f"ADR-{adr_number}",
        "title": title,
        "content": adr_content
    }
    
    # Save to file if path provided
    if save_path and not config.dry_run:  # Respect dry_run
        try:
            file_path = Path(save_path) / f"ADR-{adr_number}-{title.lower().replace(' ', '-')}.md"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(adr_content)
            result["file_path"] = str(file_path)
            result["message"] = f"ADR saved to {file_path}"
        except Exception as e:
            result["save_error"] = str(e)
    elif save_path and config.dry_run:
        result["message"] = "Dry run: ADR not saved to disk"
    
    return result

@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def commit_adr_to_gitlab(
    project_id: str,
    adr_content: str,
    adr_title: str,
    branch: str = "main",
    create_mr: bool = True
) -> Dict[str, Any]:
    """
    Commit an ADR document to a GitLab repository using correct API.
    Security: Protected by safe_mode, dry_run, and project access.
    
    Args:
        project_id: GitLab project ID or path
        adr_content: The ADR markdown content
        adr_title: Title for the ADR
        branch: Target branch for the commit
        create_mr: Create a merge request for the ADR
    
    Returns:
        Commit and MR details
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Use default project if not specified
        if not project_id and config.default_project_id:
            project_id = config.default_project_id
            
        # Get unique ADR number
        adr_number = await _get_next_adr_number(client, project_id)
        file_path = f"docs/adr/ADR-{adr_number}-{adr_title.lower().replace(' ', '-')}.md"
        
        if config.verbose:
            logger.info(f"Committing ADR '{adr_title}' to project {project_id}")
        
        # Prepare branch name
        target_branch = branch
        if create_mr:
            target_branch = f"adr-{adr_number}"
        
        # Use correct commits API with actions
        commit_data = {
            "branch": target_branch,
            "commit_message": f"Add ADR: {adr_title}",
            "actions": [{
                "action": "create",  # or "update" if file exists
                "file_path": file_path,
                "content": adr_content
            }]
        }
        
        # If creating MR, branch from the base branch
        if create_mr and target_branch != branch:
            commit_data["start_branch"] = branch
        
        # Create the commit
        result = await client.post(
            f"/projects/{project_id}/repository/commits",
            commit_data
        )
        
        response = {
            "status": "success",
            "file_path": file_path,
            "branch": target_branch,
            "commit": {
                "id": result["id"],
                "message": result["message"],
                "web_url": result.get("web_url", f"{config.base_url.replace('/api/v4', '')}/{project_id}/-/commit/{result['id']}")
            }
        }
        
        # Create MR if requested
        if create_mr:
            mr_result = await create_merge_request(
                project_id=project_id,
                source_branch=target_branch,
                target_branch=branch,
                title=f"ADR: {adr_title}",
                description=f"This MR adds a new Architecture Decision Record.\n\n{adr_content[:500]}...",
                remove_source_branch=True
            )
            if mr_result["status"] == "success":
                response["merge_request"] = mr_result["merge_request"]
        
        return response
    
    except Exception as e:
        logger.error(f"Failed to commit ADR: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }

# ============================================================================
# Pipeline and Job Management Tools
# ============================================================================

@check_project_access
async def list_pipeline_jobs(
    project_id: str,
    pipeline_id: Optional[int] = None,
    scope: str = "all",
    include_retried: bool = False
) -> Dict[str, Any]:
    """
    List jobs in a GitLab pipeline.
    
    Args:
        project_id: GitLab project ID or path
        pipeline_id: Specific pipeline ID (latest if not specified)
        scope: Job scope (created, pending, running, failed, success, canceled, skipped, manual, all)
        include_retried: Include retried jobs
    
    Returns:
        List of jobs with their status
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Get latest pipeline if not specified
        if not pipeline_id:
            pipelines = await client.get(f"/projects/{project_id}/pipelines", {"per_page": 1})
            if not pipelines:
                return {
                    "status": "error",
                    "error": "No pipelines found"
                }
            pipeline_id = pipelines[0]["id"]
        
        # Get jobs
        params = {
            "scope": scope,
            "include_retried": include_retried,
            "per_page": 100
        }
        
        jobs = await client.get(f"/projects/{project_id}/pipelines/{pipeline_id}/jobs", params)
        
        # Format jobs
        formatted_jobs = []
        for job in jobs:
            formatted_jobs.append({
                "id": job["id"],
                "name": job["name"],
                "stage": job["stage"],
                "status": job["status"],
                "started_at": job.get("started_at"),
                "finished_at": job.get("finished_at"),
                "duration": job.get("duration"),
                "web_url": job["web_url"],
                "retry_count": job.get("retry_count", 0),
                "allow_failure": job.get("allow_failure", False)
            })
        
        # Group by status
        status_groups = {}
        for job in formatted_jobs:
            status = job["status"]
            if status not in status_groups:
                status_groups[status] = []
            status_groups[status].append(job)
        
        return {
            "status": "success",
            "pipeline_id": pipeline_id,
            "total_jobs": len(formatted_jobs),
            "jobs": formatted_jobs,
            "jobs_by_status": status_groups,
            "summary": {
                "failed": len(status_groups.get("failed", [])),
                "success": len(status_groups.get("success", [])),
                "running": len(status_groups.get("running", [])),
                "pending": len(status_groups.get("pending", []))
            }
        }
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
    
@check_project_access
async def get_job_log(
    project_id: str,
    job_id: int,
    lines: Optional[int] = None
) -> Dict[str, Any]:
    """
    Get the log/trace of a specific job.
    
    Args:
        project_id: GitLab project ID or path
        job_id: Job ID
        lines: Number of lines to return (all if not specified, max 1000)
    
    Returns:
        Job log content
    """
    client = GitLabClient(config.base_url, config.token)
    
    if not isinstance(job_id, int) or job_id <= 0:
        return {"status": "error", "error": "Invalid job_id. Must be a positive integer."}
    
    if lines is not None:
        lines = min(max(1, lines), MAX_LOG_LINES)
    
    try:
        job = await client.get(f"/projects/{project_id}/jobs/{job_id}")
        
        log_url = f"/projects/{project_id}/jobs/{job_id}/trace"
        
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_TOTAL, connect=REQUEST_TIMEOUT_CONNECT)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{config.base_url}{log_url}",
                headers=client.headers
            ) as response:
                if response.status == 401:
                    return {"status": "error", "error": "Authentication failed."}
                if response.status == 403:
                    return {"status": "error", "error": "Access denied to job log."}
                if response.status == 404:
                    return {"status": "error", "error": "Job not found."}
                    
                response.raise_for_status()
                log_content = await response.text()
        
        original_size = len(log_content)
        if len(log_content) > MAX_LOG_SIZE:
            log_content = log_content[-MAX_LOG_SIZE:]
            log_content = f"... (truncated, showing last {MAX_LOG_SIZE // 1024}KB of {original_size // 1024}KB)\n" + log_content
        
        if lines and log_content:
            log_lines = log_content.split('\n')
            log_content = '\n'.join(log_lines[-lines:])
        
        error_patterns = []
        error_keywords = ['error', 'failed', 'exception', 'fatal']
        for line in log_content.split('\n'):
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in error_keywords):
                clean_line = line.strip()[:200]
                if clean_line and clean_line not in error_patterns:
                    error_patterns.append(clean_line)
                if len(error_patterns) >= 10:
                    break
        
        return {
            "status": "success",
            "job": {
                "id": job["id"],
                "name": job["name"],
                "status": job["status"]
            },
            "log": log_content,
            "log_size": len(log_content),
            "original_size": original_size,
            "truncated": original_size > MAX_LOG_SIZE,
            "error_patterns": error_patterns
        }
    
    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            return {"status": "error", "error": "Authentication failed."}
        elif e.status == 403:
            return {"status": "error", "error": "Access denied."}
        elif e.status == 404:
            return {"status": "error", "error": "Job not found."}
        else:
            logger.error(f"Job log API error: {e.status}")
            return {"status": "error", "error": "Failed to retrieve job log."}
    except asyncio.TimeoutError:
        return {"status": "error", "error": "Request timed out."}
    except Exception as e:
        logger.exception("Unexpected error in get_job_log")
        return {"status": "error", "error": "An unexpected error occurred."}

@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def retry_failed_job(
    project_id: str,
    job_id: int
) -> Dict[str, Any]:
    """
    Retry a failed GitLab CI/CD job.
    
    Args:
        project_id: GitLab project ID or path
        job_id: ID of the job to retry
    
    Returns:
        Status of retry operation
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Retry the job
        result = await client.post(f"/projects/{project_id}/jobs/{job_id}/retry")
        
        return {
            "status": "success",
            "message": f"Job {job_id} has been retried",
            "new_job": {
                "id": result["id"],
                "name": result["name"],
                "status": result["status"],
                "web_url": result["web_url"]
            }
        }
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
    
@check_project_access
async def analyze_failed_jobs(
    project_id: str,
    pipeline_id: Optional[int] = None,
    suggest_fixes: bool = True
) -> Dict[str, Any]:
    """
    Analyze failed jobs in a pipeline and suggest fixes.
    
    Args:
        project_id: GitLab project ID or path
        pipeline_id: Pipeline ID to analyze (latest if not specified)
        suggest_fixes: Provide fix suggestions based on error patterns
    
    Returns:
        Analysis of failed jobs with suggestions
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Get failed jobs
        jobs_result = await list_pipeline_jobs(project_id, pipeline_id, scope="failed")
        
        if jobs_result["status"] != "success":
            return jobs_result
        
        failed_jobs = jobs_result["jobs"]
        analyses = []
        
        for job in failed_jobs:
            # Get job log
            log_result = await get_job_log(project_id, job["id"], lines=100)
            
            if log_result["status"] == "success":
                analysis = {
                    "job_name": job["name"],
                    "job_id": job["id"],
                    "stage": job["stage"],
                    "web_url": job["web_url"],
                    "error_patterns": log_result.get("error_patterns", [])
                }
                
                # Suggest fixes based on common patterns
                if suggest_fixes:
                    suggestions = []
                    log_lower = log_result["log"].lower()
                    
                    # Common error patterns and suggestions
                    if "permission denied" in log_lower:
                        suggestions.append("Check file/directory permissions")
                    if "command not found" in log_lower:
                        suggestions.append("Ensure required commands are installed in the CI image")
                    if "no such file or directory" in log_lower:
                        suggestions.append("Verify file paths and ensure artifacts are properly passed between stages")
                    if "timeout" in log_lower:
                        suggestions.append("Consider increasing timeout values or optimizing long-running operations")
                    if "out of memory" in log_lower:
                        suggestions.append("Increase memory limits for the job or optimize memory usage")
                    if "authentication" in log_lower or "unauthorized" in log_lower:
                        suggestions.append("Check credentials and authentication tokens")
                    if "npm" in log_lower and "error" in log_lower:
                        suggestions.append("Try clearing npm cache or updating dependencies")
                    if "docker" in log_lower and "error" in log_lower:
                        suggestions.append("Check Docker daemon status and Docker image availability")
                    if "test" in job["name"].lower() and "failed" in log_lower:
                        suggestions.append("Review test failures and update test cases or application code")
                    
                    analysis["suggestions"] = suggestions if suggestions else ["Review the job log for specific error details"]
                
                analyses.append(analysis)
        
        return {
            "status": "success",
            "pipeline_id": jobs_result["pipeline_id"],
            "failed_jobs_count": len(failed_jobs),
            "analyses": analyses,
            "summary": {
                "total_failed": len(failed_jobs),
                "stages_affected": list(set(job["stage"] for job in failed_jobs)),
                "common_issues": _identify_common_issues(analyses)
            }
        }
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

def _identify_common_issues(analyses: List[Dict]) -> List[str]:
    """Identify common issues across multiple job failures."""
    issue_counts = {}
    
    for analysis in analyses:
        for suggestion in analysis.get("suggestions", []):
            issue_counts[suggestion] = issue_counts.get(suggestion, 0) + 1
    
    # Return issues that appear in multiple jobs
    common = [issue for issue, count in issue_counts.items() if count > 1]
    return common if common else ["Various job-specific issues detected"]


@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def trigger_pipeline(
    project_id: str,
    ref: str = "main",
    variables: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Trigger a new pipeline run.
    
    Args:
        project_id: GitLab project ID or path
        ref: Branch, tag, or commit SHA to run pipeline on
        variables: Pipeline variables to set
    
    Returns:
        Details of the triggered pipeline
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        data = {"ref": ref}
        
        if variables:
            data["variables"] = [
                {"key": k, "value": v} for k, v in variables.items()
            ]
        
        result = await client.post(
            f"/projects/{project_id}/pipeline",
            data
        )
        
        return {
            "status": "success",
            "pipeline": {
                "id": result["id"],
                "status": result["status"],
                "ref": result["ref"],
                "web_url": result["web_url"],
                "created_at": result["created_at"]
            }
        }
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
    
@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def cancel_pipeline(
    project_id: str,
    pipeline_id: int
) -> Dict[str, Any]:
    """
    Cancel a running pipeline.
    Security: Protected as write operation.
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        if not project_id and config.default_project_id:
            project_id = config.default_project_id
            
        if config.verbose:
            logger.info(f"Cancelling pipeline {pipeline_id} in project {project_id}")
            
        result = await client.post(f"/projects/{project_id}/pipelines/{pipeline_id}/cancel")
        
        return {
            "status": "success",
            "pipeline_id": pipeline_id,
            "pipeline_status": result["status"],
            "message": f"Pipeline {pipeline_id} cancelled"
        }
    except Exception as e:
        logger.error(f"Failed to cancel pipeline: {str(e)}")
        return {"status": "error", "error": str(e)}

@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def retry_pipeline(
    project_id: str,
    pipeline_id: int
) -> Dict[str, Any]:
    """
    Retry a failed pipeline.
    Security: Protected as write operation.
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        if not project_id and config.default_project_id:
            project_id = config.default_project_id
            
        if config.verbose:
            logger.info(f"Retrying pipeline {pipeline_id} in project {project_id}")
            
        result = await client.post(f"/projects/{project_id}/pipelines/{pipeline_id}/retry")
        
        return {
            "status": "success",
            "pipeline_id": result["id"],
            "pipeline_status": result["status"],
            "web_url": result["web_url"],
            "message": f"Pipeline {pipeline_id} retried"
        }
    except Exception as e:
        logger.error(f"Failed to retry pipeline: {str(e)}")
        return {"status": "error", "error": str(e)}

# ============================================================================
# Cloud Integration Tools (AWS, Azure, GCP)
# ============================================================================

@check_project_access
@check_safe_mode("write")
@dry_run_mode
async def deploy_to_cloud(
    provider: str,
    project_id: str,
    environment: str,
    deployment_config: Dict[str, Any],
    require_approval: bool = True
) -> Dict[str, Any]:
    """
    Deploy GitLab project to cloud provider (AWS, Azure, or GCP).
    Security: Protected by safe_mode, requires explicit environment permissions.
    
    Args:
        provider: Cloud provider (aws, azure, gcp)
        project_id: GitLab project ID
        environment: Target environment (dev, staging, prod)
        deployment_config: Provider-specific deployment configuration
        require_approval: Require manual approval for prod deployments
    
    Returns:
        Deployment status and details
    """
    valid_providers = {"aws", "azure", "gcp"}
    valid_environments = {"dev", "staging", "prod"}
    
    provider = str(provider).lower().strip()
    environment = str(environment).lower().strip()
    
    if provider not in valid_providers:
        return {
            "status": "error",
            "error": f"Invalid provider '{provider}'. Must be one of: {sorted(valid_providers)}"
        }
    
    if environment not in valid_environments:
        return {
            "status": "error",
            "error": f"Invalid environment '{environment}'. Must be one of: {sorted(valid_environments)}"
        }
    
    if not isinstance(deployment_config, dict):
        return {
            "status": "error",
            "error": "deployment_config must be a dictionary"
        }
    
    if environment == "prod":
        if os.getenv("ALLOW_PROD_DEPLOY", "false").lower() != "true":
            return {
                "status": "error",
                "error": "Production deployments are disabled. Set ALLOW_PROD_DEPLOY=true in .env",
                "security": "prod_deploy_disabled"
            }
        
        logger.warning(f"PRODUCTION DEPLOYMENT requested for project {_sanitize_for_log(project_id)}")
        
        if require_approval:
            logger.info("Production deployment requires approval - setting REQUIRE_APPROVAL=true")
    
    if not project_id and config.default_project_id:
        project_id = config.default_project_id
    
    required_fields = {
        "aws": ["region", "service"],
        "azure": ["region", "service", "resource_group"],
        "gcp": ["region", "service", "project"]
    }
    
    missing_fields = []
    for field in required_fields.get(provider, []):
        if field not in deployment_config:
            missing_fields.append(field)
    
    if missing_fields:
        return {
            "status": "error",
            "error": f"Missing required fields for {provider}: {missing_fields}"
        }
    
    sensitive_keys = {"password", "secret", "key", "token", "credential", "auth"}
    safe_config = {}
    for k, v in deployment_config.items():
        k_lower = k.lower()
        if any(sensitive in k_lower for sensitive in sensitive_keys):
            safe_config[k] = "[REDACTED]"
        else:
            safe_config[k] = v
    
    cloud_vars = {
        "CLOUD_PROVIDER": provider,
        "ENVIRONMENT": environment,
        "DEPLOY_CONFIG": json.dumps(safe_config),
        "REQUIRE_APPROVAL": str(require_approval).lower(),
        "DEPLOY_TIMESTAMP": datetime.now().isoformat()
    }
    
    if provider == "aws":
        cloud_vars.update({
            "AWS_REGION": deployment_config.get("region", "us-east-1"),
            "AWS_SERVICE": deployment_config.get("service", "ecs"),
            "AWS_CLUSTER": deployment_config.get("cluster_name", "default")
        })
    elif provider == "azure":
        cloud_vars.update({
            "AZURE_REGION": deployment_config.get("region", "eastus"),
            "AZURE_SERVICE": deployment_config.get("service", "container-instances"),
            "AZURE_RESOURCE_GROUP": deployment_config.get("resource_group", "default")
        })
    elif provider == "gcp":
        cloud_vars.update({
            "GCP_REGION": deployment_config.get("region", "us-central1"),
            "GCP_SERVICE": deployment_config.get("service", "cloud-run"),
            "GCP_PROJECT": deployment_config.get("project", "default")
        })
    
    if config.verbose:
        logger.info(f"Deploying to {provider} ({environment}) for project")
    
    result = await trigger_pipeline(
        project_id=project_id,
        ref=deployment_config.get("branch", "main"),
        variables=cloud_vars
    )
    
    if result["status"] == "success":
        result["deployment_info"] = {
            "provider": provider,
            "environment": environment,
            "configuration": safe_config,
            "timestamp": cloud_vars["DEPLOY_TIMESTAMP"],
            "require_approval": require_approval,
            "pipeline_id": result.get("pipeline", {}).get("id")
        }
        
        if environment == "prod":
            result["warning"] = "Production deployment initiated. Monitor pipeline closely."
    
    return result

# ============================================================================
# Create MCP Servers
# ============================================================================

def create_gitlab_mcp_server_inprocess(verbose: bool = None):
    """
    Create an in-process GitLab MCP server.
    Perfect for use with Cursor/ChatGPT via local execution.
    
    Returns:
        InProcessMCPServer configured for GitLab operations
    """
    # Use verbose from config if not explicitly set
    if verbose is None:
        verbose = config.verbose
    
    # Log startup info if verbose
    if verbose:
        logger.info("Starting GitLab MCP Server (In-Process)")
        logger.info(f"Base URL: {config.base_url}")
        logger.info(f"Safe Mode: {config.safe_mode}")
        logger.info(f"Dry Run: {config.dry_run}")
        if config.allowed_projects:
            logger.info(f"Allowed Projects: {', '.join(config.allowed_projects)}")
        else:
            logger.info("Allowed Projects: All")
    
    
    tools = [
        # MR/PR Tools
        list_merge_requests,
        get_merge_request_details,
        create_merge_request,
        approve_merge_request,
        merge_merge_request,
        rebase_merge_request,
        
        # Search Tools
        search_code,
        
        # ADR Tools
        create_adr_document,
        commit_adr_to_gitlab,
        
        # Pipeline/Job Tools
        list_pipeline_jobs,
        get_job_log,
        retry_failed_job,
        analyze_failed_jobs,
        trigger_pipeline,
        cancel_pipeline,
        retry_pipeline,
        
        # Cloud Deployment
        deploy_to_cloud
    ]
    
    # Add LLM config if configured
    llm_config = None
    if config.llm_provider:
        llm_config = {
            "provider": config.llm_provider,
            "model": config.llm_model,
            "temperature": config.llm_temperature
        }
        if verbose:
            logger.info(f"LLM Provider: {config.llm_provider}")
            logger.info(f"LLM Model: {config.llm_model}")
    
    return expose_tools_inprocess(tools, verbose=verbose, llm_config=llm_config)

def create_gitlab_mcp_server_http(
    host: str = None,
    port: int = None,
    verbose: bool = None
):
    """
    Create an HTTP GitLab MCP server.
    Can be accessed remotely via HTTP API.
    
    Returns:
        FastAPI app configured for GitLab operations
    """
    # Use config values if not explicitly set
    if host is None:
        host = config.host
    if port is None:
        port = config.port
    if verbose is None:
        verbose = config.verbose
    
    # Log startup info if verbose
    if verbose:
        logger.info(f"Starting GitLab MCP Server (HTTP) on {host}:{port}")
        logger.info(f"Base URL: {config.base_url}")
        logger.info(f"Safe Mode: {config.safe_mode}")
        logger.info(f"Dry Run: {config.dry_run}")
        if config.allowed_projects:
            logger.info(f"Allowed Projects: {', '.join(config.allowed_projects)}")
        else:
            logger.info("Allowed Projects: All")
    
    tools = [
        # MR/PR Tools
        list_merge_requests,
        get_merge_request_details,
        create_merge_request,  
        approve_merge_request,
        merge_merge_request,
        rebase_merge_request,  
        
        # Search Tools
        search_code,  
        
        # ADR Tools
        create_adr_document,
        commit_adr_to_gitlab,
        
        # Pipeline/Job Tools
        list_pipeline_jobs,
        get_job_log,
        retry_failed_job,
        analyze_failed_jobs,
        trigger_pipeline,
        cancel_pipeline,  
        retry_pipeline,  
        
        # Cloud Deployment
        deploy_to_cloud
    ]
    
    # Add LLM config if configured
    llm_config = None
    if config.llm_provider:
        llm_config = {
            "provider": config.llm_provider,
            "model": config.llm_model,
            "temperature": config.llm_temperature
        }
        if verbose:
            logger.info(f"LLM Provider: {config.llm_provider}")
            logger.info(f"LLM Model: {config.llm_model}")
    
    app = expose_tools_http(
        tools,
        title="GitLab MCP Server",
        description="MCP server for GitLab operations including MR processing, ADR creation, and pipeline management",
        version="1.0.0",
        verbose=verbose,
        llm_config=llm_config
    )
    
    # Add health check endpoint
    try:
        from fastapi import FastAPI
        if hasattr(app, 'get'):  # Check if it's a FastAPI app
            @app.get("/health")
            async def health_check():
                return {
                    "status": "healthy",
                    "safe_mode": config.safe_mode,
                    "dry_run": config.dry_run,
                    "timestamp": datetime.now().isoformat(),
                    "version": "1.0.0"
                }
    except ImportError:
        pass
    
    return app

# ============================================================================
# Main entry points
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    
    # Load .env file
    load_dotenv()
    
    # Re-initialize config after loading .env
    config = GitLabConfig()
    
    # For HTTP server - use config values
    app = create_gitlab_mcp_server_http()
    
    print(f"Starting GitLab MCP Server on {config.host}:{config.port}")
    print(f"Safe Mode: {config.safe_mode}")
    print(f"Dry Run: {config.dry_run}")
    
    uvicorn.run(app, host=config.host, port=config.port)
