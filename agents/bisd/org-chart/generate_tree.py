#!/usr/bin/env python3
"""
Generate the BISD org-chart data from the real agent_spec.yaml files.

Source of truth: agents/bisd/<sub-department>/<function>/agent_spec.yaml
Output:
  tree.data.js   -> window.BISD_TREE = {...};   (consumed by index.html over file://)
  tree.json      -> same tree, plain JSON (for wiring into a real app / tests)

The org-chart *shape* is:  Basco -> value-chain stage -> sub-department -> function.
Everything except the stage grouping and the prototype flag comes straight from the specs.
Those two are curation layers defined below (STAGES, PROTO) because the specs only
carry `sub_department`, not the higher stage grouping or UI prototype state.

Run:  python generate_tree.py
Dep:  pyyaml   (pip install pyyaml)
"""

import json
import pathlib
import sys

try:
    import yaml
except ImportError:
    sys.exit("This script needs PyYAML.  Install it with:  pip install pyyaml")

HERE = pathlib.Path(__file__).resolve().parent
BISD_ROOT = HERE.parent  # agents/bisd

# ── Curation layer 1: the 4 value-chain stages that group the 9 sub-departments ──
# The specs only know their sub_department; this maps sub-departments into the
# installed-sales value chain Todd drew. Edit here if the grouping changes.
STAGES = [
    (1, "Demand Generation",     "attract → specify → find",
        ["brand-product-leadership", "influence-demand", "find-the-jobs"]),
    (2, "Win the Work",          "decide → price → submit",
        ["win-the-bid", "pipeline-margin-oversight"]),
    (3, "Deliver",               "realize → procure → install",
        ["turn-win-into-shipment", "stand-up-install", "install-sign-off"]),
    (4, "Get Paid & Close Out",  "bill → collect → release",
        ["get-paid-close-out"]),
]

# Human-friendly sub-department labels (folder name -> display label).
SUBDEPT_LABELS = {
    "brand-product-leadership": "Brand & Product Leadership",
    "influence-demand":         "Influence Demand",
    "find-the-jobs":            "Find the Jobs",
    "win-the-bid":              "Win the Bid",
    "pipeline-margin-oversight": "Pipeline & Margin Oversight",
    "turn-win-into-shipment":   "Turn Win into Shipment",
    "stand-up-install":         "Stand Up Install",
    "install-sign-off":         "Install Sign-Off",
    "get-paid-close-out":       "Get Paid & Close Out",
}

# ── Curation layer 2: which function has a working review-screen prototype ──
# Keyed by the function folder name. Only Publicized Project Aggregation (the
# Gross Opportunity queue) has a live prototype today.
PROTO = {
    "publicized-project-aggregation": {
        "alias": "= Gross Opportunity",
        # Deployed GOA console (Cloud Run). The chart also live-fetches real counts
        # from {live_api}/api/counts and falls back to `sim` when unreachable.
        "link": "https://goa-console-1059272334202.us-central1.run.app/",
        "live_api": "https://goa-console-1059272334202.us-central1.run.app",
        "sim": {"active": 847, "new": 23},   # fallback placeholder metrics
    },
}


def load_specs():
    """Return {sub_department_folder: [function_dict, ...]} from every agent_spec.yaml."""
    by_subdept = {}
    for spec_path in sorted(BISD_ROOT.glob("*/*/agent_spec.yaml")):
        subdept_folder = spec_path.parent.parent.name
        fn_folder = spec_path.parent.name
        with open(spec_path, encoding="utf-8") as f:
            spec = yaml.safe_load(f)

        blocked = (spec.get("blocked_until") or "None").strip()
        fn = {
            "folder": fn_folder,
            "name": spec.get("display_name") or fn_folder,
            "obj": (spec.get("business_object") or "").strip(),
            "posture": (spec.get("posture") or "").strip(),
            "status": (spec.get("implementation_status") or "").strip(),
            "blocked": blocked,
            "sub": len(spec.get("sub_agents") or []),
            "gates": spec.get("human_gates") or [],
            "is_active": bool(spec.get("is_active", False)),
            "deferred": (spec.get("implementation_status") or "").strip().lower() == "deferred",
        }
        p = PROTO.get(fn_folder)
        if p:
            fn["proto"] = True
            fn["alias"] = p["alias"]
            fn["link"] = p["link"]
            fn["live_api"] = p.get("live_api")
            fn["sim"] = p["sim"]
        by_subdept.setdefault(subdept_folder, []).append(fn)

    # deterministic order within each sub-department
    for fns in by_subdept.values():
        fns.sort(key=lambda x: x["name"])
    return by_subdept


def build_tree(by_subdept):
    seen_subdepts = set()
    stages = []
    for num, name, arc, subdept_folders in STAGES:
        subdepts = []
        for folder in subdept_folders:
            fns = by_subdept.get(folder)
            if not fns:
                print(f"  warning: no functions found for sub-department '{folder}'")
                continue
            seen_subdepts.add(folder)
            subdepts.append({
                "type": "subdept",
                "name": SUBDEPT_LABELS.get(folder, folder),
                "folder": folder,
                "children": fns,
            })
        stages.append({
            "type": "module", "num": num, "name": name, "arc": arc, "children": subdepts,
        })

    # surface any sub-department the STAGES map forgot
    for folder in by_subdept:
        if folder not in seen_subdepts:
            print(f"  warning: sub-department '{folder}' is not assigned to any stage")

    return {
        "type": "root",
        "name": "Basco",
        "sub": "Installed Sales Division · BISD",
        "children": stages,
    }


def main():
    by_subdept = load_specs()
    tree = build_tree(by_subdept)

    total_fns = sum(len(v) for v in by_subdept.values())
    active = sum(1 for v in by_subdept.values() for f in v if f["is_active"])
    proto = sum(1 for v in by_subdept.values() for f in v if f.get("proto"))
    blocked = sum(1 for v in by_subdept.values() for f in v
                  if f["blocked"] != "None" and not f.get("proto"))

    (HERE / "tree.json").write_text(
        json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
    (HERE / "tree.data.js").write_text(
        "// AUTO-GENERATED by generate_tree.py — do not edit by hand.\n"
        "window.BISD_TREE = " + json.dumps(tree, ensure_ascii=False) + ";\n",
        encoding="utf-8")

    print(f"Generated tree.json and tree.data.js")
    print(f"  {len(STAGES)} stages, {len(by_subdept)} sub-departments, {total_fns} functions")
    print(f"  {proto} in prototype, {blocked} blocked, {active} active, "
          f"{total_fns - active} dormant")


if __name__ == "__main__":
    main()
