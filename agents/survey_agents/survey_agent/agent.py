"""
EmpathicInterviewAgent — Survey Agent (Tier 2)

Role Category : Empathic Interview Agent
Engine        : claude-sonnet-4-6 (from Config Registry)
Memory        : pgvector + session (from Config Registry)
Authority     : DECIDE_SILENTLY (standard turns) / MUST_ESCALATE (distress)
Tools         : SURVEY_TOOLS (wrapping NestJS /surveys endpoints)

Usage:
    agent = await EmpathicInterviewAgent(tenant_id="t_abc123").initialise()
    result = await agent.conduct_survey(
        survey_type="POST_APPOINTMENT",
        subject_type="CONTACT",
        subject_id="contact_xyz",
        subject_name="Jane Smith",
        channel="SMS",
        channel_ref="+15551234567",
        appointment_context="HVAC maintenance, completed 2024-05-28",
    )
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from base_role_agent import BaseRoleAgent
from tools.survey_tools import SURVEY_TOOLS


class EmpathicInterviewAgent(BaseRoleAgent):

    ROLE_CATEGORY = "Empathic Interview Agent"

    def _build_tools(self) -> list:
        return SURVEY_TOOLS

    # ── Main entry point ──────────────────────────────────────────────────────

    async def conduct_survey(
        self,
        survey_type: str,
        subject_type: str,
        subject_id: str,
        subject_name: str,
        channel: str,
        channel_ref: Optional[str] = None,
        appointment_context: Optional[str] = None,
    ) -> dict:
        """
        Run a full survey conversation. Returns a result dict with
        session_id, completed, sentiment_score, summary, trust_ledger_id.
        """
        # 1. Retrieve prior responses for this subject (continuity)
        prior_context = ""
        try:
            from tools.survey_tools import get_prior_responses
            prior = await get_prior_responses(self.tenant_id, subject_type, subject_id)
            sessions = prior.get("sessions", [])
            if sessions:
                last = sessions[0]
                prior_context = (
                    f"\n\nContext from last interaction with {subject_name} "
                    f"({last.get('initiatedAt', 'previously')}): {last.get('summary', '')}"
                )
        except Exception:
            pass  # no prior — that's fine

        # 2. Also retrieve similar pgvector memories
        memory_context = ""
        try:
            memories = await self.retrieve_similar_memories(
                f"{survey_type} {subject_name} {appointment_context or ''}",
                top_k=2,
            )
            if memories:
                memory_context = "\n\nRelated past sessions:\n" + "\n".join(
                    f"- {m['summary']}" for m in memories
                )
        except Exception:
            pass

        # 3. Build the initial prompt
        prompt = self._build_opening_prompt(
            survey_type=survey_type,
            subject_name=subject_name,
            channel=channel,
            appointment_context=appointment_context,
            prior_context=prior_context,
            memory_context=memory_context,
        )

        # 4. Run the ADK agent
        t_start = time.monotonic()
        response = await self.run(prompt)
        duration_ms = int((time.monotonic() - t_start) * 1000)

        # 5. Parse structured result from agent response
        result = self._parse_agent_result(response)

        # 6. Record in Trust Ledger
        trust_id = await self.record_decision(
            event_type="SURVEY_SESSION_COMPLETED" if result.get("completed") else "SURVEY_SESSION_ABANDONED",
            outcome="POSITIVE" if (result.get("sentiment_score") or 0) >= 0.2 else
                    "NEGATIVE" if (result.get("sentiment_score") or 0) <= -0.2 else "NEUTRAL",
            evidence={
                "session_id": result.get("session_id"),
                "survey_type": survey_type,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "turns_completed": result.get("turns_completed", 0),
                "distress_flagged": result.get("distress_flagged", False),
                "duration_ms": duration_ms,
            },
        )

        # 7. Save session memory
        if result.get("summary"):
            await self.save_session_memory(
                summary=result["summary"],
                metadata={
                    "survey_type": survey_type,
                    "subject_id": subject_id,
                    "sentiment_score": result.get("sentiment_score"),
                },
            )

        result["trust_ledger_id"] = trust_id
        return result

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_opening_prompt(
        self,
        survey_type: str,
        subject_name: str,
        channel: str,
        appointment_context: Optional[str],
        prior_context: str,
        memory_context: str,
    ) -> str:

        context_lines = []
        if appointment_context:
            context_lines.append(f"Appointment context: {appointment_context}")
        if prior_context:
            context_lines.append(prior_context)
        if memory_context:
            context_lines.append(memory_context)
        context_block = "\n".join(context_lines)

        survey_goal = {
            "POST_APPOINTMENT":    "understand how the appointment went and how the customer feels about the service",
            "LEAD_QUALIFICATION":  "understand the lead's needs and priorities to qualify them for the right solution",
            "WELLNESS_CHECK":      "check in on the customer's overall experience with ISRDS and identify any unresolved concerns",
        }.get(survey_type, "gather feedback")

        channel_note = {
            "SMS":    "You are communicating via SMS. Keep each message short (under 160 characters). Wait for a reply before sending the next question.",
            "IN_APP": "You are communicating through the ISRDS customer app. You can be slightly more conversational.",
            "EMAIL":  "You are communicating via email. You may ask 2-3 questions at once if they naturally group together.",
        }.get(channel, "")

        return f"""
You are conducting a {survey_type.replace('_', ' ').lower()} survey with {subject_name}.

Goal: {survey_goal}.

{channel_note}

{context_block}

Begin the conversation now. Introduce yourself warmly as the ISRDS team reaching out.
Ask your first question. After each response you receive, decide the next question based
on what they said — do not follow a rigid script.

At the end of the session (after at most 7 turns or when you have enough information),
output a JSON block with this exact structure inside triple backticks:

```json
{{
  "session_id": "<the session_id returned by create_survey_session>",
  "completed": true,
  "turns_completed": <number>,
  "sentiment_score": <float from -1.0 to 1.0>,
  "distress_flagged": false,
  "summary": "<2-3 sentence summary of the conversation>",
  "structured_data": {{
    "overall_satisfaction": "<high|medium|low|unknown>",
    "key_concern": "<main concern raised or null>",
    "follow_up_needed": <true|false>,
    "follow_up_reason": "<reason or null>"
  }}
}}
```
""".strip()

    # ── Result parser ─────────────────────────────────────────────────────────

    def _parse_agent_result(self, response: str) -> dict:
        """Extract the JSON result block from the agent's final response."""
        import re
        match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # Fallback — return minimal result
        return {
            "completed": False,
            "turns_completed": 0,
            "sentiment_score": None,
            "distress_flagged": False,
            "summary": response[:500],
            "structured_data": {},
        }

    # ── Distress override ─────────────────────────────────────────────────────

    async def _pre_run_check(self, message: str, **kwargs):
        """
        No pre-run checks needed for Survey Agent.
        Distress detection happens inside the agent via the flag_distress_signal tool.
        """
        pass
