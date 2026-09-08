"""
plan_execution.py — Agent 01 ReAct loop and plan extraction.

Responsibilities:
  - Extract and parse the migration plan JSON from LLM message history.
  - Run the ReAct batch loop: iterates up to 3 times per batch, injecting
    validation feedback, handling rate-limits with exponential backoff,
    and returning normalized service dicts.

Used by planner.py (run_agent_01, run_agent_01_correction).
Imports from plan_validation (structural validation + normalization).
"""

from __future__ import annotations

import json
import logging
import time

from langchain_core.messages import HumanMessage, AIMessage

from agents.migration_planner.plan_validation import (
    _validate_plan_inline,
    _normalize_service,
)

logger = logging.getLogger("Agent01")


# ─────────────────────────────────────────────────────────────────────────────
# Plan extraction from LLM message history
# ─────────────────────────────────────────────────────────────────────────────

def _extract_migration_plan(result: dict) -> dict | None:
    """Scan the message list in reverse to find the first valid 'services' JSON.

    Accepts both 'services' (v2) and 'resources' (legacy fallback) keys.
    Returns None if no valid plan JSON is found.
    """
    import re

    def _try_json(text: str) -> dict | None:
        if not text:
            return None
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            pass
        m = re.search(r"```(?:json)?\s*(\{[^`]{0,100000}?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        m = re.search(r"\{.{0,100000}\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    for msg in reversed(result.get("messages", [])):
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        if not content.strip():
            continue
        parsed = _try_json(content)
        if not isinstance(parsed, dict):
            continue

        if "services" in parsed and isinstance(parsed["services"], list):
            return parsed

        # Legacy format: 'resources' instead of 'services'
        if "resources" in parsed and isinstance(parsed["resources"], list):
            logger.warning("Agent01: LLM returned 'resources' instead of 'services' — adapting")
            services = []
            for r in parsed["resources"]:
                r_copy = r.copy()
                r_copy.setdefault("service_name", r.get("source_service") or r.get("resource_name", ""))
                r_copy.setdefault("strategy_7r", r.get("strategy", "REPLATFORM"))
                r_copy.setdefault("breaking_changes", [])
                r_copy.setdefault("equivalence_score", 0.7)
                r_copy.setdefault("confidence", 0.7)
                r_copy.setdefault("reasoning", "Adapted from legacy format")
                services.append(r_copy)
            parsed["services"] = services
            return parsed

    return None


# ─────────────────────────────────────────────────────────────────────────────
# ReAct batch loop with feedback injection and rate-limit backoff
# ─────────────────────────────────────────────────────────────────────────────

def _run_batch_with_react_loop(
    batch_nodes:       list[dict],
    batch_edges:       list[dict],
    category:          str,
    react_agent,
    source_cloud:      str,
    target_cloud:      str,
    migration_context: dict,
    state:             dict,
) -> list[dict]:
    """Run the ReAct planning loop for a single category batch.

    - Max 3 iterations per batch.
    - Injects structured validation feedback when JSON is invalid or fails checks.
    - Handles Azure OpenAI rate-limits with exponential backoff (max 5 retries).
    - Caps batch size at 20 nodes to prevent token overflow.
    - Returns a list of normalized service dicts, or [] on unrecoverable failure.
    """
    MAX_ITERATIONS   = 3
    MAX_RATE_RETRIES = 5
    MAX_NODES_PER_BATCH = 20

    iteration        = 0
    rate_retries     = 0
    feedback_message: str | None = None

    user_context: dict = {
        "migration_context": migration_context,
        "dependency_graph": {
            "nodes": batch_nodes,
            "edges": batch_edges,
        },
    }

    correction_ctx = state.get("_correction_context")
    if correction_ctx:
        user_context["correction_context"] = correction_ctx

    if len(batch_nodes) > MAX_NODES_PER_BATCH:
        logger.warning(
            f"Agent01 batch '{category}': {len(batch_nodes)} nodes > {MAX_NODES_PER_BATCH} — truncating"
        )
        batch_nodes = batch_nodes[:MAX_NODES_PER_BATCH]

    while iteration < MAX_ITERATIONS:
        if feedback_message is None:
            msg = HumanMessage(content=json.dumps(user_context, indent=2))
        else:
            ctx_with_feedback = {
                "migration_context": user_context["migration_context"],
                "dependency_graph":  user_context["dependency_graph"],
                "FEEDBACK":  feedback_message[:800],
                "attempt":   f"{iteration + 1}/{MAX_ITERATIONS}",
            }
            if "correction_context" in user_context:
                ctx_with_feedback["correction_context"] = user_context["correction_context"]
            msg = HumanMessage(content=json.dumps(ctx_with_feedback, indent=2))

        try:
            res  = react_agent.invoke(
                {"messages": [msg]},
                config={"recursion_limit": 40},
            )
            plan = _extract_migration_plan(res)

            if plan is None:
                messages_list = res.get("messages") or [None]
                last_msg = messages_list[-1]
                raw = (last_msg.content if hasattr(last_msg, "content") else str(last_msg))[:300]
                iteration += 1
                if iteration >= MAX_ITERATIONS:
                    logger.error(
                        f"Agent01 batch '{category}': MAX_ITERATIONS reached "
                        "(unparseable JSON) — batch discarded."
                    )
                    return []
                feedback_message = (
                    f"JSON non parsable. Output reçu :\n{raw}\n\n"
                    "Réponds UNIQUEMENT avec un JSON brut (sans markdown)."
                )
                continue

            errors = _validate_plan_inline(plan)
            if errors:
                iteration += 1
                if iteration >= MAX_ITERATIONS:
                    logger.error(
                        f"Agent01 batch '{category}': MAX_ITERATIONS reached "
                        f"(validation errors: {errors[:2]}) — batch discarded."
                    )
                    return []
                feedback_message = (
                    "Erreurs de validation :\n"
                    + "\n".join(f"  - {e}" for e in errors[:5])
                    + "\n\nCorrige uniquement ces erreurs."
                )
                continue

            raw_services = plan.get("services", [])

            # Guard: empty services list for a non-empty batch is a silent LLM failure.
            if not raw_services and batch_nodes:
                iteration += 1
                node_ids = [n.get("id") or n.get("service_name", "?") for n in batch_nodes[:5]]
                logger.warning(
                    f"Agent01 batch '{category}': LLM returned services=[] "
                    f"for {len(batch_nodes)} node(s) — requesting retry ({iteration}/{MAX_ITERATIONS})"
                )
                if iteration >= MAX_ITERATIONS:
                    logger.error(
                        f"Agent01 batch '{category}': MAX_ITERATIONS reached "
                        f"(empty services for {len(batch_nodes)} nodes) — batch discarded."
                    )
                    return []
                feedback_message = (
                    f"Tu as retourné services=[] alors que le dependency_graph contient "
                    f"{len(batch_nodes)} nœud(s) : {node_ids}.\n"
                    "Tu DOIS produire exactement un objet service par nœud du dependency_graph.\n"
                    "Format attendu : {\"services\": [ {...}, {...} ]}"
                )
                continue

            # Anti-hallucination guard: only keep services whose source_service
            # (Terraform resource type) was actually in the input batch_nodes.
            # Uses fuzzy matching to handle LLM normalisation divergences:
            #   - exact match: "iam" == "iam"
            #   - cloud-prefix stripped: "aws_iam_role" → "iam-role" contains "iam"
            #   - substring containment: "db-instance" in "aws_db_instance"
            _CLOUD_PREFIXES = ("aws_", "azurerm_", "google_", "gcp_")
            _GENERIC_SUFFIXES = (
                "_bucket", "_table", "_topic", "_queue", "_policy", "_role",
                "_object", "_handler", "_layer", "_function", "_instance",
                "_cluster", "_server", "_account", "_group",
            )

            def _strip_tf_name(name: str) -> str:
                s = name.lower().strip()
                for p in _CLOUD_PREFIXES:
                    if s.startswith(p):
                        s = s[len(p):]
                        break
                for sfx in _GENERIC_SUFFIXES:
                    if s.endswith(sfx):
                        s = s[: -len(sfx)]
                        break
                return s.replace("_", "-")

            allowed_ids: set[str] = set()
            allowed_ids_stripped: set[str] = set()
            for _n in batch_nodes:
                for _field in ("id", "service", "service_name", "resource_name"):
                    _v = _n.get(_field)
                    if _v:
                        _raw = str(_v).strip()
                        allowed_ids.add(_raw)
                        allowed_ids_stripped.add(_strip_tf_name(_raw))

            def _fuzzy_match(candidate: str) -> bool:
                c = candidate.strip()
                c_low = c.lower()
                # 1. Exact match
                if c in allowed_ids:
                    return True
                # 2. Case-insensitive exact
                if c_low in {a.lower() for a in allowed_ids}:
                    return True
                # 3. Stripped name match
                c_stripped = _strip_tf_name(c)
                if c_stripped in allowed_ids_stripped:
                    return True
                # 4. Substring containment (both directions, stripped)
                for a_stripped in allowed_ids_stripped:
                    if a_stripped and (a_stripped in c_stripped or c_stripped in a_stripped):
                        return True
                # 5. Original substring containment (handles "aws_db_instance" vs "db-instance")
                for a in allowed_ids:
                    a_low = a.lower()
                    if a_low and (a_low in c_low or c_low in a_low):
                        return True
                return False

            filtered_services = []
            for s in raw_services:
                candidates = [
                    str(v).strip()
                    for v in (
                        s.get("source_service"),
                        s.get("service_name"),
                        s.get("resource_name"),
                    )
                    if v
                ]
                if any(_fuzzy_match(c) for c in candidates):
                    filtered_services.append(s)
                else:
                    logger.warning(
                        "Agent01 batch '%s': dropping hallucinated service (candidates=%s "
                        "not in input nodes %s)",
                        category, candidates, sorted(allowed_ids),
                    )

            # Safety: if the filter dropped EVERYTHING and the input had nodes,
            # accept all services to avoid a silent empty plan (empty plan is
            # worse than a hallucinated one — the orchestrator has further guards).
            if not filtered_services and raw_services and batch_nodes:
                logger.warning(
                    "Agent01 batch '%s': hallucination filter dropped all %d service(s) "
                    "— falling back to unfiltered (allowed_ids=%s). "
                    "Check that source_service matches node ids.",
                    category, len(raw_services), sorted(allowed_ids),
                )
                filtered_services = raw_services

            # Dedup: if the LLM generated multiple services for the same source_service,
            # keep the one with the highest equivalence_score (or the first one).
            seen_sources: dict[str, dict] = {}
            for s in filtered_services:
                src_key = (
                    s.get("source_service")
                    or s.get("service_name")
                    or s.get("resource_name", "")
                )
                if src_key not in seen_sources:
                    seen_sources[src_key] = s
                else:
                    existing_score = seen_sources[src_key].get("equivalence_score", 0) or 0
                    new_score = s.get("equivalence_score", 0) or 0
                    if new_score > existing_score:
                        seen_sources[src_key] = s
                        logger.warning(
                            "Agent01 batch '%s': duplicate source_service '%s' — "
                            "keeping higher-scored entry (%.2f > %.2f)",
                            category, src_key, new_score, existing_score,
                        )
            deduped = list(seen_sources.values())
            if len(deduped) < len(filtered_services):
                logger.warning(
                    "Agent01 batch '%s': deduplicated %d → %d services",
                    category, len(filtered_services), len(deduped),
                )

            logger.info(
                f"Agent01 batch '{category}': valid plan after {iteration + 1} iteration(s) "
                f"({len(deduped)}/{len(raw_services)} services after hallucination filter + dedup)"
            )
            return [_normalize_service(s, source_cloud, target_cloud) for s in deduped]

        except Exception as exc:
            exc_str = str(exc)

            if ("429" in exc_str or "rate limit" in exc_str.lower()
                    or "ratelimit" in exc_str.lower() or "quota" in exc_str.lower()):
                rate_retries += 1
                if rate_retries >= MAX_RATE_RETRIES:
                    logger.error(
                        f"Agent01 batch '{category}': rate-limit max ({MAX_RATE_RETRIES}) — batch discarded."
                    )
                    return []
                backoff = min(15 * (2 ** (rate_retries - 1)), 120)
                logger.warning(
                    f"Agent01 batch '{category}': rate-limit "
                    f"(retry {rate_retries}/{MAX_RATE_RETRIES}) — backoff {backoff}s"
                )
                time.sleep(backoff)
                continue

            iteration += 1
            if iteration >= MAX_ITERATIONS:
                logger.error(
                    f"Agent01 batch '{category}': MAX_ITERATIONS after exception: {exc} — batch discarded."
                )
                return []
            feedback_message = f"Erreur : {exc_str[:150]}\n\nRetente avec une réponse JSON valide."

    logger.error(f"Agent01 batch '{category}': loop ended without valid plan — batch discarded.")
    return []
