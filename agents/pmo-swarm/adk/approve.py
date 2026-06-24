"""
Human approval CLI for PMO Swarm governance gates.

When PMO_AUTO_COMMENT=false, the agent never writes to Jira on its own. Instead
it drafts an action and opens a governance gate (logged as a `gate` entry in the
Trust Ledger). This tool is the human side of that gate: list what is pending,
review each drafted action, and approve → the comment is posted to Jira and the
decision is recorded back to the Trust Ledger for the audit trail.

Usage:
    python -m adk.approve                 # list all pending approvals
    python -m adk.approve --list          # same as above
    python -m adk.approve <TICKET>        # review + approve the gate for one ticket
    python -m adk.approve <TICKET> --yes  # approve without the interactive prompt

This works against the local Trust Ledger JSONL (trust-ledger.jsonl). For the
durable Cloud SQL store, run it where DB env vars / Cloud SQL Connector are set.
"""
import sys
import logging

from .shared.governance import trust_ledger_read, trust_ledger_log
from .shared import jira_client as jc

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("approve")


def _pending_gates(last_n: int = 200) -> list:
    """Gates that were opened but have no matching approval/rejection yet."""
    entries = trust_ledger_read(last_n)
    opened, resolved = [], set()
    for e in entries:
        detail = e.get("detail", "")
        if e.get("type") == "gate":
            opened.append(e)
        elif e.get("type") in ("approval", "rejection"):
            # detail starts with the ticket key in brackets we wrote on resolve
            resolved.add(detail.split("|", 1)[0].strip())
    # de-dupe opened gates by their detail; drop ones already resolved
    seen, out = set(), []
    for e in opened:
        key = e.get("detail", "")
        if key in seen or key in resolved:
            continue
        seen.add(key)
        out.append(e)
    return out


def _ticket_of(detail: str) -> str:
    # gate detail format: "Review: <text> [TICKET]"
    if "[" in detail and detail.rstrip().endswith("]"):
        return detail.rsplit("[", 1)[1].rstrip("]").strip()
    return ""


def list_pending() -> None:
    gates = _pending_gates()
    if not gates:
        log.info("✅ No pending approvals.")
        return
    log.info("⏸  %d pending approval(s):\n", len(gates))
    for i, g in enumerate(gates, 1):
        detail = g.get("detail", "")
        log.info("  [%d] %s  (%s)", i, detail, g.get("timestamp", "")[:19])
    log.info("\nReview one with:  python -m adk.approve <TICKET>")


def approve(ticket: str, auto_yes: bool = False) -> int:
    gates = _pending_gates()
    match = next((g for g in gates if _ticket_of(g.get("detail", "")) == ticket), None)
    if not match:
        log.error("No pending gate found for %s. Run with --list to see all.", ticket)
        return 1

    detail = match["detail"]
    log.info("Pending action for %s:\n", ticket)
    log.info("  %s\n", detail)

    if not auto_yes:
        resp = input(f"Post this to Jira {ticket}? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            trust_ledger_log("rejection", f"{ticket} | human declined gate", agent_id="human")
            log.info("✗ Declined. Recorded to Trust Ledger.")
            return 0

    # The drafted message text is everything after "Review: " up to the trailing [TICKET]
    text = detail
    if ":" in detail:
        text = detail.split(":", 1)[1]
    text = text.rsplit("[", 1)[0].strip()

    try:
        result = jc.add_comment_adf(ticket, text)
        log.info("✓ Comment posted to %s (id=%s)", ticket, result.get("id", "?"))
        trust_ledger_log("approval", f"{ticket} | human approved + posted", agent_id="human")
        return 0
    except Exception as e:
        log.error("Failed to post comment to %s: %s", ticket, e)
        return 1


def main() -> int:
    args = [a for a in sys.argv[1:]]
    auto_yes = "--yes" in args
    args = [a for a in args if a not in ("--yes",)]

    if not args or args[0] in ("--list", "-l", "list"):
        list_pending()
        return 0
    return approve(args[0], auto_yes=auto_yes)


if __name__ == "__main__":
    raise SystemExit(main())
