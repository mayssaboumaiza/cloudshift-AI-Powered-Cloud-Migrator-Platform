"""
service/stack_analyzer — Deterministic GitHub repo analyzer (zero LLM).

Public API re-exported here so callers use a stable path regardless of
which internal module a symbol lives in.
"""

from services.stack_analyzer.core import run_stack_analyzer, run_iac_parser
from services.stack_analyzer.service_classifier import _get_service_type, _get_complexity
from services.stack_analyzer.constants import _TYPE_PRIORITY
from services.stack_analyzer.multirepo_merger import merge_graphs

__all__ = [
    "run_stack_analyzer",
    "run_iac_parser",
    "merge_graphs",
    "_get_service_type",
    "_get_complexity",
    "_TYPE_PRIORITY",
]
