# Artifact 1: Agent Spec (Phase 1)

The agent spec is the **base, vendor-neutral** description of the agent. It is the root
artifact; the other five reference it. It is YAML.

## Hard constraints

- **No vendor or model name appears anywhere.** Not `claude`, not `gemini`, not `gpt`, not
  `grok`, not a model version string. The runtime engine is resolved at execution time by the
  Config Registry. The spec only records *that* resolution happens, never *what* it resolves
  to.
- The spec names **tool ids**, not tool implementations. Implementations live in
  `tool-registry.yaml`.
- The spec references the other artifacts by filename, not by inlining them.

## Required fields

```yaml
apiVersion: isrds.agent/v1
kind: AgentSpec
metadata:
  agent_id: pmo-agent          # kebab-case, stable, used in config_registry.resolve()
  display_name: PMO Agent
  tier: 1                       # tier label is informational; it does NOT pick the proving order
  division: "5.0 Runtime"       # product-architecture home
  department: "PMO"
  owner: Manmeet
  version: 0.1.0
spec:
  purpose: >
    One paragraph. What operating reality this agent externalizes or automates.
  engine_binding:
    # The platform-defining line. No model named here, ever.
    resolution: "config_registry.resolve(tenant_id, agent_id)"
  tenants:                      # which tenants receive this agent (config, not rebuild)
    - isrds                     # tenant 1 always proves first
  inputs:                       # what the agent consumes
    - name: active_projects
      source: postgres          # see four-destination model
      contract: "project[] with id, milestone, status, budget"
  outputs:
    - name: operating_picture
      sink: postgres
      contract: "daily digest + at-risk milestone list"
  tools:                        # ids only; defined in tool-registry.yaml
    - workorder_db
    - jira
  refs:
    prompt: prompt.md
    tools: tool-registry.yaml
    memory: memory-schema.json
    governance: governance-rules.yaml
    workflow: workflow-definition.yaml
  gates:                        # which of the six gate types apply; configured in Phase 5
    - Review
    - Escalate
    - Approve
```

## Notes

- `tier` records the product-architecture home. It is **not** the proving-build selector — the
  selector is runtime-layer coverage at minimum blast radius (see `build-sequencing.md`). The
  Survey Agent is Tier 2 by department but Tier 1 by timing for exactly this reason.
- `tenants` is a list because the dual-use pattern means one unconfigured agent serves many
  tenants. ISRDS (tenant 1) proves every agent before any paying tenant receives it. Adding a
  tenant is a config entry here plus Config Registry values — not a rebuild.
- Keep `purpose` honest and narrow. A spec that tries to do everything fails the governance
  attachment in Phase 3 because its action surface is unbounded.
