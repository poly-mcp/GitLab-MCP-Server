"""
GitLab Tools for Cursor IDE
Wrapper functions for easy integration with Cursor.
Compatible with polymcp and GitLab MCP Server.
"""

import os
import asyncio
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import the MCP server
from gitlab_mcp_server import create_gitlab_mcp_server_inprocess

# Global server instance
_server = None

def _get_server():
    """Get or create the server instance."""
    global _server
    if _server is None:
        _server = create_gitlab_mcp_server_inprocess(verbose=False)
    return _server

def _run_async(coro):
    """Run async function in sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

def _get_project_id(project_id: Optional[str] = None) -> str:
    """Get project ID from argument or environment."""
    pid = project_id or os.getenv("GITLAB_PROJECT_ID", "")
    if not pid:
        raise ValueError("project_id is required. Set GITLAB_PROJECT_ID in .env or pass it as argument.")
    return pid


# Merge Request Functions

def list_open_merge_requests(project_id: str = None) -> Dict[str, Any]:
    """
    List all open merge requests for a project.
    
    Args:
        project_id: GitLab project ID or path. Uses GITLAB_PROJECT_ID env var if not provided.
    
    Returns:
        Dictionary with merge request list and count.
    
    Example:
        >>> mrs = list_open_merge_requests("mygroup/myproject")
        >>> print(f"Found {mrs['count']} open MRs")
    """
    server = _get_server()
    return _run_async(server.invoke(
        "list_merge_requests",
        {"project_id": _get_project_id(project_id), "state": "opened"}
    ))

def list_all_merge_requests(
    project_id: str = None,
    state: str = "all",
    author: str = None
) -> Dict[str, Any]:
    """
    List merge requests with filters.
    
    Args:
        project_id: GitLab project ID or path.
        state: Filter by state (opened, closed, merged, all).
        author: Filter by author username.
    
    Returns:
        Dictionary with filtered merge request list.
    """
    server = _get_server()
    params = {
        "project_id": _get_project_id(project_id),
        "state": state
    }
    if author:
        params["author_username"] = author
    
    return _run_async(server.invoke("list_merge_requests", params))

def get_merge_request(
    mr_iid: int,
    project_id: str = None,
    include_changes: bool = False,
    include_discussions: bool = False
) -> Dict[str, Any]:
    """
    Get detailed information about a specific merge request.
    
    Args:
        mr_iid: Merge request internal ID (the number shown in GitLab).
        project_id: GitLab project ID or path.
        include_changes: Include file diffs.
        include_discussions: Include comments and discussions.
    
    Returns:
        Dictionary with merge request details.
    
    Example:
        >>> mr = get_merge_request(42, include_changes=True)
        >>> print(mr['merge_request']['title'])
    """
    server = _get_server()
    return _run_async(server.invoke(
        "get_merge_request_details",
        {
            "project_id": _get_project_id(project_id),
            "mr_iid": mr_iid,
            "include_changes": include_changes,
            "include_discussions": include_discussions
        }
    ))

def approve_merge_request(
    mr_iid: int,
    project_id: str = None,
    comment: str = None
) -> Dict[str, Any]:
    """
    Approve a merge request.
    
    Args:
        mr_iid: Merge request internal ID.
        project_id: GitLab project ID or path.
        comment: Optional approval comment.
    
    Returns:
        Approval status.
    """
    server = _get_server()
    params = {
        "project_id": _get_project_id(project_id),
        "mr_iid": mr_iid
    }
    if comment:
        params["approval_comment"] = comment
    
    return _run_async(server.invoke("approve_merge_request", params))

def merge_merge_request(
    mr_iid: int,
    project_id: str = None,
    squash: bool = False,
    remove_source_branch: bool = True,
    message: str = None
) -> Dict[str, Any]:
    """
    Merge a merge request.
    
    Args:
        mr_iid: Merge request internal ID.
        project_id: GitLab project ID or path.
        squash: Squash commits when merging.
        remove_source_branch: Delete source branch after merge.
        message: Custom merge commit message.
    
    Returns:
        Merge status.
    """
    server = _get_server()
    params = {
        "project_id": _get_project_id(project_id),
        "mr_iid": mr_iid,
        "squash": squash,
        "remove_source_branch": remove_source_branch
    }
    if message:
        params["merge_commit_message"] = message
    
    return _run_async(server.invoke("merge_merge_request", params))


# Pipeline and Job Functions

def list_pipeline_jobs(project_id: str = None, pipeline_id: int = None) -> Dict[str, Any]:
    """
    List jobs in a pipeline.
    
    Args:
        project_id: GitLab project ID or path.
        pipeline_id: Specific pipeline ID. Uses latest if not provided.
    
    Returns:
        Dictionary with job list grouped by status.
    
    Example:
        >>> jobs = list_pipeline_jobs()
        >>> print(f"Failed: {jobs['summary']['failed']}, Success: {jobs['summary']['success']}")
    """
    server = _get_server()
    params = {"project_id": _get_project_id(project_id)}
    if pipeline_id:
        params["pipeline_id"] = pipeline_id
    
    return _run_async(server.invoke("list_pipeline_jobs", params))

def analyze_pipeline_failures(project_id: str = None, pipeline_id: int = None) -> Dict[str, Any]:
    """
    Analyze failed jobs in a pipeline and get fix suggestions.
    
    Args:
        project_id: GitLab project ID or path.
        pipeline_id: Specific pipeline ID. Uses latest if not provided.
    
    Returns:
        Dictionary with failure analysis and fix suggestions.
    
    Example:
        >>> analysis = analyze_pipeline_failures()
        >>> for job in analysis['analyses']:
        ...     print(f"{job['job_name']}: {job['suggestions']}")
    """
    server = _get_server()
    params = {
        "project_id": _get_project_id(project_id),
        "suggest_fixes": True
    }
    if pipeline_id:
        params["pipeline_id"] = pipeline_id
    
    return _run_async(server.invoke("analyze_failed_jobs", params))

def get_job_log(job_id: int, project_id: str = None, lines: int = None) -> Dict[str, Any]:
    """
    Get the log output of a specific job.
    
    Args:
        job_id: The job ID.
        project_id: GitLab project ID or path.
        lines: Number of lines to return. Returns all if not specified.
    
    Returns:
        Dictionary with job info and log content.
    """
    server = _get_server()
    params = {
        "project_id": _get_project_id(project_id),
        "job_id": job_id
    }
    if lines:
        params["lines"] = lines
    
    return _run_async(server.invoke("get_job_log", params))

def retry_job(job_id: int, project_id: str = None) -> Dict[str, Any]:
    """
    Retry a failed job.
    
    Args:
        job_id: The job ID to retry.
        project_id: GitLab project ID or path.
    
    Returns:
        Status of the retry operation with new job details.
    """
    server = _get_server()
    return _run_async(server.invoke(
        "retry_failed_job",
        {
            "project_id": _get_project_id(project_id),
            "job_id": job_id
        }
    ))

def trigger_pipeline(
    project_id: str = None,
    ref: str = "main",
    variables: Dict[str, str] = None
) -> Dict[str, Any]:
    """
    Trigger a new pipeline run.
    
    Args:
        project_id: GitLab project ID or path.
        ref: Branch, tag, or commit to run pipeline on.
        variables: Pipeline variables to set.
    
    Returns:
        Details of the triggered pipeline.
    """
    server = _get_server()
    params = {
        "project_id": _get_project_id(project_id),
        "ref": ref
    }
    if variables:
        params["variables"] = variables
    
    return _run_async(server.invoke("trigger_pipeline", params))


# ADR Functions

def create_architecture_decision(
    title: str,
    context: str,
    decision: str,
    consequences: str,
    status: str = "proposed",
    alternatives: str = None,
    participants: List[str] = None
) -> Dict[str, Any]:
    """
    Create an Architecture Decision Record document.
    
    Args:
        title: Title of the ADR.
        context: Context and problem statement.
        decision: The decision that was made.
        consequences: Positive and negative consequences.
        status: Decision status (proposed, accepted, deprecated, superseded).
        alternatives: Alternative solutions that were considered.
        participants: List of people involved in the decision.
    
    Returns:
        Dictionary with ADR content in markdown format.
    
    Example:
        >>> adr = create_architecture_decision(
        ...     title="Use PostgreSQL for primary database",
        ...     context="We need a reliable relational database",
        ...     decision="We will use PostgreSQL 15",
        ...     consequences="Good performance, need DBA expertise"
        ... )
        >>> print(adr['content'])
    """
    server = _get_server()
    params = {
        "title": title,
        "status": status,
        "context": context,
        "decision": decision,
        "consequences": consequences
    }
    if alternatives:
        params["alternatives"] = alternatives
    if participants:
        params["participants"] = participants
    
    return _run_async(server.invoke("create_adr_document", params))

def commit_adr(
    project_id: str,
    adr_content: str,
    adr_title: str,
    branch: str = "main",
    create_mr: bool = True
) -> Dict[str, Any]:
    """
    Commit an ADR document to a GitLab repository.
    
    Args:
        project_id: GitLab project ID or path.
        adr_content: The ADR markdown content.
        adr_title: Title for the ADR.
        branch: Target branch for the commit.
        create_mr: Create a merge request for the ADR.
    
    Returns:
        Commit and MR details.
    """
    server = _get_server()
    return _run_async(server.invoke(
        "commit_adr_to_gitlab",
        {
            "project_id": _get_project_id(project_id),
            "adr_content": adr_content,
            "adr_title": adr_title,
            "branch": branch,
            "create_mr": create_mr
        }
    ))


# Utility Functions

def get_server_stats() -> Dict[str, Any]:
    """
    Get server execution statistics.
    
    Returns:
        Dictionary with execution count, error count, and success rate.
    """
    server = _get_server()
    return server.get_stats()

def list_available_tools() -> List[str]:
    """
    List all available GitLab tools.
    
    Returns:
        List of tool names.
    """
    server = _get_server()
    result = _run_async(server.list_tools())
    return [tool['name'] for tool in result['tools']]

def show_help():
    """Print available commands and usage examples."""
    print("""
GitLab Tools for Cursor
=======================

Merge Request Commands:
  list_open_merge_requests()           - List open MRs
  list_all_merge_requests(state='all') - List MRs with filters
  get_merge_request(mr_iid)            - Get MR details
  approve_merge_request(mr_iid)        - Approve an MR
  merge_merge_request(mr_iid)          - Merge an MR

Pipeline Commands:
  list_pipeline_jobs()                 - List jobs in latest pipeline
  analyze_pipeline_failures()          - Analyze failed jobs with suggestions
  get_job_log(job_id)                  - Get job log output
  retry_job(job_id)                    - Retry a failed job
  trigger_pipeline(ref='main')         - Trigger new pipeline

ADR Commands:
  create_architecture_decision(...)    - Create ADR document
  commit_adr(...)                       - Commit ADR to GitLab

Utility Commands:
  list_available_tools()               - List all tools
  get_server_stats()                   - Get execution stats
  show_help()                          - Show this help

Configuration:
  Set GITLAB_TOKEN and GITLAB_PROJECT_ID in .env file.

Examples:
  >>> mrs = list_open_merge_requests()
  >>> analysis = analyze_pipeline_failures()
  >>> adr = create_architecture_decision(
  ...     title="Use Redis for caching",
  ...     context="Need faster response times",
  ...     decision="Implement Redis caching layer",
  ...     consequences="Added infrastructure complexity"
  ... )
""")


# Validate configuration on import
_token = os.getenv("GITLAB_TOKEN")
if not _token:
    print("[GitLab Tools] WARNING: GITLAB_TOKEN not set in .env file")
else:
    print("[GitLab Tools] Ready. Type show_help() for available commands")
