# MCP Server Setup (read when wiring tool-registry.yaml to real connections)

Every tool in `tool-registry.yaml` with `integration: mcp` needs a running MCP server
that the ADK agent connects to via `MCPToolset`. This reference covers how to set up
those servers.

## Option A: Managed MCP endpoints (recommended)

Some services provide managed MCP servers — zero infrastructure on your side.

### Atlassian Jira + Confluence (Rovo MCP)
```
URL:  https://mcp.atlassian.com/v1/mcp/authv2
Auth: OAuth 2.1 (browser-based token)
```

Setup:
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Generate an API token
3. Set in your agent's `.env`:
   ```
   JIRA_MCP_URL=https://mcp.atlassian.com/v1/mcp/authv2
   JIRA_MCP_TOKEN=your-token
   ```
4. In `agent.py`, the `MCPToolset` connects automatically:
   ```python
   jira_tools = MCPToolset(
       connection_params=StreamableHTTPConnectionParams(
           url=os.environ["JIRA_MCP_URL"],
           headers={"Authorization": f"Bearer {os.environ['JIRA_MCP_TOKEN']}"},
       ),
   )
   ```

### PostgreSQL (via community MCP server)
```
URL:  http://localhost:5433/mcp  (local) or Cloud Run endpoint (remote)
Auth: Connection string in environment
```

## Option B: Custom MCP server on Cloud Run

When no managed MCP endpoint exists for a tool, build your own using Python FastMCP.

### Step 1: Build the server
```python
# mcp_server.py
from fastmcp import FastMCP

mcp = FastMCP("my-tool-server")

@mcp.tool()
def search_data(query: str) -> dict:
    """Search for data matching the query."""
    # Your custom logic here
    return {"results": [...]}

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
```

### Step 2: Containerize
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "mcp_server.py"]
```

### Step 3: Deploy to Cloud Run
```bash
# Build and push container
gcloud builds submit --tag gcr.io/$PROJECT/my-mcp-server
# Deploy
gcloud run deploy my-mcp-server \
  --image gcr.io/$PROJECT/my-mcp-server \
  --region us-central1 \
  --allow-unauthenticated  # or use IAM for production
```

### Step 4: Store secrets
```bash
echo -n "your-api-key" | gcloud secrets create MY_API_KEY --data-file=-
# Reference in Cloud Run:
gcloud run services update my-mcp-server \
  --set-secrets=API_KEY=MY_API_KEY:latest
```

### Step 5: Connect from agent.py
```python
my_tools = MCPToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=os.environ.get("MY_MCP_URL", "https://my-mcp-server-xxxxx.run.app/mcp"),
    ),
)
```

## Register in GCP Cloud API Registry

Per the architecture (Section 7), every MCP server should be registered in the
GCP Cloud API Registry for discoverability. This is informational for now — the
agent connects via URL, but the registry provides a catalog for the swarm.

```bash
gcloud api-gateway apis create my-tool-api --project=$PROJECT
```

## How agent.py maps tool-registry.yaml to MCPToolset

The `tool-registry.yaml` is the specification; `agent.py` is the implementation.
When generating agent code, map each tool entry:

| tool-registry.yaml | agent.py |
|---------------------|----------|
| `integration: mcp` + `mcp.server: atlassian` | `MCPToolset(url=JIRA_MCP_URL)` |
| `integration: mcp` + `mcp.server: postgres` | `MCPToolset(url=POSTGRES_MCP_URL)` |
| `integration: custom` | `@FunctionTool` decorator on a Python function |

The skill generates this mapping automatically in Phase 8 code generation.
