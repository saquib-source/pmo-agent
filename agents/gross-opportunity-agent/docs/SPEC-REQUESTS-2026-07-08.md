# Note to the Specification Side — Gross Opportunity Surface

**From:** Build side (Saquib, reporting to Manmeet; drafted with the build assistant)
**To:** 9.1 / specification side (Todd's specification assistant — "your Claude controls specifications")
**Date:** 2026-07-08
**Protocol:** Per Todd's direction — nothing below has been invented or hard-coded by the build side. Where a decision is needed, it is framed as a **specification request** and we have built nothing until you answer. Genuine defects inside *existing* spec are listed separately and we are fixing those.

---

## 1. What triggered this note

Reviewing the record **"Torchy's Tacos"** (source: Emailed Bid Leads / BAS-5, direct ITB email from Embree Group) on the review screen exposed four distinct issues at once:

1. The **"Pull full report from SAM.gov"** button did nothing for this record.
2. The button's **label names one source** while the queue now carries **mixed sources** (SAM.gov + Emailed Bid Leads, more coming per BAS-5).
3. The reviewer **cannot see why the record is in the set** — its scope text ("Invitation to bid — accept/decline/view drawings link in email") is stored in the raw record but never displayed; location/bid date/valuation are all legitimately null (Data Contract v1.0 honesty), so the card shows almost nothing to evaluate.
4. The decision trail displayed a **raw engine-vendor error message** to the reviewer ("Your credit balance is too low to access the Anthropic API…").

## 2. What we verified before writing (facts, with code locations)

- The full-report **backend is already source-generic** — it walks `source_link`s, resolves each source's adapter from the registry, and calls `adapter.fetch_full()` with per-source budget enforcement (`goa/events/full_report.py:63-110`). It is *not* hard-wired to SAM.gov.
- The **gap is capability + presentation**: only `SamGovAdapter` implements `fetch_full`; the base contract raises `NotImplementedError` (`goa/adapters/base.py:63-68`), so for an emailed lead the job dead-ends, and the failure is not surfaced to the reviewer. The **button label** "Pull full report from SAM.gov" is a hard-coded string in the screen (`screen/index.html:455`) — that label is the only SAM-specific hardcoding.
- The **scope words are NOT hard-coded**: "shower / shower doors / toilet partitions / …" live in configuration — the seeded division scope (`jobs/seed.py` SCOPE, stored in the `scope` table) and the SAM.gov `query_plan.keywords` (`config/sources/sam_gov.json`). They came from the build spec / implementation plan as the defensible starting scope. What is missing is a **governance spec** for who owns that list and a single canonical place all consumers read it from (today: gate reads the `scope` table; the SAM.gov query plan keeps its own keyword list).
- The **vendor error in the UI** comes from the gate's recall-first fallback storing the raw exception string as the classifier reason (`goa/gate/classifier.py:75`) which the screen renders verbatim. Two problems: reviewers see operator-level errors, and a **vendor name reaches a client-facing surface** (vendor-neutrality is a golden rule of this platform).
- The Torchy's record itself is honest: every displayed null is a true unknown from Meri's sheet. The record was **kept at score 0.40 by the recall-first policy** because the classifier was down (engine account out of credits) — by design, uncertainty keeps a record for human review rather than dropping it.

## 3. Specification requests (decisions we need from you — we will not decide these)

**SR-1 · Full-record retrieval for mixed sources.**
What does "pull the full report" *mean* per source class?
- REST API sources (SAM.gov): fetch the full notice + attachments — implemented today.
- Emailed/file-drop leads: the "full record" may be (a) nothing beyond what arrived, (b) the RFP link/portal page referenced in the email, or (c) a request-more-info workflow back to the sender.
Please specify: the per-source **capability declaration** (we suggest it belongs in the source config, since everything must be configurable), the **button behavior when a source has no such capability** (hidden / disabled with reason / alternate action), and the **label** (presumably from the source registry, not a literal).

**SR-2 · Inclusion rationale on the review screen.**
Reviewers need to see *why* a record is in the set: the scope/description text the gate evaluated, which rule(s) or keyword(s) matched, and the alert keyword that produced portal invitations. The data exists (raw record `description`, `agent_trace.gate.matched_rules`) but the contract does not define a reviewer-facing field or its display. Please specify the field(s) and their place in Data Contract (v1.0 addendum or v1.1) — e.g. `scope_text` and `inclusion_rationale`.

**SR-3 · Minimum-evaluability / enrichment state.**
Records like Torchy's Tacos are real but arrive too thin to evaluate (no location, no date, no scope detail). Today they sit in the queue like any other record. Should the contract define an explicit state (e.g. `needs_enrichment`) and/or an enrichment step (follow the ITB link, ask the sender), so reviewers can filter them? We have not built anything here.

**SR-4 · Degraded-mode messaging.**
When the model layer is unavailable, the recall-first behavior is specified and working — but *what the reviewer sees* is not. Please specify the reviewer-facing wording for "kept by policy because the classifier was unavailable" (we will sanitize the raw error regardless — see §4).

**SR-5 · Scope-keyword governance.**
Confirm the canonical owner and single source of truth for the BASCO product scope list (currently: seeded `scope` table + per-source query plans), and the change process (who approves adding e.g. "glazing"). We widened the SAM.gov query plan to 5 keywords for the demo window under Todd's "more real records" directive — please ratify or amend that list: `partition, shower, restroom renovation, glazing, bathroom`.

**SR-6 · Engine unavailability operations.**
The engine account (Anthropic transport) is out of credits. Options that exist in config today: top up the account, or flip `GOA_ENGINE_TRANSPORT=vertex` (Config Registry already binds all roles vendor-neutrally; Vertex requires the model enabled in Model Garden). Please specify the preferred transport and the alerting expectation when an engine is unavailable (today it degrades silently except in the decision trail).

## 4. Defects we are fixing now (inside existing spec — no new decisions)

| # | Defect | Fix |
|---|---|---|
| F-1 | Raw vendor/exception text stored as `classifier_reason` and shown to reviewers (`goa/gate/classifier.py:75`) | Store a neutral reason ("classifier unavailable — kept by recall-first policy") and log the raw error to `agent_activity` at `warn` for operators. No vendor strings on client-facing surfaces. |
| F-2 | Full-report failure is silent in the UI (button appears dead; `fetch_state` failure not explained) | Surface the API/job failure state on the screen (existing `fetch_state=failed` chip + the activity log line already written by the job). |
| F-3 | Engine account has no credits | Ops with Manmeet: top up or transport flip per SR-6 — no code change. |

We are deliberately **not** re-labeling or hiding the full-report button, not adding capability flags, and not displaying new fields until SR-1/SR-2 come back — per the no-invented-specs rule.

## 5. Standing guardrails we propose to adopt (process, not product decisions)

- Extend `quality_gate.py` to also scan **UI surfaces and stored trace fields** for vendor strings (the golden rule today checks specs/configs; F-1 shows client-facing leaks are possible).
- Any string a reviewer can see must come from **data or config**, never a literal naming a source or vendor.
- New sources arrive only via config + `jobs/register_source.py`; human APPROVE recorded by the `--enable` flag (already the pattern).
- Anything ambiguous → a numbered SR in a note like this one, before code.

---
*Build side contact: Saquib (reports to Manmeet). This note is the requested channel: our assistant → your assistant.*
