# GitLab MCP Server

![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)
![License MIT](https://img.shields.io/badge/license-MIT-green.svg)
![MCP Compatible](https://img.shields.io/badge/MCP-compatible-purple.svg)

An MCP (Model Context Protocol) server for integrating GitLab with AI assistants like Cursor, ChatGPT, and any polymcp-compatible client. Manage merge requests, analyze CI/CD pipelines, create ADR documents, and more.

## What it does

This project exposes GitLab APIs through the MCP protocol, allowing AI assistants to:

- List and manage merge requests
- Analyze failed pipeline jobs with fix suggestions
- Create ADR (Architecture Decision Records) documents in markdown
- View CI/CD job logs
- Trigger pipelines and retry failed jobs
- Deploy to AWS, Azure, and GCP

## Requirements

- Python 3.8 or higher
- PolyMCP (for AI agent integration)
- FastAPI and uvicorn (for HTTP server)

## Installation

```bash
git clone https://github.com/poly-mcp/Gitlab-MCP-Server.git
cd Gitlab-MCP-Server

pip install -r requirements.txt
```

Contents of `requirements.txt`:

```
fastapi>=0.104.0
uvicorn>=0.24.0
aiohttp>=3.9.0
pyyaml>=6.0
docstring-parser>=0.15
python-dotenv>=1.0.0
pydantic>=2.0.0
pip install polymcp==1.2.4
```

## Configuration

Create a `.env` file in the project root:

```bash
# For use with real GitLab
GITLAB_BASE_URL=https://gitlab.com/api/v4
GITLAB_TOKEN=glpat-xxxxxxxxxxxx
GITLAB_PROJECT_ID=12345678

# Optional - security settings
SAFE_MODE=true
DRY_RUN=false
```

## Usage with PolyMCP

This server is fully compatible with the polymcp library. Here is how to use it:

### 1. Start the server

```bash
# Production mode (requires token)
python gitlab_mcp_server.py --http --port 8000
```

### 2. Connect with polymcp

Create a file `gitlab_chat.py`:

```python
#!/usr/bin/env python3
"""GitLab MCP Chat with PolyMCP"""
import asyncio
from polymcp.polyagent import UnifiedPolyAgent, OllamaProvider

async def main():
    # Configure the LLM provider (you can use OpenAI, Anthropic, Ollama, etc.)
    llm = OllamaProvider(model="gpt-oss:120b-cloud", temperature=0.1)
    
    # Point to the GitLab MCP server
    mcp_servers = ["http://localhost:8000/mcp"]
    
    agent = UnifiedPolyAgent(
        llm_provider=llm, 
        mcp_servers=mcp_servers,  
        verbose=True
    )
    
    async with agent:
        print("\nGitLab MCP Server connected!\n")
        print("Available commands:")
        print("- 'show me open merge requests'")
        print("- 'analyze failed jobs'")
        print("- 'create an ADR for cloud migration'")
        print("- 'exit' to quit\n")
        
        while True:
            user_input = input("\nYou: ")
            
            if user_input.lower() in ['exit', 'quit']:
                print("Session ended.")
                break
            
            result = await agent.run_async(user_input, max_steps=5)
            print(f"\nGitLab Assistant: {result}")

if __name__ == "__main__":
    asyncio.run(main())
```

### 3. Run it

```bash
python gitlab_chat.py
```

Example session:

```
GitLab MCP Server connected!

You: show me open merge requests in project mygroup/myproject

GitLab Assistant: I found 3 open merge requests:

1. MR !42 - "Fix authentication bug" 
   Author: john_doe
   Branch: bugfix/auth -> main
   
2. MR !43 - "Add caching layer"
   Author: jane_smith  
   Branch: feature/cache -> main

3. MR !44 - "Update dependencies"
   Author: bob_wilson
   Branch: chore/deps -> main

You: analyze why the pipeline is failing

GitLab Assistant: I analyzed pipeline #12345. There are 2 failed jobs:

1. test:unit (stage: test)
   Error: Snapshot test mismatch
   Suggestion: Run 'npm test -- -u' to update snapshots

2. security:sonar (stage: security)
   Error: Code coverage below threshold (67% < 80%)
   Suggestion: Add tests for uncovered functions

You: exit
Session ended.
```

## Usage with Cursor

### Method 1 - Direct import

Copy `cursor_tools.py` to your project and use it directly:

```python
from cursor_tools import *

# List merge requests
mrs = list_open_merge_requests("mygroup/myproject")

# Analyze pipeline
analysis = analyze_pipeline_failures("mygroup/myproject")

# Create ADR
adr = create_architecture_decision(
    title="Kubernetes Adoption",
    context="We need to scale horizontally",
    decision="We will migrate to Kubernetes on GKE",
    consequences="Higher operational complexity but better scalability"
)
```

### Method 2 - MCP Configuration

Add to `.cursor/mcp_config.json`:

```json
{
  "mcpServers": {
    "gitlab": {
      "command": "python",
      "args": ["gitlab_mcp_server.py", "--mode", "stdio"]
    }
  }
}
```

## Usage with ChatGPT

1. Start the server and expose it publicly (with ngrok or similar):

```bash
python gitlab_mcp_server.py --http --port 8000
ngrok http 8000
```

2. Create a Custom GPT with Actions pointing to the ngrok URL

3. Or use Code Interpreter by uploading `gitlab_assistant.py`

## Safety Features

The server includes built-in protections:

| Feature | Default | What it does |
|---------|---------|--------------|
| Safe Mode | `ON` | Blocks write operations until you're ready |
| Dry Run | `OFF` | Test operations without executing them |
| Project Allowlist | `*` (all allowed) | Use `*` for all, empty to block all, or list specific projects |
| Rate Limiting | 60/min | Prevents API abuse |

> 💡 Start with `SAFE_MODE=true` to explore safely, then disable when needed.

## Available Tools

### Merge Request Management

| Tool | Description |
|------|-------------|
| `list_merge_requests` | List merge requests with filters (state, author, assignee) |
| `get_merge_request_details` | Get MR details including changes and discussions |
| `create_merge_request` | Create a new merge request |
| `approve_merge_request` | Approve a merge request |
| `merge_merge_request` | Merge a merge request into target branch |
| `rebase_merge_request` | Rebase a merge request onto target branch |

### Code Search

| Tool | Description |
|------|-------------|
| `search_code` | Search for code across project files |

### Pipeline & CI/CD

| Tool | Description |
|------|-------------|
| `list_pipeline_jobs` | List all jobs in a pipeline with status |
| `get_job_log` | Get the log output of a specific job |
| `analyze_failed_jobs` | Analyze failures and suggest fixes |
| `trigger_pipeline` | Trigger a new pipeline run |
| `retry_pipeline` | Retry all failed jobs in a pipeline |
| `cancel_pipeline` | Cancel a running pipeline |
| `retry_failed_job` | Retry a specific failed job |

### ADR (Architecture Decision Records)

| Tool | Description |
|------|-------------|
| `create_adr_document` | Create an ADR document in Markdown format |
| `commit_adr_to_gitlab` | Commit ADR to repository with optional MR |

### Cloud Deployment

| Tool | Description |
|------|-------------|
| `deploy_to_cloud` | Deploy to AWS, Azure, or GCP via pipeline |

## Security

To use the server safely with Cursor or other AI assistants:

1. Create a GitLab token with minimal permissions (only `read_api` and `read_repository`)
2. Enable `SAFE_MODE=true` in the .env file to disable destructive operations
3. Use `DRY_RUN=true` to simulate operations without executing them
4. Limit accessible projects by configuring `ALLOWED_PROJECTS`

The server tracks all operations and provides usage statistics.

## Project Structure

```
Gitlab-MCP-Server/
├── gitlab_mcp_server.py    # Main server
├── cursor_tools.py         # Wrapper for Cursor
├── gitlab_chat.py          # Client for PolyMCP
├── gitlab_assistant.py     # Client for ChatGPT
├── .env.example            # Configuration template
├── requirements.txt        # Dependencies
└── README.md
```

## Troubleshooting

| Error | Solution |
|-------|----------|
| `GITLAB_TOKEN is required` | Create `.env` file with your token |
| `Operation blocked by safe mode` | Set `SAFE_MODE=false` in `.env` |
| `Access denied to project` | Check token permissions or `ALLOWED_PROJECTS` |
| `Request timed out` | Increase `MAX_RETRIES` in `.env` |

## Contributing

Contributions are welcome. Open an issue to discuss significant changes before proceeding with a pull request.

## License

MIT License - see LICENSE file for details.

## Useful Links

- GitLab API Documentation: https://docs.gitlab.com/ee/api/
- Model Context Protocol: https://modelcontextprotocol.io/
- PolyMCP: [https://github.com/poly-mcp/polymcp](https://github.com/poly-mcp/Polymcp)
