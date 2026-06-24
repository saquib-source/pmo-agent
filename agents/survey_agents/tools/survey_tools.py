"""
Survey Tools — ADK FunctionTools that wrap the NestJS surveys API.
These are the tools the EmpathicInterviewAgent calls during a session.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from google.adk.tools import FunctionTool

# ── HTTP client ───────────────────────────────────────────────────────────────

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=os.environ["NESTJS_API_BASE_URL"],
        headers={"x-agent-api-key": os.environ["NESTJS_AGENT_API_KEY"]},
        timeout=15.0,
    )


# ── Tool functions ────────────────────────────────────────────────────────────

async def get_survey_template(tenant_id: str, survey_type: str) -> dict:
    """
    Fetch the active survey template for a given tenant and survey type.

    Args:
        tenant_id:   The tenant this survey belongs to.
        survey_type: One of POST_APPOINTMENT, LEAD_QUALIFICATION, WELLNESS_CHECK.

    Returns:
        The survey template with id, title, description, and question_tree.
    """
    async with _client() as client:
        resp = await client.get(
            "/surveys/templates/active",
            params={"tenantId": tenant_id, "surveyType": survey_type},
        )
        resp.raise_for_status()
        return resp.json()


async def create_survey_session(
    tenant_id: str,
    template_id: str,
    survey_type: str,
    subject_type: str,
    subject_id: str,
    subject_name: str,
    channel: str,
    channel_ref: Optional[str] = None,
    adk_session_id: Optional[str] = None,
) -> dict:
    """
    Create a new survey session in the database before starting the conversation.

    Args:
        tenant_id:      Tenant ID.
        template_id:    UUID of the survey template to use.
        survey_type:    POST_APPOINTMENT | LEAD_QUALIFICATION | WELLNESS_CHECK.
        subject_type:   CONTACT | LEAD | WORKORDER.
        subject_id:     ID of the contact, lead, or workorder being surveyed.
        subject_name:   Display name of the subject.
        channel:        SMS | IN_APP | EMAIL.
        channel_ref:    Phone number or email address.
        adk_session_id: The Vertex AI Agent Engine session ID.

    Returns:
        The created session with id and status.
    """
    async with _client() as client:
        resp = await client.post(
            "/surveys/sessions",
            json={
                "tenantId": tenant_id,
                "templateId": template_id,
                "surveyType": survey_type,
                "subjectType": subject_type,
                "subjectId": subject_id,
                "subjectName": subject_name,
                "channel": channel,
                "channelRef": channel_ref,
                "adkSessionId": adk_session_id,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def save_response(
    session_id: str,
    tenant_id: str,
    turn_number: int,
    question_id: str,
    question_text: str,
    answer_text: str,
    answer_type: str,
    sentiment: str,
    distress_flag: bool = False,
) -> dict:
    """
    Save one question-answer turn from the current survey session.

    Args:
        session_id:    Survey session UUID.
        tenant_id:     Tenant ID.
        turn_number:   Sequential turn number (1-based).
        question_id:   Question ID from the template question_tree.
        question_text: The question as asked by the agent.
        answer_text:   The customer's answer.
        answer_type:   FREE_TEXT | CHOICE | NUMERIC | SKIP.
        sentiment:     POSITIVE | NEUTRAL | NEGATIVE | DISTRESS.
        distress_flag: True if agent detected distress in this response.

    Returns:
        The saved response with id.
    """
    async with _client() as client:
        resp = await client.post(
            f"/surveys/sessions/{session_id}/responses",
            json={
                "tenantId": tenant_id,
                "turnNumber": turn_number,
                "questionId": question_id,
                "questionText": question_text,
                "answerText": answer_text,
                "answerType": answer_type,
                "sentiment": sentiment,
                "distressFlag": distress_flag,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def complete_survey_session(
    session_id: str,
    tenant_id: str,
    sentiment_score: float,
    summary: str,
    structured_data: dict,
) -> dict:
    """
    Mark the survey session as completed and store the agent's summary and
    structured data extract.

    Args:
        session_id:      Survey session UUID.
        tenant_id:       Tenant ID.
        sentiment_score: Overall sentiment score from -1.0 (very negative) to 1.0 (very positive).
        summary:         Agent's natural-language summary of the session.
        structured_data: Key-value pairs extracted from responses (for Data Assimilation).

    Returns:
        The updated session.
    """
    async with _client() as client:
        resp = await client.patch(
            f"/surveys/sessions/{session_id}/complete",
            json={
                "tenantId": tenant_id,
                "sentimentScore": sentiment_score,
                "summary": summary,
                "structuredData": structured_data,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def flag_distress_signal(
    session_id: str,
    tenant_id: str,
    context: str,
) -> dict:
    """
    Flag a distress signal detected during the session. This triggers an
    escalation to the Supervisor-Trainer and gracefully ends the survey.

    Args:
        session_id: Survey session UUID.
        tenant_id:  Tenant ID.
        context:    Description of what triggered the distress flag.

    Returns:
        Confirmation with escalation_id.
    """
    async with _client() as client:
        resp = await client.post(
            f"/surveys/sessions/{session_id}/distress",
            json={"tenantId": tenant_id, "context": context},
        )
        resp.raise_for_status()
        return resp.json()


async def get_prior_responses(
    tenant_id: str,
    subject_type: str,
    subject_id: str,
    limit: int = 3,
) -> dict:
    """
    Retrieve prior survey responses for a subject (for continuity context).
    Call this at the start of a session if the subject has been surveyed before.

    Args:
        tenant_id:    Tenant ID.
        subject_type: CONTACT | LEAD | WORKORDER.
        subject_id:   ID of the subject.
        limit:        Max number of past sessions to return (default 3).

    Returns:
        List of past sessions with their summaries and structured_data.
    """
    async with _client() as client:
        resp = await client.get(
            "/surveys/sessions/prior",
            params={
                "tenantId": tenant_id,
                "subjectType": subject_type,
                "subjectId": subject_id,
                "limit": limit,
            },
        )
        resp.raise_for_status()
        return resp.json()


# ── Export as ADK FunctionTools ───────────────────────────────────────────────

SURVEY_TOOLS = [
    FunctionTool(get_survey_template),
    FunctionTool(create_survey_session),
    FunctionTool(save_response),
    FunctionTool(complete_survey_session),
    FunctionTool(flag_distress_signal),
    FunctionTool(get_prior_responses),
]
