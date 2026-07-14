"""
ISRDS PMO Swarm Daemon

Drives the PMO Orchestrator on a recurring schedule.
Each cycle sends "Run the Operating Brief" to the orchestrator, which fans out to its
skill agents (execution_tracking, follow_up, ownership_raci, feature_completeness, hygiene).

Usage:
    python pmo_daemon.py           # Continuous loop (SCAN_INTERVAL minutes)
    python pmo_daemon.py --once    # Single cycle and exit
    python pmo_daemon.py --brief   # Brief only (skips auto-chase)
    python pmo_daemon.py --web     # Hint: use `adk web .` instead for interactive mode
"""
import os
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

# ── Load .env before any GCP/ADK imports ─────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env", override=True)

# ── Resolve relative path env vars ───────────────────────────────────────────
_ADK_DIR = Path(__file__).parent
for _var in ["GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_SERVICE_ACCOUNT",
             "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"]:
    _val = os.environ.get(_var)
    if _val and not os.path.isabs(_val):
        _resolved = str((_ADK_DIR / _val).resolve())
        if os.path.exists(_resolved):
            os.environ[_var] = _resolved
        else:
            print(f"⚠  {_var} → missing: {_resolved} — using system default")
            os.environ.pop(_var, None)

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# ── ADK imports (after env is loaded) ────────────────────────────────────────
from google.adk.runners import Runner
from google.genai.types import Content, Part

from .orchestrator import root_agent
from .shared.memory          import get_session_service       # Layer 4
from .shared import observability                             # Layer 8
from .shared import config_registry                           # Layer 1
from .shared.config_registry import (
    get_scan_interval, get_brief_hour, get_auto_comment,
    get_jira_projects,
)
from .shared.db import get_pool as get_db_pool               # Cloud SQL Postgres (operational tables only)
from .shared.analytics import (                              # BigQuery — all agent-generated data
    ensure_dataset_and_tables,
    log_cycle_metrics,
    log_operating_brief,
)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = _ADK_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

log = logging.getLogger("pmo.daemon")
log.setLevel(logging.INFO)
log.handlers.clear()
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_fh  = logging.FileHandler(LOG_DIR / "pmo_daemon.log")
_fh.setFormatter(_fmt)
log.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
log.addHandler(_ch)

# ── Config — populated after async initialize(), env-var defaults until then ──
SCAN_INTERVAL  = get_scan_interval()
BRIEF_HOUR     = get_brief_hour()
AUTO_COMMENT   = get_auto_comment()
JIRA_PROJECTS  = get_jira_projects()
JIRA_PROJECT   = JIRA_PROJECTS[0] if JIRA_PROJECTS else "ISRDS"

BRIEF_DIR = _ADK_DIR / "briefs"
BRIEF_DIR.mkdir(exist_ok=True)

# ── ADK runner (Layer 2 + Layer 4) — created after initialize() in startup ───
_session_service = None
_runner: Runner | None = None


async def _startup() -> None:
    """Called once before the first cycle.
    Loads config from Cloud SQL Postgres, opens DB pool, wires session service + runner.
    """
    global _session_service, _runner
    global SCAN_INTERVAL, BRIEF_HOUR, AUTO_COMMENT, JIRA_PROJECTS, JIRA_PROJECT

    # Layer 1 — pull config from Cloud SQL Postgres config_registry
    await config_registry.initialize()
    # Refresh module-level vars now that DB config is loaded
    SCAN_INTERVAL = get_scan_interval()
    BRIEF_HOUR    = get_brief_hour()
    AUTO_COMMENT  = get_auto_comment()
    JIRA_PROJECTS = get_jira_projects()
    JIRA_PROJECT  = JIRA_PROJECTS[0] if JIRA_PROJECTS else "ISRDS"

    # Layer 4 — open Cloud SQL Postgres pool (used by governance, memory, brief saving)
    await get_db_pool()

    # BigQuery — ensure isrds_pmo dataset + tables exist
    ensure_dataset_and_tables()

    # Layer 4 — ADK session service
    _session_service = get_session_service()
    _runner = Runner(
        agent=root_agent,
        app_name="pmo_swarm",
        session_service=_session_service,
    )
    log.info(
        f"Startup complete — projects: {', '.join(JIRA_PROJECTS)}"
        f"  scan={SCAN_INTERVAL}min  brief={BRIEF_HOUR}:00"
    )


async def _run_prompt(prompt: str, session_id: str) -> str:
    """Send a prompt to the orchestrator and collect the full response."""
    if _runner is None:
        raise RuntimeError("Runner not initialised — call _startup() first")

    # ADK requires the session to exist before run_async can use it.
    # get_session returns None (not an exception) when not found.
    existing = await _session_service.get_session(
        app_name="pmo_swarm",
        user_id="pmo_daemon",
        session_id=session_id,
    )
    if existing is None:
        await _session_service.create_session(
            app_name="pmo_swarm",
            user_id="pmo_daemon",
            session_id=session_id,
        )

    message = Content(role="user", parts=[Part(text=prompt)])
    full_response = []

    async for event in _runner.run_async(
        user_id="pmo_daemon",
        session_id=session_id,
        new_message=message,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_response.append(part.text)

    return "\n".join(full_response)


def _build_brief_prompt(mode: str = "full") -> str:
    now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    projects_str = ", ".join(JIRA_PROJECTS) if JIRA_PROJECTS else JIRA_PROJECT
    if mode == "brief_only":
        return (
            f"It is {now}. Run the daily Operating Brief. "
            f"Scan all active work in Jira projects: {projects_str}. "
            f"Identify stalled tickets, check feature build completion, and write the brief. "
            f"Do NOT draft or post any chase comments — report only."
        )
    auto_chase_instruction = (
        "For tickets stalled >48h and Critical/High priority, draft chase pings "
        "and post them directly (auto-comment is ON)."
        if AUTO_COMMENT else
        # Default: DRAFT messages and queue them for human approval. Do NOT post.
        "For every ticket stalled >24h, call `draft_followup_ping` with a complete, "
        "human-voiced message written as Danielle (warm, direct, references the ticket "
        "summary and how long it's been stalled, no greeting, no sign-off). This queues "
        "the message for human approval — do NOT post it yourself. Draft one per stalled ticket."
    )
    return (
        f"It is {now}. Run the full PMO Operating Brief cycle for projects: {projects_str}. "
        f"1. Scan all boards for active and stalled work. "
        f"2. Check feature build completion. "
        f"3. Run a RACI gap check. "
        f"4. Run a hygiene scan (report-only — never draft or send hygiene/housekeeping "
        f"comments about ticket type, Epic links, estimates, or due dates). "
        f"{auto_chase_instruction} "
        f"Synthesise everything into the Operating Brief."
    )


async def run_cycle(mode: str = "full") -> str:
    """Execute one full PMO swarm cycle."""
    start = datetime.now(timezone.utc)
    session_id = f"daemon-{start.strftime('%Y%m%d%H%M%S')}"

    log.info("=" * 80)
    log.info(f"🚀 PMO SWARM CYCLE START — {start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info("=" * 80)
    log.info(f"📋 Mode: {mode}")
    log.info(f"🏗️  Projects: {', '.join(JIRA_PROJECTS) if JIRA_PROJECTS else 'ISRDS'}")
    log.info(f"📊 Config: scan_interval={SCAN_INTERVAL}min | brief_hour={BRIEF_HOUR}:00 | auto_comment={AUTO_COMMENT}")
    log.info(f"🔄 Session ID: {session_id}")
    log.info("-" * 80)
    log.info("📌 Active Agents:")
    log.info("   1️⃣  execution_tracking_agent (DECIDE_AND_REPORT) — scan for stalled tickets")
    log.info("   2️⃣  follow_up_agent (MUST_ESCALATE) — draft chase messages")
    log.info("   3️⃣  ownership_raci_agent (MUST_ESCALATE) — audit RACI gaps")
    log.info("   4️⃣  feature_completeness_agent (DECIDE_AND_REPORT) — track feature build progress")
    log.info("   5️⃣  hygiene_agent (DECIDE_AND_REPORT) — detect field/status violations")
    log.info("-" * 80)

    prompt = _build_brief_prompt(mode)
    log.info(f"🎯 Prompt (full): {prompt}")
    log.info("-" * 80)

    # Layer 8 — trace the full swarm run in Cloud Logging + Monitoring
    try:
        with observability.trace_agent_run(
            "pmo_swarm",
            extra={"mode": mode, "session_id": session_id, "projects": JIRA_PROJECTS},
        ):
            response = await _run_prompt(prompt, session_id)
    except Exception as e:
        log.error(f"Orchestrator error: {type(e).__name__}: {e}")
        return f"ERROR: {e}"

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    log.info("-" * 80)
    log.info("📝 ORCHESTRATOR RESPONSE:")
    log.info(response[:500] + ("..." if len(response) > 500 else ""))
    log.info("-" * 80)

    # Save brief — local file (always)
    ts = start.strftime("%Y%m%d_%H%M%S")
    brief_path = BRIEF_DIR / f"brief_{ts}.txt"
    brief_path.write_text(response)
    (BRIEF_DIR / "latest.txt").write_text(response)

    log.info(f"✅ Brief saved → briefs/brief_{ts}.txt")
    log.info(f"⏱️  Execution time: {elapsed:.2f} seconds")
    observability.record_metric("briefs_generated_total", 1.0, labels={"mode": mode})

    # BigQuery — primary: Operating Brief (system of record) + cycle KPIs
    try:
        await log_operating_brief(
            cycle_ts=start,
            mode=mode,
            brief_text=response,
            duration_ms=elapsed * 1000,
        )
        log.info("📊 Operating Brief logged to BigQuery")
    except Exception as e:
        log.warning(f"BigQuery brief write failed: {e}")

    try:
        await log_cycle_metrics(
            cycle_ts=start,
            mode=mode,
            projects=JIRA_PROJECTS,
            duration_ms=elapsed * 1000,
        )
        log.info("📈 Cycle metrics logged to BigQuery")
    except Exception as e:
        log.warning(f"BigQuery metrics write failed: {e}")

    log.info("=" * 80)
    log.info(f"✨ PMO SWARM CYCLE COMPLETE — {elapsed:.2f}s total")
    log.info("=" * 80)
    print("\n" + response + "\n")
    return response


async def _run_with_startup(mode: str = "full") -> str:
    """Run startup (once) then a single cycle. Used by CLI entry points."""
    await _startup()
    return await run_cycle(mode)


def run_daemon():
    async def _loop():
        await _startup()
        log.info("=" * 60)
        log.info(f"PMO Swarm Daemon | projects: {', '.join(JIRA_PROJECTS)} | scan every {SCAN_INTERVAL}min")
        log.info(f"Auto-comment: {AUTO_COMMENT} | Brief hour: {BRIEF_HOUR}:00")
        log.info("=" * 60)

        last_brief_date = None
        while True:
            try:
                await run_cycle()
                now = datetime.now()
                if now.hour == BRIEF_HOUR and (not last_brief_date or last_brief_date != now.date()):
                    log.info("Daily brief dispatched.")
                    last_brief_date = now.date()
                log.info(f"Next cycle in {SCAN_INTERVAL} min...")
                await asyncio.sleep(SCAN_INTERVAL * 60)
            except asyncio.CancelledError:
                log.info("Daemon stopped.")
                break
            except Exception as e:
                log.error(f"{type(e).__name__}: {e}")
                await asyncio.sleep(SCAN_INTERVAL * 60)

    asyncio.run(_loop())


if __name__ == "__main__":
    if "--once" in sys.argv:
        asyncio.run(_run_with_startup("full"))
    elif "--brief" in sys.argv:
        asyncio.run(_run_with_startup("brief_only"))
    else:
        run_daemon()
