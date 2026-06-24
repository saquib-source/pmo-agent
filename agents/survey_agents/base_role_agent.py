"""
BaseRoleAgent — all ISRDS Role Category agents extend this.

Responsibilities:
  - Loads RoleConfig from Config Registry at init
  - Builds the ADK LlmAgent with the correct engine binding
  - Writes to Trust Ledger on every decision
  - Fires escalation to Pub/Sub for MUST_ESCALATE decisions
  - Stores session memory in pgvector at session end
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

import config_registry
from config_registry import RoleConfig


class BaseRoleAgent:
    """
    Extend this class for every Role Category agent.

    Usage:
        class SurveyAgent(BaseRoleAgent):
            ROLE_CATEGORY = "Empathic Interview Agent"

            def _build_tools(self) -> list:
                from tools.survey_tools import SURVEY_TOOLS
                return SURVEY_TOOLS
    """

    ROLE_CATEGORY: str = ""  # subclasses must set this

    def __init__(self, tenant_id: str, session_id: Optional[str] = None):
        if not self.ROLE_CATEGORY:
            raise NotImplementedError("Subclass must define ROLE_CATEGORY")

        self.tenant_id = tenant_id
        self.session_id = session_id or str(uuid.uuid4())
        self.trace_id = str(uuid.uuid4())
        self.config: Optional[RoleConfig] = None
        self._agent: Optional[LlmAgent] = None
        self._pool: Optional[asyncpg.Pool] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialise(self) -> "BaseRoleAgent":
        """Call before first use. Loads config and wires ADK agent."""
        self.config = await config_registry.load(self.ROLE_CATEGORY)
        self._pool = await config_registry.get_pool()
        self._agent = self._build_adk_agent()
        return self

    def _build_adk_agent(self) -> LlmAgent:
        tools = self._build_tools()
        return LlmAgent(
            name=self.ROLE_CATEGORY.replace(" ", "_").lower(),
            model=self.config.engine_binding,
            instruction=self.config.system_prompt or "",
            tools=tools,
        )

    def _build_tools(self) -> list:
        """Subclasses override to return their tool list."""
        return []

    # ── Run ───────────────────────────────────────────────────────────────────

    async def run(self, user_message: str, **kwargs) -> str:
        """
        Execute one agent turn. Checks authority gradient before acting.
        Returns the agent's text response.
        """
        if self._agent is None:
            raise RuntimeError("Call await agent.initialise() before run()")

        # Authority gradient check — subclasses can override _pre_run_check
        await self._pre_run_check(user_message, **kwargs)

        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part

        session_service = InMemorySessionService()
        runner = Runner(
            agent=self._agent,
            app_name=f"isrds_{self.ROLE_CATEGORY.replace(' ', '_').lower()}",
            session_service=session_service,
        )

        session = await session_service.create_session(
            app_name=runner.app_name,
            user_id=self.tenant_id,
            session_id=self.session_id,
        )

        content = Content(role="user", parts=[Part(text=user_message)])
        response_text = ""

        async for event in runner.run_async(
            user_id=self.tenant_id,
            session_id=self.session_id,
            new_message=content,
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    # ── Authority Gradient ────────────────────────────────────────────────────

    async def _pre_run_check(self, message: str, **kwargs):
        """
        Override in subclasses for context-specific gradient checks.
        Default: log the incoming task.
        """
        pass

    async def escalate(self, context: dict, decision_class: str = "MUST_ESCALATE"):
        """Publish escalation to Pub/Sub and write to escalation_queue."""
        from google.cloud import pubsub_v1

        topic = os.environ.get("PUBSUB_ESCALATION_TOPIC", "isrds-escalation-v1")
        project = os.environ["GOOGLE_CLOUD_PROJECT"]

        payload = {
            "tenant_id": self.tenant_id,
            "role_category": self.ROLE_CATEGORY,
            "decision_class": decision_class,
            "context": context,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(project, topic)
        publisher.publish(topic_path, json.dumps(payload).encode())

        # Write to escalation_queue
        async with self._pool.acquire() as conn:
            await conn.execute("SET app.tenant_id = $1", self.tenant_id)
            await conn.execute(
                """
                INSERT INTO escalation_queue
                  (tenant_id, role_category, decision_class, context)
                VALUES ($1, $2, $3, $4)
                """,
                self.tenant_id,
                self.ROLE_CATEGORY,
                decision_class,
                json.dumps(context),
            )

    # ── Trust Ledger ──────────────────────────────────────────────────────────

    async def record_decision(
        self,
        event_type: str,
        outcome: Optional[str] = None,
        evidence: Optional[dict] = None,
        swarm_id: Optional[str] = None,
    ) -> str:
        """Write one row to the Trust Ledger. Returns the new row ID."""
        async with self._pool.acquire() as conn:
            await conn.execute("SET app.tenant_id = $1", self.tenant_id)
            row = await conn.fetchrow(
                """
                INSERT INTO trust_ledger
                  (tenant_id, swarm_id, role_category, event_type, decision_class, outcome, evidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                self.tenant_id,
                swarm_id,
                self.ROLE_CATEGORY,
                event_type,
                self.config.decision_class,
                outcome,
                json.dumps(evidence or {}),
            )
        return str(row["id"])

    # ── Tool Call Audit ───────────────────────────────────────────────────────

    async def audit_tool_call(
        self,
        tool_name: str,
        input_data: Any,
        output_data: Any,
        duration_ms: int,
        status: str = "SUCCESS",
    ):
        def _hash(data: Any) -> str:
            return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tool_call_audit
                  (trace_id, role_category, tool_name, input_hash, output_hash, duration_ms, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                self.trace_id,
                self.ROLE_CATEGORY,
                tool_name,
                _hash(input_data),
                _hash(output_data),
                duration_ms,
                status,
            )

    # ── Memory ────────────────────────────────────────────────────────────────

    async def save_session_memory(self, summary: str, metadata: Optional[dict] = None):
        """
        Embed the session summary and store in agent_memory for future retrieval.
        Uses Vertex AI text-embedding-004.
        """
        embedding = await self._embed(summary)
        async with self._pool.acquire() as conn:
            await conn.execute("SET app.tenant_id = $1", self.tenant_id)
            await conn.execute(
                """
                INSERT INTO agent_memory
                  (tenant_id, role_category, session_id, summary, embedding, metadata)
                VALUES ($1, $2, $3, $4, $5::vector, $6)
                """,
                self.tenant_id,
                self.ROLE_CATEGORY,
                self.session_id,
                summary,
                str(embedding),  # asyncpg passes as text; pgvector casts
                json.dumps(metadata or {}),
            )

    async def retrieve_similar_memories(self, query: str, top_k: int = 3) -> list[dict]:
        """Return top_k most similar past session summaries for this tenant + role."""
        embedding = await self._embed(query)
        async with self._pool.acquire() as conn:
            await conn.execute("SET app.tenant_id = $1", self.tenant_id)
            rows = await conn.fetch(
                """
                SELECT summary, metadata, created_at,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM agent_memory
                WHERE tenant_id    = $2
                  AND role_category = $3
                ORDER BY embedding <=> $1::vector
                LIMIT $4
                """,
                str(embedding),
                self.tenant_id,
                self.ROLE_CATEGORY,
                top_k,
            )
        return [dict(r) for r in rows]

    async def _embed(self, text: str) -> list[float]:
        """Generate embedding via Vertex AI text-embedding-004."""
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        model = TextEmbeddingModel.from_pretrained(
            os.environ.get("VERTEX_EMBEDDING_MODEL", "text-embedding-004")
        )
        result = model.get_embeddings([text])
        return result[0].values
