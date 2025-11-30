"""
GitLab Assistant for ChatGPT
HTTP client for interacting with GitLab MCP Server.
Upload this file to ChatGPT Code Interpreter or use with Custom GPTs.
"""

import requests
import json
from typing import Dict, Any, Optional, List


class GitLabAssistant:
    """
    GitLab MCP client for ChatGPT integration.
    
    This class provides a simple interface to interact with the GitLab MCP Server.
    Can be used with ChatGPT Code Interpreter or as part of a Custom GPT.
    
    Example:
        >>> assistant = GitLabAssistant("http://localhost:8000")
        >>> mrs = assistant.list_merge_requests("mygroup/myproject")
        >>> print(mrs)
    """
    
    def __init__(self, server_url: str = "http://localhost:8000"):
        """
        Initialize the GitLab assistant.
        
        Args:
            server_url: URL of the GitLab MCP server.
        """
        self.server_url = server_url.rstrip("/")
        self.mcp_endpoint = f"{self.server_url}/mcp"
    
    def _invoke(self, tool_name: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Invoke an MCP tool.
        
        Args:
            tool_name: Name of the tool to invoke.
            params: Parameters to pass to the tool.
        
        Returns:
            Tool execution result.
        
        Raises:
            Exception: If the request fails.
        """
        url = f"{self.mcp_endpoint}/invoke/{tool_name}"
        
        try:
            response = requests.post(
                url,
                json=params or {},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                "status": "error",
                "error": f"Request failed: {str(e)}"
            }
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """
        List all available tools on the server.
        
        Returns:
            List of tool definitions with names and descriptions.
        """
        try:
            response = requests.get(f"{self.mcp_endpoint}/list_tools", timeout=10)
            response.raise_for_status()
            return response.json().get("tools", [])
        except requests.exceptions.RequestException as e:
            return [{"error": str(e)}]
    
    def health_check(self) -> Dict[str, Any]:
        """
        Check if the server is running.
        
        Returns:
            Server health status.
        """
        try:
            response = requests.get(f"{self.server_url}/health", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status": "unhealthy", "error": str(e)}
    
    # Merge Request Methods
    
    def list_merge_requests(
        self,
        project_id: str,
        state: str = "opened",
        author: str = None
    ) -> Dict[str, Any]:
        """
        List merge requests for a project.
        
        Args:
            project_id: GitLab project ID or path (e.g., "group/project").
            state: Filter by state (opened, closed, merged, all).
            author: Filter by author username.
        
        Returns:
            Dictionary with merge request list.
        """
        params = {
            "project_id": project_id,
            "state": state
        }
        if author:
            params["author_username"] = author
        
        return self._invoke("list_merge_requests", params)
    
    def get_merge_request_details(
        self,
        project_id: str,
        mr_iid: int,
        include_changes: bool = False,
        include_discussions: bool = False
    ) -> Dict[str, Any]:
        """
        Get detailed information about a merge request.
        
        Args:
            project_id: GitLab project ID or path.
            mr_iid: Merge request internal ID.
            include_changes: Include file diffs.
            include_discussions: Include comments.
        
        Returns:
            Merge request details.
        """
        return self._invoke("get_merge_request_details", {
            "project_id": project_id,
            "mr_iid": mr_iid,
            "include_changes": include_changes,
            "include_discussions": include_discussions
        })
    
    def approve_merge_request(
        self,
        project_id: str,
        mr_iid: int,
        comment: str = None
    ) -> Dict[str, Any]:
        """
        Approve a merge request.
        
        Args:
            project_id: GitLab project ID or path.
            mr_iid: Merge request internal ID.
            comment: Optional approval comment.
        
        Returns:
            Approval status.
        """
        params = {
            "project_id": project_id,
            "mr_iid": mr_iid
        }
        if comment:
            params["approval_comment"] = comment
        
        return self._invoke("approve_merge_request", params)
    
    def merge_merge_request(
        self,
        project_id: str,
        mr_iid: int,
        squash: bool = False,
        remove_source_branch: bool = True,
        message: str = None
    ) -> Dict[str, Any]:
        """
        Merge a merge request.
        
        Args:
            project_id: GitLab project ID or path.
            mr_iid: Merge request internal ID.
            squash: Squash commits when merging.
            remove_source_branch: Delete source branch after merge.
            message: Custom merge commit message.
        
        Returns:
            Merge status.
        """
        params = {
            "project_id": project_id,
            "mr_iid": mr_iid,
            "squash": squash,
            "remove_source_branch": remove_source_branch
        }
        if message:
            params["merge_commit_message"] = message
        
        return self._invoke("merge_merge_request", params)
    
    # Pipeline Methods
    
    def list_pipeline_jobs(
        self,
        project_id: str,
        pipeline_id: int = None
    ) -> Dict[str, Any]:
        """
        List jobs in a pipeline.
        
        Args:
            project_id: GitLab project ID or path.
            pipeline_id: Specific pipeline ID. Uses latest if not provided.
        
        Returns:
            Job list with status summary.
        """
        params = {"project_id": project_id}
        if pipeline_id:
            params["pipeline_id"] = pipeline_id
        
        return self._invoke("list_pipeline_jobs", params)
    
    def analyze_failed_jobs(
        self,
        project_id: str,
        pipeline_id: int = None
    ) -> Dict[str, Any]:
        """
        Analyze failed jobs and get fix suggestions.
        
        Args:
            project_id: GitLab project ID or path.
            pipeline_id: Specific pipeline ID. Uses latest if not provided.
        
        Returns:
            Analysis with error patterns and fix suggestions.
        """
        params = {
            "project_id": project_id,
            "suggest_fixes": True
        }
        if pipeline_id:
            params["pipeline_id"] = pipeline_id
        
        return self._invoke("analyze_failed_jobs", params)
    
    def get_job_log(
        self,
        project_id: str,
        job_id: int,
        lines: int = None
    ) -> Dict[str, Any]:
        """
        Get job log output.
        
        Args:
            project_id: GitLab project ID or path.
            job_id: Job ID.
            lines: Number of lines to return.
        
        Returns:
            Job log content.
        """
        params = {
            "project_id": project_id,
            "job_id": job_id
        }
        if lines:
            params["lines"] = lines
        
        return self._invoke("get_job_log", params)
    
    def retry_failed_job(
        self,
        project_id: str,
        job_id: int
    ) -> Dict[str, Any]:
        """
        Retry a failed job.
        
        Args:
            project_id: GitLab project ID or path.
            job_id: Job ID to retry.
        
        Returns:
            Retry status with new job details.
        """
        return self._invoke("retry_failed_job", {
            "project_id": project_id,
            "job_id": job_id
        })
    
    def trigger_pipeline(
        self,
        project_id: str,
        ref: str = "main",
        variables: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Trigger a new pipeline.
        
        Args:
            project_id: GitLab project ID or path.
            ref: Branch, tag, or commit SHA.
            variables: Pipeline variables.
        
        Returns:
            New pipeline details.
        """
        params = {
            "project_id": project_id,
            "ref": ref
        }
        if variables:
            params["variables"] = variables
        
        return self._invoke("trigger_pipeline", params)
    
    # ADR Methods
    
    def create_adr(
        self,
        title: str,
        context: str,
        decision: str,
        consequences: str,
        status: str = "proposed",
        alternatives: str = None
    ) -> Dict[str, Any]:
        """
        Create an Architecture Decision Record.
        
        Args:
            title: ADR title.
            context: Problem context.
            decision: The decision made.
            consequences: Positive and negative consequences.
            status: Decision status.
            alternatives: Alternatives considered.
        
        Returns:
            ADR document in markdown format.
        """
        params = {
            "title": title,
            "status": status,
            "context": context,
            "decision": decision,
            "consequences": consequences
        }
        if alternatives:
            params["alternatives"] = alternatives
        
        return self._invoke("create_adr_document", params)
    
    def commit_adr(
        self,
        project_id: str,
        adr_content: str,
        adr_title: str,
        branch: str = "main",
        create_mr: bool = True
    ) -> Dict[str, Any]:
        """
        Commit an ADR document to GitLab.
        
        Args:
            project_id: GitLab project ID or path.
            adr_content: The ADR markdown content.
            adr_title: Title for the ADR.
            branch: Target branch.
            create_mr: Create a merge request.
        
        Returns:
            Commit and MR details.
        """
        return self._invoke("commit_adr_to_gitlab", {
            "project_id": project_id,
            "adr_content": adr_content,
            "adr_title": adr_title,
            "branch": branch,
            "create_mr": create_mr
        })


def format_merge_requests(result: Dict[str, Any]) -> str:
    """
    Format merge request list as readable text.
    
    Args:
        result: Result from list_merge_requests.
    
    Returns:
        Formatted string.
    """
    if result.get("status") == "error":
        return f"Error: {result.get('error')}"
    
    mrs = result.get("merge_requests", [])
    if not mrs:
        return "No merge requests found."
    
    lines = [f"Found {len(mrs)} merge request(s):\n"]
    
    for mr in mrs:
        lines.append(f"  MR !{mr['iid']} - {mr['title']}")
        lines.append(f"    Author: {mr['author']}")
        lines.append(f"    Branch: {mr['source_branch']} -> {mr['target_branch']}")
        lines.append(f"    State: {mr['state']}")
        lines.append(f"    URL: {mr['web_url']}")
        lines.append("")
    
    return "\n".join(lines)


def format_pipeline_analysis(result: Dict[str, Any]) -> str:
    """
    Format pipeline analysis as readable text.
    
    Args:
        result: Result from analyze_failed_jobs.
    
    Returns:
        Formatted string.
    """
    if result.get("status") == "error":
        return f"Error: {result.get('error')}"
    
    analyses = result.get("analyses", [])
    if not analyses:
        return "No failed jobs found."
    
    lines = [f"Pipeline #{result.get('pipeline_id')} - {len(analyses)} failed job(s):\n"]
    
    for analysis in analyses:
        lines.append(f"  Job: {analysis['job_name']} (stage: {analysis['stage']})")
        lines.append(f"    URL: {analysis['web_url']}")
        
        errors = analysis.get("error_patterns", [])
        if errors:
            lines.append("    Errors:")
            for error in errors[:3]:
                lines.append(f"      - {error}")
        
        suggestions = analysis.get("suggestions", [])
        if suggestions:
            lines.append("    Suggestions:")
            for suggestion in suggestions:
                lines.append(f"      - {suggestion}")
        
        lines.append("")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print("GitLab Assistant")
    print("=" * 40)
    
    assistant = GitLabAssistant("http://localhost:8000")
    
    health = assistant.health_check()
    print(f"Server status: {health.get('status', 'unknown')}")
    
    if health.get("status") == "healthy":
        tools = assistant.list_tools()
        print(f"\nAvailable tools: {len(tools)}")
        for tool in tools:
            name = tool.get('name', 'unknown')
            desc = tool.get('description', 'No description')[:50]
            print(f"  - {name}: {desc}")
    else:
        print(f"Server error: {health.get('error', 'Unknown error')}")
        print("\nMake sure the server is running:")
        print("  python gitlab_mcp_server.py --http --port 8000")
    
    print("\n" + "=" * 40)
    print("Usage with ChatGPT:")
    print("  assistant = GitLabAssistant('http://your-server:8000')")
    print("  assistant.list_merge_requests('group/project')")
    print("  assistant.analyze_failed_jobs('group/project')")
