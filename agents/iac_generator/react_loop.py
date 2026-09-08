"""
react_loop.py — Agent 02 hand-rolled ReAct loop and debug utilities.

Responsibilities:
  - Serialize/dump the LangChain message trace for forensic analysis.
  - Execute individual tool calls returned by the LLM and wrap in ToolMessage.
  - Run the hand-rolled ReAct loop that drives llm.bind_tools().invoke() directly
    (not via create_react_agent), avoiding the thread_id leakage issue that caused
    silent {'messages': []} returns when Agent 02 ran inside the outer LangGraph
    with a Postgres checkpointer.

Why hand-rolled ReAct (not create_react_agent)?
  When Agent 02 runs as a LangGraph node with Postgres checkpointer, the
  inner compiled ReAct agent's configurable.thread_id is set by the parent
  graph and routes the child straight to END in <20ms with no tool calls.
  Direct llm.bind_tools(...).invoke() bypasses this routing entirely.

Exported functions:
  _serialize_messages()  — compact JSON-serialisable message list
  _save_debug_messages() — dump trace to agent_02_messages_debug.json
  _count_tool_calls()    — tally tool invocations by name
  _execute_tool_call()   — run one tool call, return ToolMessage
  _run_iac_react_loop()  — full ReAct loop (max 3 iter, rate-limit backoff)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, SystemMessage
from agents.iac_generator.iac_system_prompts import AGENT_02_SYSTEM
from agents.iac_generator.llm_config import _get_llm_02

logger = logging.getLogger("Agent02")

# ─────────────────────────────────────────────────────────────────────────────
# Forensic debug dump (always saved — diagnoses silent ReAct failures)
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_messages(messages: list) -> list[dict]:
    """Compact serialization of a LangChain message list."""
    out: list[dict] = []
    for m in messages:
        msg_type = getattr(m, "type", None) or m.__class__.__name__.lower().replace("message", "")
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = "\n".join(str(p) for p in content)
        entry = {"type": msg_type, "content": str(content)[:6000]}
        tool_calls = getattr(m, "tool_calls", None) or []
        if tool_calls:
            entry["tool_calls"] = [
                {
                    "name": tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", ""),
                    "args": tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {}),
                }
                for tc in tool_calls
            ]
        tool_call_id = getattr(m, "tool_call_id", None)
        if tool_call_id:
            entry["tool_call_id"] = tool_call_id
        name = getattr(m, "name", None)
        if name:
            entry["name"] = name
        out.append(entry)
    return out


def _save_debug_messages(
    output_dir: Path,
    messages: list,
    metadata: dict,
) -> None:
    """Dump message trace + tool-call summary to JSON for forensic analysis."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        debug_path = output_dir / "agent_02_messages_debug.json"
        payload = {
            "metadata": metadata,
            "messages": _serialize_messages(messages),
        }
        debug_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"_save_debug_messages: wrote {debug_path} ({len(messages)} messages)")
    except Exception as e:
        logger.warning(f"_save_debug_messages: failed to write debug dump: {e}")


def _count_tool_calls(messages: list) -> dict[str, int]:
    """Count tool invocations by name across an agent message trace."""
    counts: dict[str, int] = {}
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Single-pass ReAct loop (true ReAct — feedback injection, no fallbacks)
# ─────────────────────────────────────────────────────────────────────────────

def _execute_tool_call(tool_call: dict, tools_by_name: dict) -> ToolMessage:
    """Run one tool call returned by the LLM and wrap the result in a ToolMessage."""
    name = tool_call.get("name", "")
    args = tool_call.get("args", {}) or {}
    call_id = tool_call.get("id", "")
    tool = tools_by_name.get(name)
    if tool is None:
        return ToolMessage(
            content=f"ERROR: unknown tool '{name}'. Available: {list(tools_by_name.keys())}",
            tool_call_id=call_id,
            name=name,
        )
    try:
        result = tool.invoke(args)
        return ToolMessage(content=str(result), tool_call_id=call_id, name=name)
    except Exception as exc:  # noqa: BLE001 — surface tool failures back to the LLM
        logger.warning(f"Agent02 tool '{name}' raised: {exc}")
        return ToolMessage(
            content=f"ERROR: tool {name} raised: {exc}",
            tool_call_id=call_id,
            name=name,
        )


def _run_iac_react_loop(
    input_msg: str,
    output_dir: Path,
    expected_resource_count: int,
    tools: list,
) -> list:
    """Hand-rolled ReAct loop driving llm.bind_tools(...).invoke() directly.

    We do NOT use langgraph's create_react_agent here. When Agent 02 runs as a
    node inside the outer LangGraph (with a Postgres checkpointer), the inner
    compiled ReAct agent silently returns {'messages': []} in <20ms — the
    parent's `configurable.thread_id` leaks into the child runtime and routes
    it straight to END. This was reproduced and proven via the DIRECT LLM
    bypass diagnostic: llm.bind_tools(tools).invoke(messages) returns the
    expected tool calls, while create_react_agent.invoke(...) does not.

    Returns the full message trace (caller must check disk to decide success).
    """
    MAX_ITERATIONS    = 3
    MAX_LLM_STEPS     = max(30, expected_resource_count * 3 + 15)  # tool-loop budget per iteration
    MAX_RATE_RETRIES  = 5
    NO_PROGRESS_LIMIT = 3  # consecutive LLM responses with 0 tool calls → break

    messages: list = [
        SystemMessage(content=AGENT_02_SYSTEM),
        HumanMessage(content=input_msg),
    ]

    logger.info(
        f"Agent02 ReAct: max_steps_per_iter={MAX_LLM_STEPS} "
        f"for {expected_resource_count} resource(s) + provider/variables, "
        f"input_msg_chars={len(input_msg)}"
    )

    llm = _get_llm_02()

    # Pre-flight connectivity check — fail fast if Azure is unreachable.
    try:
        t_ping = time.monotonic()
        ping = llm.invoke([HumanMessage(content="ping")])
        logger.info(
            f"Agent02: LLM ping OK in {(time.monotonic()-t_ping)*1000:.0f}ms "
            f"({len(str(getattr(ping, 'content', '')))} chars)"
        )
    except Exception as e:
        raise RuntimeError(
            f"Agent02: Azure OpenAI unreachable on pre-flight ping — {e}. "
            f"Verify AZURE_AI_ENDPOINT, AZURE_AI_API_KEY, AZURE_MODEL deployment name."
        ) from e

    llm_with_tools = llm.bind_tools(tools)
    tools_by_name = {getattr(t, "name", ""): t for t in tools}
    logger.info(
        f"Agent02: hand-rolled ReAct ready — tools={list(tools_by_name.keys())}"
    )

    rate_retries = 0

    for iteration in range(1, MAX_ITERATIONS + 1):
        t_start = time.monotonic()
        steps_in_iter = 0
        no_progress_streak = 0
        logger.info(
            f"Agent02 ReAct iter {iteration}: starting — len(messages)={len(messages)}, "
            f"types={[type(m).__name__ for m in messages]}"
        )

        while steps_in_iter < MAX_LLM_STEPS:
            steps_in_iter += 1
            try:
                ai_response: AIMessage = llm_with_tools.invoke(messages)
            except Exception as exc:  # noqa: BLE001
                err_text = str(exc).lower()
                if ("429" in err_text or "rate" in err_text or "quota" in err_text) and rate_retries < MAX_RATE_RETRIES:
                    rate_retries += 1
                    backoff = min(15 * (2 ** (rate_retries - 1)), 120)
                    logger.warning(
                        f"Agent02 ReAct rate-limit (iter {iteration} step {steps_in_iter}, "
                        f"retry {rate_retries}/{MAX_RATE_RETRIES}) — backoff {backoff}s"
                    )
                    time.sleep(backoff)
                    steps_in_iter -= 1  # don't count rate-limit retries against the budget
                    continue
                logger.error(f"Agent02 ReAct iter {iteration} step {steps_in_iter}: LLM error: {exc}")
                raise

            messages.append(ai_response)
            tool_calls = list(getattr(ai_response, "tool_calls", None) or [])

            if not tool_calls:
                # No tool calls = LLM produced a final answer (or stalled with text).
                no_progress_streak += 1
                logger.info(
                    f"Agent02 ReAct iter {iteration} step {steps_in_iter}: "
                    f"no tool calls (text_chars={len(str(getattr(ai_response, 'content', '')))}, "
                    f"streak={no_progress_streak}/{NO_PROGRESS_LIMIT})"
                )
                if no_progress_streak >= NO_PROGRESS_LIMIT:
                    break
                continue

            no_progress_streak = 0
            for tc in tool_calls:
                messages.append(_execute_tool_call(tc, tools_by_name))

        elapsed = time.monotonic() - t_start
        counts = _count_tool_calls(messages)
        logger.info(
            f"Agent02 ReAct iter {iteration}: completed — steps={steps_in_iter}, "
            f"elapsed={elapsed:.1f}s, len(messages)={len(messages)}, "
            f"write_terraform_file={counts.get('write_terraform_file', 0)}, "
            f"get_rag_context_for_resource={counts.get('get_rag_context_for_resource', 0)}, "
            f"validate_terraform_block={counts.get('validate_terraform_block', 0)}"
        )

        disk_tf = sorted(output_dir.glob("*.tf"))
        names = {p.name for p in disk_tf}
        # Success when Agent 02 has written at least one .tf file (any file counts).
        resource_files = disk_tf

        if resource_files:
            logger.info(
                f"Agent02 ReAct: success in {iteration} iteration(s) — "
                f"{len(disk_tf)} .tf file(s) on disk: {sorted(names)}"
            )
            return messages

        if iteration >= MAX_ITERATIONS:
            logger.error(
                f"Agent02 ReAct: exhausted {MAX_ITERATIONS} iterations — "
                f"on disk: {sorted(names)}, resources={len(resource_files)}/{expected_resource_count}"
            )
            return messages

        # Build precise feedback for next iteration
        missing_parts: list[str] = []
        if expected_resource_count > 0 and len(resource_files) < expected_resource_count:
            missing_parts.append(
                f"Only {len(resource_files)}/{expected_resource_count} resource files exist. "
                f"Group remaining resources into <category>.tf files (storage.tf, database.tf, "
                f"compute.tf, iam.tf, monitoring.tf, network.tf, messaging.tf) and call "
                f"write_terraform_file for each."
            )
        if counts.get("write_terraform_file", 0) == 0:
            missing_parts.append(
                "You wrote NO files. Files are only saved when you call "
                "write_terraform_file(filename, content). Generating HCL as plain text "
                "in your reply does NOT persist anything."
            )

        # Anti-hallucination: if files were written but RAG was never called,
        # demand a RAG-first pass in the next iteration.
        wrote_files = counts.get("write_terraform_file", 0) > 0
        called_rag  = counts.get("get_rag_context_for_resource", 0) > 0
        if wrote_files and not called_rag:
            missing_parts.append(
                "ANTI-HALLUCINATION VIOLATION: you wrote Terraform files without calling "
                "get_rag_context_for_resource first. This risks inventing argument names "
                "that don't exist in the provider schema. In the next iteration, call "
                "get_rag_context_for_resource(provider, resource_type) for EVERY resource "
                "type BEFORE writing its block."
            )
            logger.warning(
                "Agent02 ReAct iter %d: wrote %d file(s) with 0 RAG calls — "
                "hallucination risk, injecting anti-hallucination feedback",
                iteration, counts.get("write_terraform_file", 0),
            )

        feedback = (
            f"Iteration {iteration}/{MAX_ITERATIONS} — output incomplete:\n"
            + "\n".join(f"- {p}" for p in missing_parts)
            + "\nReturn to the ReAct loop: get_rag_context_for_resource → "
              "validate_terraform_block → write_terraform_file → read_generated_files."
        )
        logger.warning(f"Agent02 ReAct iter {iteration}: injecting feedback: {feedback[:160]}…")
        messages.append(HumanMessage(content=feedback))

    return messages
