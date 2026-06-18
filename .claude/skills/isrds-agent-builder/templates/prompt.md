# Role
REPLACE — one or two sentences. What this agent is and whom it serves.
(No vendor framing. Do not write "As Claude…". Write to the model-agnostic ADK contract.)

# Operating loop
REPLACE — what the agent does each run, in order. Tie each step to a tool id from the
spec and to the inputs it consumes.

# Judgment rules
REPLACE — how it decides what is at-risk / noteworthy / out of authority. Domain
expertise goes here.

# Gate behavior
REPLACE — when to Review, Escalate, Approve, log an Override, Flag, or accept Kill.
State it plainly so the agent expects the stop. Example: "Before any external
communication, request Approve and wait — never proceed on a timeout."

# Output contract
REPLACE — the exact shape of what it emits, matching the spec's outputs.
