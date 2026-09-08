"""
execution_graph.py — Shim de compatibilité ascendante.

Renommé vers iac_guidance.py. Ce shim maintient les imports existants.
SCAFFOLD_FILES et regenerate_scaffolding_from_meta ont été supprimés (code mort).
"""
from agents.iac_generator.iac_guidance import build_iac_guidance  # noqa: F401
from core.constants import PROVIDER_VERSION_FLOORS  # noqa: F401
