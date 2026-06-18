# Artifact 2: Prompt (Phase 2, parallel)

The prompt is the agent's standing instruction set. It is portable text and **must read
identically regardless of which engine runs it** — that is what makes engine swap a 3–10 day
prompt-retune rather than a rewrite.

## Rules

- **No model-specific syntax or vendor framing.** Do not write "As Claude…" or rely on one
  vendor's tool-calling quirks. Write to the ADK tool-dispatch contract, which is
  model-agnostic as of Cloud Next 2026.
- The prompt describes **behavior and judgment**, not infrastructure. It does not say where
  data lives (that's memory schema + tools) or which approvals are required (that's governance
  + gates). It says how the agent reasons about its job.
- Reference tools by the same ids used in the spec and tool registry.
- State the gate behavior in plain language so the model knows when to stop: e.g. "Before
  sending any external communication, request Approve and wait — never proceed on a timeout."
  The enforcement is in Layers 5/6; the prompt makes the agent *expect* it.

## Suggested structure

```markdown
# Role
One or two sentences. What the agent is and whom it serves.

# Operating loop
What it does each run, in order. Tie steps to tool ids and inputs from the spec.

# Judgment rules
How it decides what's at-risk / noteworthy / out of authority. This is where domain
expertise goes.

# Gate behavior
When to Review, Escalate, Approve, Override-log, Flag, or accept Kill. Plain language;
the runtime enforces it.

# Output contract
The exact shape of what it emits, matching the spec's outputs.
```

## Engine-swap test

Before finishing, reread the prompt and ask: *if a tenant resolved this agent to Grok
tomorrow, would any line break?* If a line only makes sense for one vendor, rewrite it. The
honest swap cost is prompt re-tuning and testing (3–10 days). Vendor-specific prompt lines
inflate that cost and leak lock-in back into a layer that is supposed to be portable.
