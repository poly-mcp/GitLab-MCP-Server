"""
GitLab MCP Server
Complete MCP server for GitLab operations including PR/MR processing,
ADR document creation, and pipeline job management.
"""

import os
import json
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
import aiohttp
import yaml
from enum import Enum

# Import your polymcp library
from polymcp import expose_tools_inprocess, expose_tools_http

# ============================================================================
# Configuration
# ============================================================================

class GitLabConfig:
    """GitLab configuration management."""
    
    def __init__(self):
        self.base_url = os.getenv("GITLAB_BASE_URL", "https://gitlab.com/api/v4")
        self.token = os.getenv("GITLAB_TOKEN", "")
        self.default_project_id = os.getenv("GITLAB_PROJECT_ID", "")
        
    @property
    def headers(self) -> Dict[str, str]:
        """Get headers for GitLab API requests."""
        return {
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json"
        }

config = GitLabConfig()

# ============================================================================
# GitLab API Client
# ============================================================================

class GitLabClient:
    """Async GitLab API client."""
    
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.headers = {"PRIVATE-TOKEN": token}
        
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Make async request to GitLab API."""
        url = f"{self.base_url}{endpoint}"
        kwargs['headers'] = {**self.headers, **kwargs.get('headers', {})}
        
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                return await response.json()
    
    async def get(self, endpoint: str, params: Dict = None) -> Dict:
        """GET request."""
        return await self._request("GET", endpoint, params=params)
    
    async def post(self, endpoint: str, json_data: Dict = None) -> Dict:
        """POST request."""
        return await self._request("POST", endpoint, json=json_data)
    
    async def put(self, endpoint: str, json_data: Dict = None) -> Dict:
        """PUT request."""
        return await self._request("PUT", endpoint, json=json_data)

# ============================================================================
# Merge Request Tools
# ============================================================================

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
    
    params = {
        "state": state,
        "scope": scope,
        "per_page": 100
    }
    
    if author_username:
        params["author_username"] = author_username
    if assignee_username:
        params["assignee_username"] = assignee_username
    
    try:
        mrs = await client.get(f"/projects/{project_id}/merge_requests", params)
        
        # Format the response
        formatted_mrs = []
        for mr in mrs:
            formatted_mrs.append({
                "id": mr["id"],
                "iid": mr["iid"],
                "title": mr["title"],
                "description": mr.get("description", ""),
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
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

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
        return {
            "status": "error",
            "error": str(e)
        }

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
        return {
            "status": "error",
            "error": str(e)
        }

# ============================================================================
# ADR (Architecture Decision Record) Tools
# ============================================================================

def create_adr_document(
    title: str,
    status: str,
    context: str,
    decision: str,
    consequences: str,
    alternatives: Optional[str] = None,
    related_decisions: Optional[List[str]] = None,
    participants: Optional[List[str]] = None,
    save_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create an Architecture Decision Record (ADR) document in Markdown format.
    
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
    
    Returns:
        Dictionary with ADR content and save status
    """
    # Generate ADR number based on existing files
    adr_number = datetime.now().strftime("%Y%m%d")
    
    # Build ADR content
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
        for decision in related_decisions:
            adr_content += f"- {decision}\n"
    
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
    if save_path:
        try:
            file_path = Path(save_path) / f"ADR-{adr_number}-{title.lower().replace(' ', '-')}.md"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(adr_content)
            result["file_path"] = str(file_path)
            result["message"] = f"ADR saved to {file_path}"
        except Exception as e:
            result["save_error"] = str(e)
    
    return result

async def commit_adr_to_gitlab(
    project_id: str,
    adr_content: str,
    adr_title: str,
    branch: str = "main",
    create_mr: bool = True
) -> Dict[str, Any]:
    """
    Commit an ADR document to a GitLab repository.
    
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
        # Generate file path
        adr_number = datetime.now().strftime("%Y%m%d")
        file_name = f"docs/adr/ADR-{adr_number}-{adr_title.lower().replace(' ', '-')}.md"
        
        # Create or update file
        commit_data = {
            "branch": branch if not create_mr else f"adr-{adr_number}",
            "commit_message": f"Add ADR: {adr_title}",
            "content": adr_content
        }
        
        # If creating MR, first create a new branch
        if create_mr:
            commit_data["start_branch"] = branch
        
        # Commit the file
        result = await client.post(
            f"/projects/{project_id}/repository/files/{file_name}",
            commit_data
        )
        
        response = {
            "status": "success",
            "file_path": file_name,
            "branch": commit_data["branch"],
            "commit": result
        }
        
        # Create MR if requested
        if create_mr:
            mr_data = {
                "source_branch": commit_data["branch"],
                "target_branch": branch,
                "title": f"ADR: {adr_title}",
                "description": f"This MR adds a new Architecture Decision Record.\n\n{adr_content[:500]}...",
                "remove_source_branch": True
            }
            
            mr = await client.post(
                f"/projects/{project_id}/merge_requests",
                mr_data
            )
            response["merge_request"] = mr
        
        return response
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

# ============================================================================
# Pipeline and Job Management Tools
# ============================================================================

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
        lines: Number of lines to return (all if not specified)
    
    Returns:
        Job log content
    """
    client = GitLabClient(config.base_url, config.token)
    
    try:
        # Get job details first
        job = await client.get(f"/projects/{project_id}/jobs/{job_id}")
        
        # Get job log
        log_url = f"/projects/{project_id}/jobs/{job_id}/trace"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.base_url}{log_url}",
                headers=client.headers
            ) as response:
                log_content = await response.text()
        
        # Process log if lines specified
        if lines and log_content:
            log_lines = log_content.split('\n')
            log_content = '\n'.join(log_lines[-lines:])
        
        # Try to identify error patterns
        error_patterns = []
        if "error" in log_content.lower():
            for line in log_content.split('\n'):
                if "error" in line.lower():
                    error_patterns.append(line.strip())
        
        return {
            "status": "success",
            "job": {
                "id": job["id"],
                "name": job["name"],
                "status": job["status"]
            },
            "log": log_content,
            "log_size": len(log_content),
            "error_patterns": error_patterns[:10]  # First 10 error patterns
        }
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

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

# ============================================================================
# Cloud Integration Tools (AWS, Azure, GCP)
# ============================================================================

async def deploy_to_cloud(
    provider: str,
    project_id: str,
    environment: str,
    deployment_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Deploy GitLab project to cloud provider (AWS, Azure, or GCP).
    
    Args:
        provider: Cloud provider (aws, azure, gcp)
        project_id: GitLab project ID
        environment: Target environment (dev, staging, prod)
        deployment_config: Provider-specific deployment configuration
    
    Returns:
        Deployment status and details
    """
    # This would integrate with cloud provider SDKs
    # For now, we'll create a GitLab CI/CD pipeline with cloud deployment
    
    provider = provider.lower()
    
    # Define cloud-specific variables
    cloud_vars = {
        "CLOUD_PROVIDER": provider,
        "ENVIRONMENT": environment,
        "DEPLOY_CONFIG": json.dumps(deployment_config)
    }
    
    if provider == "aws":
        cloud_vars.update({
            "AWS_REGION": deployment_config.get("region", "us-east-1"),
            "AWS_SERVICE": deployment_config.get("service", "ecs")
        })
    elif provider == "azure":
        cloud_vars.update({
            "AZURE_REGION": deployment_config.get("region", "eastus"),
            "AZURE_SERVICE": deployment_config.get("service", "container-instances")
        })
    elif provider == "gcp":
        cloud_vars.update({
            "GCP_REGION": deployment_config.get("region", "us-central1"),
            "GCP_SERVICE": deployment_config.get("service", "cloud-run")
        })
    
    # Trigger deployment pipeline
    result = await trigger_pipeline(
        project_id=project_id,
        ref=deployment_config.get("branch", "main"),
        variables=cloud_vars
    )
    
    if result["status"] == "success":
        result["deployment_info"] = {
            "provider": provider,
            "environment": environment,
            "configuration": deployment_config
        }
    
    return result

# ============================================================================
# Create MCP Servers
# ============================================================================

def create_gitlab_mcp_server_inprocess(verbose: bool = False):
    """
    Create an in-process GitLab MCP server.
    Perfect for use with Cursor/ChatGPT via local execution.
    
    Returns:
        InProcessMCPServer configured for GitLab operations
    """
    tools = [
        # MR/PR Tools
        list_merge_requests,
        get_merge_request_details,
        approve_merge_request,
        merge_merge_request,
        
        # ADR Tools
        create_adr_document,
        commit_adr_to_gitlab,
        
        # Pipeline/Job Tools
        list_pipeline_jobs,
        get_job_log,
        retry_failed_job,
        analyze_failed_jobs,
        trigger_pipeline,
        
        # Cloud Deployment
        deploy_to_cloud
    ]
    
    return expose_tools_inprocess(tools, verbose=verbose)

def create_gitlab_mcp_server_http(
    host: str = "0.0.0.0",
    port: int = 8000,
    verbose: bool = False
):
    """
    Create an HTTP GitLab MCP server.
    Can be accessed remotely via HTTP API.
    
    Returns:
        FastAPI app configured for GitLab operations
    """
    tools = [
        # MR/PR Tools
        list_merge_requests,
        get_merge_request_details,
        approve_merge_request,
        merge_merge_request,
        
        # ADR Tools
        create_adr_document,
        commit_adr_to_gitlab,
        
        # Pipeline/Job Tools
        list_pipeline_jobs,
        get_job_log,
        retry_failed_job,
        analyze_failed_jobs,
        trigger_pipeline,
        
        # Cloud Deployment
        deploy_to_cloud
    ]
    
    return expose_tools_http(
        tools,
        title="GitLab MCP Server",
        description="MCP server for GitLab operations including MR processing, ADR creation, and pipeline management",
        version="1.0.0",
        verbose=verbose
    )

# ============================================================================
# Main entry points
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    # For HTTP server
    app = create_gitlab_mcp_server_http(verbose=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)
