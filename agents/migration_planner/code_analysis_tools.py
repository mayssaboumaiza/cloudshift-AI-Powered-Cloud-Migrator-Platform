"""
code_analysis_tools.py — STUB: backward-compatibility re-export only.

This file has been relocated to services/stack_analyzer/code_analysis_tools.py
(its canonical home — the stack_analyzer service owns static code analysis).

All symbols are re-exported from the new location so that any remaining
direct imports of this path continue to work without modification.

New code should import from services.stack_analyzer.code_analysis_tools directly.
"""

from services.stack_analyzer.code_analysis_tools import (  # noqa: F401
    ast_parse_file,
    compute_dependency_score,
    detect_cloud_provider,
    detect_ai_framework,
)

__all__ = [
    "ast_parse_file",
    "compute_dependency_score",
    "detect_cloud_provider",
    "detect_ai_framework",
]
