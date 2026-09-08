"""
pipeline/orchestrator.py — PipelineOrchestrator facade.

Encapsulates all LangGraph internals so that MigrationService never imports
langgraph, references node names, or sets recursion_limit directly.

Contracts:
  - resume_after_human(thread_id, decision) → raw result dict
  - resume_after_services(thread_id, services) → raw result dict
  - resume_after_correction(thread_id, rejected, reasons) → raw result dict
  - start(initial_state) → raw result dict
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("PipelineOrchestrator")

# ── Constants (single source of truth for node names and timeouts) ────────────

_NODE_ASK_HUMAN        = "ask_human"
_NODE_ASK_SERVICES     = "ask_user_services"
_NODE_ENQUEUE_DEPLOY   = "enqueue_deploy"

_TIMEOUT_ANALYSIS_SEC  = 1800   # 30 min — Agent 01
_TIMEOUT_ACCEPT_SEC    = 3600   # 60 min — Agent 02 + deploy
_TIMEOUT_CORRECTION_SEC = 600   # 10 min — partial rejection
_TIMEOUT_REJECTION_SEC  = 120   # 2 min  — ZIP export
_TIMEOUT_SERVICES_SEC   = 1800  # 30 min — manual service selection


def _config(thread_id: str, recursion_limit: int = 60) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}


def _graph():
    from pipeline.pipeline_graph import get_compiled_graph
    return get_compiled_graph()


class PipelineOrchestrator:
    """Single entry point for all LangGraph interactions."""

    async def start_analysis(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        """Run the analysis phase (analyze_repo → check_plan, pauses at ask_human)."""
        config = _config(initial_state["thread_id"])
        _t0 = time.perf_counter()
        result = await asyncio.wait_for(
            _graph().ainvoke(initial_state, config=config),
            timeout=_TIMEOUT_ANALYSIS_SEC,
        )
        logger.info("PERF start_analysis: %.2fs", time.perf_counter() - _t0)
        return result

    async def accept_plan(self, thread_id: str) -> dict[str, Any]:
        """Inject user_accepted=True and resume from ask_human."""
        config = _config(thread_id)
        await _graph().aupdate_state(config, {"user_accepted": True}, as_node=_NODE_ASK_HUMAN)
        _t0 = time.perf_counter()
        result = await asyncio.wait_for(
            _graph().ainvoke(None, config=config),
            timeout=_TIMEOUT_ACCEPT_SEC,
        )
        logger.info("PERF accept_plan (IaC+deploy): %.2fs", time.perf_counter() - _t0)
        return result

    async def reject_plan(self, thread_id: str) -> dict[str, Any]:
        """Inject user_accepted=False and resume (→ export_zip)."""
        config = _config(thread_id)
        await _graph().aupdate_state(config, {"user_accepted": False}, as_node=_NODE_ASK_HUMAN)
        return await asyncio.wait_for(
            _graph().ainvoke(None, config=config),
            timeout=_TIMEOUT_REJECTION_SEC,
        )

    async def partial_reject(
        self,
        thread_id: str,
        rejected_services: list[dict],
        rejection_reasons: dict[str, str],
    ) -> dict[str, Any]:
        """Inject partial rejection data and resume correction loop."""
        config = _config(thread_id)
        excluded = [
            svc.get("target_service")
            or svc.get("target_equivalent")
            or svc.get("resource_name", "")
            for svc in rejected_services
        ]
        await _graph().aupdate_state(
            config,
            {
                "partial_rejection": True,
                "rejected_services": rejected_services,
                "rejection_reasons": rejection_reasons,
                "excluded_services": excluded,
                "user_accepted": None,
            },
            as_node=_NODE_ASK_HUMAN,
        )
        return await asyncio.wait_for(
            _graph().ainvoke(None, config=config),
            timeout=_TIMEOUT_CORRECTION_SEC,
        )

    async def submit_services(
        self,
        thread_id: str,
        services: list[dict],
    ) -> dict[str, Any]:
        """Inject manually-selected services and resume from ask_user_services."""
        config = _config(thread_id)
        # Inject services into state WITHOUT as_node so the graph resumes
        # from the interrupt point (ask_user_services) and the node actually
        # runs — it reads user_selected_services and builds dependency_graph.
        # Using as_node=ask_user_services would skip the node and pass through
        # only the raw dict, leaving dependency_graph empty for Agent 01.
        await _graph().aupdate_state(
            config,
            {"user_selected_services": services, "needs_service_selection": False},
        )
        return await asyncio.wait_for(
            _graph().ainvoke(None, config=config),
            timeout=_TIMEOUT_SERVICES_SEC,
        )

    async def resume_after_runner(self, thread_id: str, job_id: str) -> dict[str, Any]:
        """Resume graph after executor callback (wait_runner → health_check)."""
        config = _config(thread_id)
        await _graph().aupdate_state(
            config,
            {"runner_job_id": job_id},
            as_node=_NODE_ENQUEUE_DEPLOY,
        )
        return await asyncio.wait_for(
            _graph().ainvoke(None, config=config),
            timeout=300,
        )


_orchestrator: PipelineOrchestrator | None = None


def get_orchestrator() -> PipelineOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PipelineOrchestrator()
    return _orchestrator
