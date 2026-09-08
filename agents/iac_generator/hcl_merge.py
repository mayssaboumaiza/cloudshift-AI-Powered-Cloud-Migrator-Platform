"""
hcl_merge.py — Brace/string-aware HCL merge engine + write_terraform_file tool.

Responsibilities:
  - Parse top-level `resource` blocks from HCL text (_extract_blocks).
  - Merge two HCL files, deduplicating by (resource_type, resource_name) (_merge_hcl).
  - Expose write_terraform_file as the single LangChain tool that Agent 02 uses
    to persist generated Terraform to disk.

Why a separate module?
  write_terraform_file is imported by generator.py AND python_migrator.py.
  Keeping the HCL-specific logic here avoids loading MCP/Jinja2/cloud deps
  just to write a .tf file.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger("Tools")

# ── Path helpers ──────────────────────────────────────────────────────────────

_AGENT_02_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR   = os.path.dirname(_AGENT_02_DIR)
_PROJECT_ROOT = os.path.dirname(_AGENTS_DIR)


def _get_output_dir() -> str:
    """Return output directory, re-reading MIGRATION_OUTPUT_DIR on every call.

    Must be a function — the env var may be set after module import
    (e.g. by uvicorn after the module is loaded at startup).
    """
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    return _out_env if os.path.isabs(_out_env) else os.path.join(
        _PROJECT_ROOT, _out_env or os.path.join("output", "migrated_app")
    )


# Backward-compat alias — callers that do `from hcl_merge import OUTPUT_DIR`
# get the value at import time; callers that need the live value must use
# _get_output_dir() instead.
OUTPUT_DIR = _get_output_dir()

# ── HCL merge internals ───────────────────────────────────────────────────────

_HCL_RES_HEADER = re.compile(
    r'(?m)^[ \t]*resource[ \t]+"([\w-]+)"[ \t]+"([\w-]+)"[ \t]*\{'
)

# Files that must be overwritten whole (singleton semantics).
# Resource files (compute.tf, network.tf, …) are merged so the LLM can write
# one resource per ReAct step without losing prior work.
_SINGLETON_TF_FILES = {"outputs.tf", "terraform.tf", "provider.tf", "variables.tf"}


def _extract_blocks(hcl: str) -> tuple[list[str], dict[tuple[str, str], str]]:
    """Brace/string/heredoc-aware splitter for top-level `resource` blocks.

    Returns (preamble_segments, {(type, name): block_text}).
    preamble_segments are non-resource fragments (comments, locals, data, output…)
    preserved in order.  Resources are keyed by (type, name) — a later block with
    the same key replaces an earlier one (mirrors Terraform's duplicate-detection).
    """
    preamble: list[str] = []
    blocks: dict[tuple[str, str], str] = {}
    pos = 0
    n = len(hcl)

    while pos < n:
        m = _HCL_RES_HEADER.search(hcl, pos)
        if not m:
            tail = hcl[pos:].strip()
            if tail:
                preamble.append(tail)
            break

        if m.start() > pos:
            head = hcl[pos:m.start()].strip()
            if head:
                preamble.append(head)

        rtype, rname = m.group(1), m.group(2)
        depth = 1
        i = m.end()
        in_str = False
        in_heredoc = False
        heredoc_marker = ""

        while i < n and depth > 0:
            c = hcl[i]
            if in_heredoc:
                if hcl.startswith(heredoc_marker, i) and (i == 0 or hcl[i - 1] == "\n"):
                    in_heredoc = False
                    i += len(heredoc_marker)
                    continue
            elif in_str:
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                elif c == "<" and hcl[i:i + 2] == "<<":
                    end_line = hcl.find("\n", i)
                    if end_line != -1:
                        marker_raw = hcl[i + 2:end_line].strip()
                        marker = marker_raw.lstrip("-").strip()
                        if marker.isidentifier():
                            heredoc_marker = "\n" + marker + "\n"
                            in_heredoc = True
                            i = end_line
                            continue
            i += 1

        block_text = hcl[m.start():i].rstrip()
        blocks[(rtype, rname)] = block_text
        pos = i

    return preamble, blocks


def _merge_hcl(existing: str, incoming: str) -> str:
    """Merge `incoming` into `existing`, deduplicating resource blocks by (type, name).

    `incoming` blocks win on conflict — mirrors Terraform's "last write wins" convention.
    Non-resource preamble segments (locals, data, comments) are preserved in order
    with deduplication by content.
    """
    pre1, b1 = _extract_blocks(existing)
    pre2, b2 = _extract_blocks(incoming)
    merged_blocks: dict[tuple[str, str], str] = {**b1, **b2}

    parts: list[str] = []
    seen_pre: set[str] = set()
    for p in [*pre1, *pre2]:
        if p and p not in seen_pre:
            parts.append(p)
            seen_pre.add(p)

    parts.extend(merged_blocks.values())
    return "\n\n".join(parts).rstrip() + "\n"


# ── LangChain tool ────────────────────────────────────────────────────────────

@tool
def write_terraform_file(filename: str, content: str) -> str:
    """Write or merge generated Terraform content into output/migrated_app/<filename>.

    Behaviour:
      • provider.tf / variables.tf / outputs.tf / terraform.tf → overwrite (singleton).
      • Any other .tf file → MERGE with existing content on disk.  The merger is
        brace/string/heredoc-aware and deduplicates resource blocks by (type, name).
        This means you can call this tool once per resource (e.g. one call for
        azurerm_virtual_network, another for azurerm_subnet, both targeting
        network.tf) and both resources end up in the file.
      • .py / .sh files → overwrite.

    Args:
        filename: Target filename, e.g. 'storage.tf'.  Must end with .tf, .py, or .sh.
        content:  File content.  For resource .tf files, may be a single block.

    Returns:
        JSON with: path, bytes_written, mode ("wrote" or "merged"), error.
    """
    if not (filename.endswith(".tf") or filename.endswith(".py") or filename.endswith(".sh")):
        return json.dumps({"path": None, "error": f"Unsupported file extension: {filename}"})

    # Shell scripts must abort on error — inject set -euo pipefail if absent.
    if filename.endswith(".sh") and "set -e" not in content:
        lines = content.split("\n")
        insert_pos = 1 if lines and lines[0].startswith("#!") else 0
        lines.insert(insert_pos, "set -euo pipefail")
        content = "\n".join(lines)

    # Reject malformed/truncated HCL fragments before they ever reach the merge
    # engine — _extract_blocks treats anything without a `resource "type" "name" {`
    # header as preamble and splices it in raw, so a truncated LLM fragment (missing
    # its own header, an unterminated string, an unbalanced brace) silently corrupts
    # the whole file. Catching it here means corruption can never reach disk, even
    # if the LLM skips the separate validate_terraform_block tool call.
    if filename.endswith(".tf"):
        open_braces  = content.count("{")
        close_braces = content.count("}")
        if open_braces != close_braces:
            return json.dumps({
                "path": None,
                "error": (
                    f"Refused to write {filename}: unbalanced braces "
                    f"({open_braces} open vs {close_braces} close) — content looks "
                    "truncated or malformed. Regenerate this block as a single "
                    "complete `resource \"type\" \"name\" { ... }` and retry."
                ),
            })
        try:
            import hcl2
            import io
            hcl2.load(io.StringIO(content))
        except ImportError:
            pass
        except Exception as e:
            return json.dumps({
                "path": None,
                "error": (
                    f"Refused to write {filename}: content failed HCL parsing ({e}). "
                    "The block is likely truncated or has a syntax error (unterminated "
                    "string, missing resource header, stray brace). Regenerate it as a "
                    "single complete, valid resource block and retry."
                ),
            })

        # A valid-looking attribute map can still be a *headless* fragment — e.g. the
        # LLM emits a resource's body (`account_tier = "Standard" ...`) without its
        # `resource "azurerm_storage_account" "main" {` wrapper, often from truncation
        # mid-generation. hcl2 happily parses this as a bare attribute list, and
        # _extract_blocks (below) would then file it as "preamble" and splice it in
        # raw — corrupting the file with orphan attributes that break `terraform init`
        # at parse time. Resource files must contain ONLY `resource` blocks, so any
        # preamble at all is a sign of a headless/truncated fragment — refuse it.
        if Path(filename).name not in _SINGLETON_TF_FILES:
            preamble, _ = _extract_blocks(content)
            if preamble:
                return json.dumps({
                    "path": None,
                    "error": (
                        f"Refused to write {filename}: content contains text outside "
                        f"any `resource \"type\" \"name\" {{ ... }}` block "
                        f"(e.g. starts with: {preamble[0][:80]!r}). This looks like a "
                        "truncated fragment missing its resource header. Regenerate it "
                        "as one or more complete `resource \"type\" \"name\" { ... }` "
                        "blocks and retry — do not send bare attribute lists."
                    ),
                })

    safe_name   = Path(filename).name
    output_path = Path(_get_output_dir()).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    full_path = (output_path / safe_name).resolve()

    if not str(full_path).startswith(str(output_path)):
        return json.dumps({"path": None, "error": "Invalid filename: path traversal detected"})

    MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        return json.dumps({
            "path": None,
            "error": (
                f"File too large: {len(encoded):,} bytes > {MAX_FILE_BYTES:,} bytes limit. "
                "Split the content into multiple smaller files."
            ),
        })

    try:
        is_tf        = safe_name.endswith(".tf")
        is_singleton = safe_name in _SINGLETON_TF_FILES
        should_merge = is_tf and not is_singleton and full_path.exists()

        if should_merge:
            existing   = full_path.read_text(encoding="utf-8")
            merged     = _merge_hcl(existing, content)
            merged_bytes = merged.encode("utf-8")
            full_path.write_bytes(merged_bytes)
            return json.dumps({
                "path": str(full_path),
                "bytes_written": len(merged_bytes),
                "mode": "merged",
                "error": None,
            })

        full_path.write_bytes(encoded)
        return json.dumps({
            "path": str(full_path),
            "bytes_written": len(encoded),
            "mode": "wrote",
            "error": None,
        })
    except OSError as e:
        return json.dumps({"path": None, "error": str(e)})
