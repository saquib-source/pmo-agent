"""
Feature Completeness Agent
Role: audits the Firestore navigation catalog against the canonical product architecture.
Reads system_config/navigation.schema from Firestore, counts built vs unbuilt features,
and surfaces the build gap by division.

Inter-agent wiring:
  ← called by: orchestrator
  → calls:     ownership_raci_agent — to identify WHO is Accountable for each unbuilt division/dept
  → calls:     execution_tracking_agent — to cross-check whether active Jira work exists for
               unbuilt features (is something being built but not yet deployed?)
"""
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "feature_completeness.md"
_PROMPT = _PROMPT_PATH.read_text() if _PROMPT_PATH.exists() else "You are the PMO Feature Completeness agent."
MODEL = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")

# Canonical division name map (staging number → canonical name)
_DIVISION_CANONICAL = {
    1: "Leadership",
    2: "Demand Generation",
    3: "People & Agent Supply",
    4: "Product & Service Intelligence",
    5: "Order Management",
    6: "Infrastructure",
    7: "Technology & Systems",
    8: "Agentic Delivery Exception Ops",
}

# Staging renames divisions 6/7 vs canonical — correct on read
_STAGING_DIV_REMAP = {
    "Technology & Systems": 7,   # staging calls this div 6 — canonical is 7
    "Infrastructure":       6,   # staging calls this div 7 — canonical is 6
}


def _is_unbuilt(node: dict) -> bool:
    href   = (node.get("href", "") or "").lower()
    status = (node.get("status", "") or "").lower()
    # 'Coming Soon' status or a missing/placeholder href both mean unbuilt.
    if status in ("coming soon", "coming-soon", "planned", "not built"):
        return True
    return not href or "coming-soon" in href


def _walk_tree(nodes: list, path: str = "") -> list:
    """Recursively walk the navigation tree and return all function-level leaf nodes."""
    leaves = []
    for node in nodes:
        label     = node.get("title") or node.get("label") or node.get("name", "")
        node_path = f"{path} / {label}" if path else label
        children  = node.get("children", node.get("items", []))
        if children:
            leaves.extend(_walk_tree(children, node_path))
        elif node.get("type") == "function" or node.get("href") or node.get("status"):
            leaves.append({
                "path":    node_path,
                "label":   label,
                "href":    node.get("href", ""),
                "built":   not _is_unbuilt(node),
                "type":    node.get("type", "function"),
            })
    return leaves


def _connect_firestore():
    import firebase_admin
    from firebase_admin import credentials, firestore

    sa_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "")
    if not firebase_admin._apps:
        if sa_path and os.path.exists(sa_path):
            cred = credentials.Certificate(sa_path)
        else:
            cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)

    return firestore.client()


# ── Tools ────────────────────────────────────────────────────────────────────

def audit_features(division_filter: str = "") -> dict:
    """Audit the Firestore navigation catalog for built vs unbuilt features.

    Reads system_config/navigation.schema and counts features at the function level.
    Normalises staging division numbering to canonical (divs 6/7 are swapped in staging).

    Args:
        division_filter: Optional division name to scope the audit (e.g. 'Leadership',
                         'Order Management'). Leave blank for all divisions.
    """
    try:
        db = _connect_firestore()
    except Exception as e:
        return {"error": f"Firestore connection failed: {e}"}

    try:
        # Document lives at system_config/navigation (overridable via env).
        doc_id = os.environ.get("NAV_SCHEMA_DOC", "navigation")
        doc = db.collection("system_config").document(doc_id).get()
        if not doc.exists:
            return {"error": f"system_config/{doc_id} not found in Firestore"}
        raw = doc.to_dict() or {}
        # The nav tree is stored under 'children' (fall back to other known keys).
        nav_data = (raw.get("children") or raw.get("data")
                    or raw.get("navigation") or [])
        if not isinstance(nav_data, list):
            # last resort: first list-valued field
            nav_data = next((v for v in raw.values() if isinstance(v, list)), [])
    except Exception as e:
        return {"error": f"Firestore read failed: {e}"}

    by_division = {}
    total_built = total_features = 0

    for div_node in nav_data:
        div_label = div_node.get("label", div_node.get("name", "Unknown"))

        # Normalise staging 6/7 swap
        canonical_id = _STAGING_DIV_REMAP.get(div_label)
        if canonical_id:
            canonical_name = _DIVISION_CANONICAL.get(canonical_id, div_label)
        else:
            canonical_name = div_label

        if division_filter and division_filter.lower() not in canonical_name.lower():
            continue

        leaves = _walk_tree(div_node.get("children", div_node.get("items", [])), canonical_name)
        built   = sum(1 for l in leaves if l["built"])
        unbuilt_list = [l["path"] for l in leaves if not l["built"]]

        by_division[canonical_name] = {
            "total":        len(leaves),
            "built":        built,
            "unbuilt":      len(leaves) - built,
            "pct_built":    round(built / len(leaves) * 100, 1) if leaves else 0,
            "unbuilt_list": unbuilt_list[:20],  # cap for readability
        }
        total_built    += built
        total_features += len(leaves)

    pct = round(total_built / total_features * 100, 1) if total_features else 0

    try:
        from ..shared.governance import trust_ledger_log
        trust_ledger_log(
            "audit",
            f"Build audit: {total_built}/{total_features} features built ({pct}%) "
            f"across {len(by_division)} divisions",
            agent_id="pmo_feature_completeness",
        )
    except Exception:
        pass

    return {
        "total_features": total_features,
        "total_built":    total_built,
        "total_unbuilt":  total_features - total_built,
        "pct_built":      pct,
        "by_division":    by_division,
    }


def get_division_detail(division_name: str) -> dict:
    """Get the full list of unbuilt features within a specific division, down to function level.

    Args:
        division_name: Division to inspect (e.g. 'Leadership', 'Order Management').
    """
    return audit_features(division_filter=division_name)


# ── Agent ────────────────────────────────────────────────────────────────────

from .ownership_raci import ownership_raci_agent
from .execution_tracking import execution_tracking_agent

feature_completeness_agent = LlmAgent(
    name="feature_completeness_agent",
    model=MODEL,
    instruction=_PROMPT,
    tools=[
        FunctionTool(audit_features),
        FunctionTool(get_division_detail),
        # Identify who is Accountable for each division/dept that has unbuilt features.
        AgentTool(ownership_raci_agent),
        # Cross-check: is there active Jira work in progress for the unbuilt feature?
        # If yes — in progress but not deployed. If no — nothing started yet.
        AgentTool(execution_tracking_agent),
    ],
    output_key="response",
)
