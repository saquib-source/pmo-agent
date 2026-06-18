# Build Posture: NEW vs ABSORB (read in Phase 1)

Before you write the spec, decide **how the agent enters the runtime**. There are two
postures, and the choice changes nothing about the six artifact *formats* — they stay
byte-compatible — but it changes what the deploy step does and what the spec must record.

| Posture | Meaning | When |
|---------|---------|------|
| **NEW** (greenfield) | The agent is provisioned as a fresh ADK agent on Vertex AI Agent Engine. Nothing pre-exists. | Default for most builds, and for any tenant that has no ISRDS codebase to extend. |
| **ABSORB** (into existing codebase) | The agent is added to an existing ISRDS codebase as a new **Role Category extending `BaseRoleAgent`**, reusing the platform primitives already running there. **Do not rebuild** Config Registry, Authority Gradient, Trust Ledger, or pgvector memory — extend them. | When a runtime codebase already exists (e.g. the PMO Agent absorbs into `…/isrd/agents`). |

The PMO Agent is **ABSORB**. The brief is explicit: *"ABSORB into the existing codebase as a
NEW Role Category extending BaseRoleAgent. Do not rebuild — reuse Config Registry, Authority
Gradient, Trust Ledger, pgvector memory."* Treat that sentence as the posture declaration.

## What ABSORB adds to the build (beyond the six artifacts)

ABSORB never *replaces* the six portable artifacts — it adds three integration obligations,
all recorded in the spec's `build:` block and carried out in Phase 8 (deploy):

1. **Extend `BaseRoleAgent`.** The agent is a new Role Category subclass, not a from-scratch
   runtime. It inherits audit, the Authority Gradient, and Trust-Ledger wiring from the base
   class. Record `extends: BaseRoleAgent` and the new role-category name.
2. **Seed the Config Registry by migration.** The agent's row (engine pointer,
   `memory_surface`, `tool_surface`, `authority`) is added with a numbered SQL migration —
   e.g. `004_seed_pmo.sql` — that runs *after* the base migrations (`001–003`). The migration
   inserts a Config Registry row; it never hardcodes a model (the engine column points at the
   resolver, see `01-agent-spec.md`). Record the migration filename in `build.config_registry_seed`.
3. **Reuse, don't fork, the platform primitives.** Authority Gradient, Trust Ledger, and
   pgvector memory already exist in the codebase. The spec must reference them, not redefine
   them. If you find yourself writing a second Trust Ledger, you have broken ABSORB.

## The spec `build:` block (optional; required when posture is ABSORB)

```yaml
spec:
  build:
    posture: absorb                     # new | absorb
    codebase: "<path-or-repo of the existing runtime>"
    extends: BaseRoleAgent              # ABSORB only
    role_category: PMO                  # the new Role Category name (ABSORB only)
    config_registry_seed: 004_seed_pmo.sql   # the migration that adds this agent's row
    reuse:                              # primitives extended, never rebuilt
      - config_registry
      - authority_gradient
      - trust_ledger
      - pgvector_memory
```

For a NEW agent, either omit `build:` or set `posture: new` — the rest of the block is unused.

## What stays identical regardless of posture

- The six artifact formats. A swarm-built agent and a hand-absorbed agent emit the same shapes.
- The vendor-neutrality rule. The migration seeds an engine *pointer*, never a model name.
- The eight phases and the quality gate. ABSORB is extra deploy wiring, not a different pipeline.

The whole point of separating posture from format: ABSORB is how this agent reaches *this*
runtime today; format parity is how the same agent reaches the swarm and every future tenant
tomorrow.
