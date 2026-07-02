# BISD Agentic Platform — Agent Scaffold

This directory contains stub artifacts for all 34 agentic functions mapped in
BISD_Projects_Agentic_Function_Architecture_v2.

## Status

**All agents are DORMANT.** `is_active: false` in every agent_spec.yaml and
every Config Registry entry. No agent runs until an explicit go-ahead sets
`is_active: true` per agent.

## Structure

```
bisd/
├── shared/                         # Shared infrastructure (mirrors pmo-swarm/adk/shared/)
├── brand-product-leadership/       # 4 functions
├── influence-demand/               # 3 functions
├── find-the-jobs/                  # 2 functions
├── win-the-bid/                    # 7 functions
├── pipeline-margin-oversight/      # 1 function
├── turn-win-into-shipment/         # 5 functions
├── stand-up-install/               # 5 functions
├── install-sign-off/               # 3 functions
└── get-paid-close-out/             # 4 functions
```

## Per-Function Artifacts (6 portable artifacts)

| Artifact | Status |
|---|---|
| agent_spec.yaml | Stub — ~70% complete from architecture doc |
| memory_schema.yaml | Stub — namespaces declared, fields TBD |
| workflow.yaml | Stub — trigger and steps declared, logic TBD |
| governance-rules.yaml | Stub — gates declared, SLAs TBD |
| prompts/ | Stub — placeholders only; requires requirements workshop |
| tool_registry.yaml | Stub — tool names only; endpoints TBD pending vendor access |

## Build Sequence

Enhance each agent through the 8-phase ISRDS build pipeline:
1. Agent Spec → 2. Prompt → 3. Tool Registry → 4. Memory Schema
→ 5. Governance Rules → 6. Workflow → 7. Test → 8. Deploy

Do not activate any agent without completing all 8 phases and receiving
explicit go-ahead from the ISRDS platform owner.

## Architecture Reference

- BISD_Projects_Agentic_Function_Architecture_v2.xlsx
- ISRDS_Screen_Template_Data_Requirements_for_9_1_v0_1_2026-06-27.docx
- CLAUDE.md (project instructions)
