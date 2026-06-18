# Swarm requirements — PMO Execution Agent (v2.0 rebuild)

## Friction points

1. **Custom MCP server required for Jira.** Atlassian Rovo MCP doesn't expose `find_stalled_issues`
   or `get_changes_since` as native tools — we need computed queries server-side. This means
   every deployment needs a running MCP server (local or Cloud Run).

2. **Staleness threshold must be configurable.** 24h for production, but testing needs 1h or less.
   The agent reads the threshold from the prompt, but the MCP server's `find_stalled_issues` also
   takes `hours_threshold` as a parameter. Both must agree.

3. **Operating Brief format is rigid by design.** The prompt specifies an exact format template.
   If teams want different sections, the prompt must be versioned per-tenant. The swarm should
   parameterize the brief template.

4. **RACI matrix is inferred, not explicit.** Jira doesn't have a native RACI field. The agent
   uses assignee = Responsible and labels/components to infer the rest. For real RACI, a custom
   field or external data source is needed.

5. **Trust Ledger is file-based for testing.** Production needs PostgreSQL. The governance.py
   module reads `TRUST_LEDGER_PATH` from env — the swarm should generate the Postgres variant
   when deploying to production.
