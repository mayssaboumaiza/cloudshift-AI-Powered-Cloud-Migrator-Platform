"""
react_tools.py — Agent 02 ReAct loop inspection/validation tools.

Three LangChain tools that close the feedback loop in Agent 02's hand-rolled ReAct:

  validate_terraform_block   — HCL syntax check before writing to disk (hcl2 + semantic).
  get_rag_context_for_resource — Pull Terraform docs from the pgvector Graph RAG index.
  read_generated_files        — Read back .tf files already on disk to avoid duplication.

These three tools (plus write_terraform_file from hcl_merge.py) are the complete
_agent_02_tools list assembled in generator.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger("Tools")

# ── Path helper (self-contained copy — avoids importing from hcl_merge) ──────

_AGENT_02_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR   = os.path.dirname(_AGENT_02_DIR)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_AGENTS_DIR))


def _get_output_dir() -> str:
    """Return output directory, re-reading MIGRATION_OUTPUT_DIR on every call."""
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    return _out_env if os.path.isabs(_out_env) else os.path.join(
        _PROJECT_ROOT, _out_env or os.path.join("output", "migrated_app")
    )


def _normalize_provider_name(provider: str) -> str:
    """Normalize provider aliases to Terraform registry identifiers."""
    p = (provider or "").strip().lower()
    return {"azure": "azurerm", "gcp": "google"}.get(p, p)


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def validate_terraform_block(hcl_content: str) -> str:
    """Validate HCL syntax of a Terraform block before writing to disk.

    Uses python-hcl2 for a lightweight in-process parse.  Call this BEFORE
    write_terraform_file to catch syntax errors early (missing braces, invalid
    argument names, duplicate blocks).

    Also performs a semantic check: azurerm_linux_virtual_machine must not have
    an empty public_key — the Azure provider rejects that at terraform plan even
    though HCL syntax is valid.

    Args:
        hcl_content: Raw HCL string to validate (one or more resource blocks).

    Returns:
        JSON: {"valid": bool, "errors": [str], "resource_count": int}
    """
    try:
        import hcl2  # type: ignore[import]
        import io
        hcl2.load(io.StringIO(hcl_content))
        resource_count = len(re.findall(r'^\s*resource\s+"', hcl_content, re.MULTILINE))

        # Semantic: azurerm_linux_virtual_machine must not have an empty public_key.
        if re.search(r'resource\s+"azurerm_linux_virtual_machine"', hcl_content):
            if re.search(r'public_key\s*=\s*""', hcl_content):
                return json.dumps({
                    "valid": False,
                    "errors": [
                        'admin_ssh_key.public_key must not be an empty string. '
                        'Set ssh_public_key variable default to a valid placeholder RSA key, '
                        'e.g. default = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC0placeholder'
                        '+key/for/terraform/plan/only+CHANGE_BEFORE_DEPLOY placeholder@cloud-migrator"'
                    ],
                    "resource_count": resource_count,
                })

        return json.dumps({"valid": True, "errors": [], "resource_count": resource_count})

    except ImportError:
        # Fallback: brace balance check only.
        open_braces  = hcl_content.count("{")
        close_braces = hcl_content.count("}")
        if open_braces != close_braces:
            return json.dumps({
                "valid": False,
                "errors": [f"Unbalanced braces: {open_braces} open vs {close_braces} close"],
                "resource_count": 0,
            })
        return json.dumps({
            "valid": True,
            "errors": ["hcl2 not installed — brace check only"],
            "resource_count": 0,
        })
    except Exception as e:
        return json.dumps({"valid": False, "errors": [str(e)], "resource_count": 0})


@tool
def get_rag_context_for_resource(provider: str, resource_type: str) -> str:
    """Fetch Terraform documentation for a specific resource type from the RAG index.

    Use this before generating HCL to get required args, optional args, blocks,
    and a complete HCL example.  Queries the pgvector Graph RAG corpus and enriches
    with argument-level nodes (multi-hop traversal).

    Args:
        provider:      'aws' | 'azurerm' | 'google'
        resource_type: Terraform resource type, e.g. 'google_storage_bucket'

    Returns:
        JSON: {resource_type, provider, context, argument_nodes, found}
              or {"error": "..."} if RAG is unavailable.
    """
    try:
        from rag.graph_rag import TerraformGraphRAG
        provider_norm = _normalize_provider_name(provider)
        rag = TerraformGraphRAG.get_instance()
        context = rag.get_context(resource_type, provider_norm)
        arg_nodes = rag.get_argument_context(
            resource_type,
            query_text=f"{resource_type} {provider_norm} required arguments",
        )
        if context:
            return json.dumps({
                "resource_type":   resource_type,
                "provider":        provider_norm,
                "context":         context,
                "argument_nodes":  arg_nodes,
                "found":           True,
            })
        return json.dumps({
            "resource_type":  resource_type,
            "provider":       provider_norm,
            "found":          False,
            "context":        "",
            "argument_nodes": [],
        })
    except ImportError:
        return json.dumps({"error": "RAG infrastructure not available", "found": False})
    except Exception as e:
        logger.warning(f"get_rag_context_for_resource({resource_type}): {e}")
        return json.dumps({"error": str(e), "found": False})


@tool
def read_generated_files() -> str:
    """Read back all .tf files already written to the output directory.

    Use this to inspect what you have already generated before writing more
    files — avoids duplicating provider/terraform blocks across files.

    Returns:
        JSON: {files: [{name, size_chars, content}], total_files, has_provider_tf}
    """
    try:
        output_dir = Path(_get_output_dir())
        tf_files = sorted(output_dir.glob("*.tf"))
        result = []
        for f in tf_files:
            content = f.read_text(encoding="utf-8")
            result.append({
                "name":       f.name,
                "size_chars": len(content),
                "content":    content,
            })
        return json.dumps({
            "files":          result,
            "total_files":    len(result),
            "has_provider_tf": any(f["name"] == "provider.tf" for f in result),
        })
    except Exception as e:
        return json.dumps({"error": str(e), "files": [], "total_files": 0, "has_provider_tf": False})
