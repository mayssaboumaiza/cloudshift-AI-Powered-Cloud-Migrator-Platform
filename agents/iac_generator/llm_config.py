"""
llm_config.py — Agent 02 Azure OpenAI LLM singleton factory.

Responsibilities:
  - Configure AzureChatOpenAI for Agent 02 (temperature=0, max_tokens=4096).
  - Expose _AZURE_TIMEOUT used by all HTTP-bound operations in this package.

Why a separate module?
  _get_llm_02() is called by react_loop.py and python_migrator.py.
  Keeping it here (not in generator.py) prevents circular imports since
  generator.py imports from both of those modules.
"""

from __future__ import annotations

import logging
import os

import httpx
from langchain_openai import AzureChatOpenAI

from configuration.settings import settings

logger = logging.getLogger("Agent02")

_AZURE_TIMEOUT = httpx.Timeout(
    timeout=float(os.getenv("AZURE_REQUEST_TIMEOUT", "180")),
    connect=15.0,
)


def _get_llm_rag() -> AzureChatOpenAI:
    """LLM for the GraphRAG chat assistant.

    Deployment read from .env: AZURE_MODEL_RAG (default gpt-4o) → AZURE_MODEL_02
    → AZURE_MODEL → settings.AZURE_MODEL_RAG. The chatbot only summarises
    retrieved context, so a lighter model (gpt-4o) is intentional here, distinct
    from the agents (gpt-5.1). Temperature=0.3 for slightly more natural
    conversational answers (vs 0 for deterministic codegen).
    """
    # RAG runs on its own Azure deployment (gpt-4o), which may live on a
    # SEPARATE Azure endpoint/key than the three agents (gpt-5.1). We read
    # the RAG-specific vars first and fall back to the global ones, so a
    # single-endpoint setup keeps working unchanged.
    api_version = (
        os.getenv("AZURE_OPENAI_API_VERSION_RAG")
        or os.getenv("AZURE_OPENAI_API_VERSION")
        or settings.AZURE_OPENAI_API_VERSION
    )
    endpoint    = (
        os.getenv("AZURE_AI_ENDPOINT_RAG")
        or os.getenv("AZURE_AI_ENDPOINT")
        or os.getenv("AZURE_OPENAI_ENDPOINT", "")
    )
    api_key     = (
        os.getenv("AZURE_AI_API_KEY_RAG")
        or os.getenv("AZURE_AI_API_KEY")
        or os.getenv("AZURE_OPENAI_API_KEY", "")
    )
    deployment  = (
        os.getenv("AZURE_MODEL_RAG")
        or settings.AZURE_MODEL_RAG
    )
    if not endpoint or not api_key:
        raise RuntimeError("AZURE_AI_ENDPOINT and AZURE_AI_API_KEY must be set for RAG chat.")
    logger.info("GraphRAG chat: deployment=%s", deployment)
    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        azure_deployment=deployment,
        api_version=api_version,
        temperature=0.3,
        max_tokens=1024,
        timeout=_AZURE_TIMEOUT,
    )


def _get_llm_02() -> AzureChatOpenAI:
    """Create a fresh AzureChatOpenAI instance for Agent 02 ReAct.

    max_tokens=4096 matches the default Azure deployment cap.
    Higher values (e.g. 16384) caused silent client-side rejection on
    standard deployments — see incident 'Agent02 ReAct iter 1 returned
    in 26ms with 0 tool calls'. Override via AZURE_MAX_TOKENS env var
    if your deployment supports a higher per-request output budget.
    """
    # Deployment name read from .env (AZURE_MODEL_02 → AZURE_MODEL → settings default = gpt-5.1)
    api_version = os.getenv("AZURE_OPENAI_API_VERSION") or settings.AZURE_OPENAI_API_VERSION
    endpoint    = os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT", "")
    api_key     = os.getenv("AZURE_AI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY", "")
    deployment  = os.getenv("AZURE_MODEL_02") or os.getenv("AZURE_MODEL") or os.getenv("AZURE_OPENAI_DEPLOYMENT") or settings.AZURE_MODEL
    max_tokens  = int(os.getenv("AZURE_MAX_TOKENS", "4096"))

    if not endpoint or not api_key:
        raise RuntimeError(
            "AZURE_AI_ENDPOINT and AZURE_AI_API_KEY must be set — "
            "Agent 02 cannot run without Azure OpenAI credentials."
        )
    logger.info(
        f"Agent02: AzureChatOpenAI endpoint={endpoint[:60]}… "
        f"deployment={deployment} api_version={api_version} max_tokens={max_tokens}"
    )
    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        azure_deployment=deployment,
        api_version=api_version,
        temperature=0,
        max_tokens=max_tokens,
        timeout=_AZURE_TIMEOUT,
    )
