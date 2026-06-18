# Artifact 3: Tool Registry (Phase 2, parallel)

This is Layer 3 of the runtime (Tools & Integrations). It declares every external capability
the agent can call, and **how** that capability is wired.

## The integration decision: MCP server first, custom connector only as fallback

The architecture is explicit (Section 4, Layer 3; Section 11 Action Registry):

> MCP servers via Cloud API Registry are the **preferred** integration pattern; custom
> connectors only where no MCP server exists.

So for any third-party tool — JIRA, Slack, Salesforce, a payments API — the decision tree is:

1. **Does an MCP server exist for this tool?** → Register it via the GCP **Cloud API Registry**
   and reference it here. No integration code. This is the default and covers most tools.
   - JIRA / Confluence: Atlassian ships an MCP server. Use it. Do **not** hand-write a JIRA
     REST client.
   - PostgreSQL (your own relational DB): access it through a **PostgreSQL MCP server**, not a
     custom DB connector. The migration plan (Section 5.4) calls this out specifically.
2. **No MCP server exists?** → Build a **custom connector** (ADK custom tool) and register it
   here. This is the exception, not the rule. Custom connectors are the thing the MCP-first
   policy is designed to minimize, because they are bespoke code you then own and maintain.

"Use the Google MCP server" is the wrong framing for the decision. The pattern is **MCP as a
protocol**: you register whichever vendor's MCP server fits the tool (Atlassian's for JIRA,
the Postgres MCP server for the database, etc.) into GCP's Cloud API Registry, and ADK
dispatches to it at runtime. GCP is the registry and runtime home; the MCP servers themselves
come from whoever publishes them.

There is no "integrate in code and deploy" path for tools that already have an MCP server.
Writing connector code when an MCP server exists is the anti-pattern the architecture removed
in the June 2026 reanalysis — it adds custom code, raises the maintenance surface, and buys
nothing.

## Tool registry shape

```yaml
apiVersion: isrds.agent/v1
kind: ToolRegistry
agent_id: pmo-agent
tools:
  - id: jira                          # matches the id used in agent-spec.yaml and prompt.md
    integration: mcp                  # mcp | custom
    mcp:
      server: atlassian               # which MCP server
      registered_via: cloud_api_registry
      scopes: [read:issues, write:comments]   # least privilege
    actions:
      - name: read_issue
        irreversible: false
      - name: comment_issue
        irreversible: false           # informs gate config in Phase 5
  - id: workorder_db
    integration: mcp
    mcp:
      server: postgres                # PostgreSQL access is an MCP server, not custom code
      registered_via: cloud_api_registry
      scopes: [select]                # read-only for a reporting agent
    actions:
      - name: query_workorders
        irreversible: false
  - id: legacy_widget                 # example of the rare fallback
    integration: custom               # only because no MCP server exists for it
    custom:
      type: adk_custom_tool
      note: "No MCP server published; revisit if one appears."
    actions:
      - name: push_update
        irreversible: true            # → forces an Approve gate in Phase 5
```

## Two things the quality gate (Phase 6) checks here

- Every tool id in `agent-spec.yaml` exists in this registry. A spec that calls an
  unregistered tool fails.
- Every action marked `irreversible: true` is covered by an **Approve** gate in the workflow.
  Irreversible actions never execute without explicit Approve (Layer 6, Section 8).

## Least privilege

Scope each MCP server to the minimum the agent needs (a reporting agent gets `select`, not
`insert`). Over-scoping is the most common way an internal-only proving agent quietly grows
blast radius and stops being a safe proving build.
