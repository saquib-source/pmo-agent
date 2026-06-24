#!/usr/bin/env python3
"""
Phase 6 Quality Gate for ISRDS agent builds.

Runs the automated checks that must pass before Phase 7 (human architecture review):
  1. All six portable artifacts exist.
  2. No vendor / model name appears anywhere in the artifacts (the core platform rule).
  3. Every tool referenced in the spec is registered in the tool registry.
  4. Every action marked irreversible:true maps to an Approve gate.
  5. Approve gates carry NO timeout; every other gate carries a supervisor and an SLA.
  6. Every memory field is justified, and durable-record placement is declared.

Usage:
    python quality_gate.py agents/<agent_id>/

Exit code 0 = pass, 1 = findings (printed). Findings are explanatory, not just booleans —
the point is to tell the builder exactly what to fix, in architecture terms.
"""
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml --break-system-packages")
    sys.exit(2)

# Vendor / model tokens that must never appear in a portable artifact. The runtime engine is
# resolved at execution time via config_registry.resolve(); naming one here is a platform break.
VENDOR_TOKENS = [
    "claude", "anthropic", "gemini", "gpt", "openai", "grok", "xai",
    "llama", "mistral", "sonnet", "opus", "haiku", "gpt-4", "gpt-5",
]
# Tokens allowed despite matching (e.g. the resolution call references the mechanism, not a model).
ALLOWED_SUBSTRINGS = ["config_registry.resolve", "model-agnostic", "model garden"]

SIX_ARTIFACTS = {
    "agent-spec.yaml",
    "prompt.md",
    "tool-registry.yaml",
    "memory-schema.json",
    "governance-rules.yaml",
    "workflow-definition.yaml",
}

# Authority Gradient levels (references/08-authority-and-trust-ledger.md). The spec may omit
# `authority` (older specs predate the field), but if present it must be one of these.
AUTHORITY_LEVELS = {
    "OBSERVE_ONLY", "DECIDE_AND_REPORT", "ACT_WITH_REVIEW", "ACT_AUTONOMOUS",
}
# Build postures (references/07-build-posture.md).
BUILD_POSTURES = {"new", "absorb"}
# Fields an ABSORB build must declare so deploy can wire it into the existing codebase.
ABSORB_REQUIRED = ["codebase", "extends", "role_category", "config_registry_seed", "reuse"]
# Every agent must record this audit invariant (FR-7 / BaseRoleAgent consistency).
REQUIRED_INVARIANT = "trust-ledger-audit"


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text()) if p.exists() else None


def load_json(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def check_files_exist(folder: Path, findings):
    present = {f.name for f in folder.iterdir() if f.is_file()}
    missing = SIX_ARTIFACTS - present
    for m in sorted(missing):
        findings.append(f"[FILES] Missing artifact: {m}")


def check_no_vendor_names(folder: Path, findings):
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name not in SIX_ARTIFACTS:
            continue
        for i, raw in enumerate(f.read_text().splitlines(), 1):
            low = raw.lower()
            if any(a in low for a in ALLOWED_SUBSTRINGS):
                continue
            for tok in VENDOR_TOKENS:
                # word-ish boundary check to avoid matching inside unrelated words
                if tok in low.replace("_", " ").replace("-", " ").split() or tok in low.split():
                    findings.append(
                        f"[VENDOR] {f.name}:{i} names a model/vendor ('{tok}'). "
                        f"Runtime engine must be config-resolved, never hardcoded. Line: {raw.strip()!r}"
                    )
                    break


def check_tools_registered(folder: Path, findings):
    spec = load_yaml(folder / "agent-spec.yaml")
    reg = load_yaml(folder / "tool-registry.yaml")
    if not spec or not reg:
        return
    spec_tools = set((spec.get("spec") or {}).get("tools") or [])
    reg_tools = {t.get("id") for t in (reg.get("tools") or [])}
    for t in sorted(spec_tools - reg_tools):
        findings.append(
            f"[TOOLS] Spec references tool '{t}' that is not registered in tool-registry.yaml."
        )


def check_irreversible_has_approve(folder: Path, findings):
    reg = load_yaml(folder / "tool-registry.yaml")
    wf = load_yaml(folder / "workflow-definition.yaml")
    if not reg or not wf:
        return
    irreversible = []
    for t in (reg.get("tools") or []):
        # Check top-level irreversible flag on the tool
        tool_irreversible = t.get("irreversible") is True
        for a in (t.get("actions") or []):
            if isinstance(a, dict):
                if a.get("irreversible") is True:
                    irreversible.append(f"{t.get('id')}.{a.get('name')}")
            elif isinstance(a, str) and tool_irreversible:
                irreversible.append(f"{t.get('id')}.{a}")
    gate_types = {g.get("type") for g in (wf.get("gates") or [])}
    if irreversible and "Approve" not in gate_types:
        findings.append(
            "[GOVERNANCE] Irreversible actions exist "
            f"({', '.join(irreversible)}) but no Approve gate is configured. "
            "Irreversible actions never execute without explicit Approve."
        )


def check_gate_slas(folder: Path, findings):
    wf = load_yaml(folder / "workflow-definition.yaml")
    if not wf:
        return
    for g in (wf.get("gates") or []):
        gtype = g.get("type")
        sla = g.get("sla")
        if gtype == "Approve":
            if sla not in (None, "none", "None"):
                findings.append(
                    f"[GATES] Approve gate has an SLA/timeout ('{sla}'). Approve is unconditional: "
                    "it waits indefinitely and must never auto-proceed. Set sla: none."
                )
        else:
            if not g.get("supervisor"):
                findings.append(f"[GATES] {gtype} gate is missing a supervisor.")
            if not sla or sla in ("none", "None"):
                findings.append(f"[GATES] {gtype} gate is missing an SLA.")


def check_authority(folder: Path, findings):
    spec = load_yaml(folder / "agent-spec.yaml")
    if not spec:
        return
    auth = (spec.get("spec") or {}).get("authority")
    if auth is None:
        findings.append(
            "[AUTHORITY] Spec declares no authority level. Set spec.authority to one of "
            f"{sorted(AUTHORITY_LEVELS)} (see references/08-authority-and-trust-ledger.md)."
        )
    elif auth not in AUTHORITY_LEVELS:
        findings.append(
            f"[AUTHORITY] Unknown authority level '{auth}'. Must be one of {sorted(AUTHORITY_LEVELS)}."
        )


def check_build_posture(folder: Path, findings):
    spec = load_yaml(folder / "agent-spec.yaml")
    if not spec:
        return
    build = (spec.get("spec") or {}).get("build")
    if not build:
        return  # NEW/greenfield agents may omit the build block entirely
    posture = build.get("posture")
    if posture not in BUILD_POSTURES:
        findings.append(
            f"[BUILD] spec.build.posture is '{posture}'; must be one of {sorted(BUILD_POSTURES)}."
        )
        return
    if posture == "absorb":
        for f in ABSORB_REQUIRED:
            if not build.get(f):
                findings.append(
                    f"[BUILD] ABSORB build is missing spec.build.{f}. An absorb into an existing "
                    "codebase must declare it (see references/07-build-posture.md)."
                )


def check_trust_ledger(folder: Path, findings):
    gov = load_yaml(folder / "governance-rules.yaml")
    if not gov:
        return
    invariant_ids = {i.get("id") for i in (gov.get("invariants") or [])}
    if REQUIRED_INVARIANT not in invariant_ids:
        findings.append(
            f"[GOVERNANCE] Missing '{REQUIRED_INVARIANT}' invariant. Every agent must record "
            "every decision and tool call to the Trust Ledger (FR-7 / BaseRoleAgent consistency; "
            "see references/08-authority-and-trust-ledger.md)."
        )


def check_memory_placement(folder: Path, findings):
    mem = load_json(folder / "memory-schema.json")
    if not mem:
        return
    for fld in (mem.get("fields") or []):
        if not fld.get("destination_justification"):
            findings.append(
                f"[MEMORY] Field '{fld.get('name')}' lacks a destination_justification. "
                "Every field in Agent Engine memory must justify being agent context, "
                "not a durable business record (which belongs in PostgreSQL)."
            )


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"Not a directory: {folder}")
        sys.exit(2)

    findings = []
    check_files_exist(folder, findings)
    check_no_vendor_names(folder, findings)
    check_tools_registered(folder, findings)
    check_irreversible_has_approve(folder, findings)
    check_gate_slas(folder, findings)
    check_memory_placement(folder, findings)
    check_authority(folder, findings)
    check_build_posture(folder, findings)
    check_trust_ledger(folder, findings)

    if not findings:
        print(f"QUALITY GATE PASSED — {folder} is ready for Phase 7 human review.")
        sys.exit(0)
    print(f"QUALITY GATE FAILED — {len(findings)} finding(s) in {folder}:\n")
    for i, f in enumerate(findings, 1):
        print(f"  {i}. {f}")
    print("\nFix every finding, then re-run. Phase 7 is mandatory and never skipped.")
    sys.exit(1)


if __name__ == "__main__":
    main()
