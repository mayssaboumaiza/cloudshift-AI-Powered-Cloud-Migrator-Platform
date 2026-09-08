"""
sa_service_classifier.py — Service name normalisation and type classification.

Functions:
  _normalize_service_name  — Strip cloud prefix + resource suffix from TF type.
  _classify_by_keywords    — Keyword rule engine for types not in _SERVICE_TYPE_MAP.
  _get_service_type        — Multi-strategy lookup (map → sliding window → keywords).
  _get_complexity          — Map a score int to LOW/MEDIUM/HIGH string.
"""
from __future__ import annotations
from services.stack_analyzer.constants import _SERVICE_TYPE_MAP, _CATEGORY_KEYWORD_RULES

def _normalize_service_name(full_resource_type: str) -> str:
    """Return a short UI-friendly name for display and graph node IDs.

    The short name strips the cloud prefix and trailing resource noun so that
    e.g. "aws_s3_bucket" → "s3" and "azurerm_storage_account" → "storage".

    ⚠ This name is intentionally lossy — it is NOT used for RAG lookups.
    Pass full_resource_type directly to lookup_terraform_mapping() so that
    pgvector receives the most specific query possible.

    Examples:
        aws_s3_bucket             → s3
        aws_rds_cluster           → rds-cluster   (suffix "_cluster" kept — more specific than "rds")
        azurerm_storage_account   → storage
        google_compute_instance   → compute
    """
    # Remove cloud prefix
    svc = full_resource_type
    if svc.startswith("aws_"):
        svc = svc[4:]
    elif svc.startswith("azurerm_"):
        svc = svc[8:]
    elif svc.startswith("google_"):
        svc = svc[7:]

    # Only strip generic, non-discriminating suffixes.
    # Suffixes that carry meaningful semantic information (e.g. "_cluster",
    # "_flexible_server") are preserved so that nodes remain distinguishable.
    generic_suffixes = [
        "_bucket", "_table", "_topic", "_queue",
        "_policy", "_role", "_object", "_handler", "_layer",
    ]
    for suffix in generic_suffixes:
        if svc.endswith(suffix):
            svc = svc[:-len(suffix)]
            break

    # Normalize underscores to hyphens
    svc = svc.replace("_", "-")
    return svc


def _normalize_service_name_for_rag(full_resource_type: str) -> str:
    """Return the full resource type unchanged, for use as a RAG query term.

    Unlike _normalize_service_name(), this function preserves the complete
    Terraform resource type (e.g. "aws_rds_cluster") so that the pgvector
    similarity search receives the most precise query possible.
    """
    return full_resource_type.strip()

def _classify_by_keywords(normalized: str) -> str:
    """Classify a normalised resource name using the keyword rule engine.

    Two-pass strategy:
    1. Exact segment match: split name on '-' and check set intersection —
       fast and precise for e.g. "db-instance" → {"db","instance"} ∩ database-kws.
    2. Substring match: catches compound tokens like "postgresql-flexible-server"
       where the keyword "postgres" is a prefix of a longer segment.
    """
    segments = set(normalized.replace("_", "-").split("-"))
    # Pass 1 — whole-segment intersection
    for category, keywords in _CATEGORY_KEYWORD_RULES:
        if segments & keywords:
            return category
    # Pass 2 — substring scan
    for category, keywords in _CATEGORY_KEYWORD_RULES:
        for kw in keywords:
            if kw in normalized:
                return category
    return "unknown"


def _get_service_type(service: str) -> str:
    """Map a service name to its type category.

    P23: Handles all naming conventions:
    - Canonical short names: "s3" → storage
    - Full Terraform names: "aws_s3_bucket" → storage (strips cloud prefix first)
    - Dash-separated: "aws-s3-bucket" → storage
    - Pulumi: "s3/bucket" → storage (uses first segment)
    - Full module paths: "google.cloud.firestore" → database

    Sliding window longest-match across segments guarantees the most specific
    match is preferred over a broader one.
    """
    if not service:
        return "unknown"
    svc = service.lower().strip()

    # P23: strip cloud provider prefixes before lookup
    for prefix in ("aws_", "azurerm_", "google_", "aws-", "azurerm-", "google-",
                   "pulumi_aws.", "pulumi_gcp.", "pulumi_azure."):
        if svc.startswith(prefix):
            svc = svc[len(prefix):]
            break

    # Handle Pulumi path notation "s3/bucket" → "s3"
    if "/" in svc:
        svc = svc.split("/")[0]

    # Handle dotted module paths "google.cloud.firestore" → "firestore"
    if "." in svc:
        svc = svc.split(".")[-1]

    if svc in _SERVICE_TYPE_MAP:
        return _SERVICE_TYPE_MAP[svc]

    # Sliding window over dash/underscore segments (longest match first)
    parts = svc.replace("_", "-").split("-")
    for length in range(len(parts), 0, -1):
        for i in range(len(parts) - length + 1):
            candidate = "-".join(parts[i : i + length])
            if candidate in _SERVICE_TYPE_MAP:
                return _SERVICE_TYPE_MAP[candidate]

    # Dynamic fallback: keyword rule engine handles any resource type
    # not enumerated in the static map, without requiring dict updates.
    return _classify_by_keywords(svc)


def _get_complexity(score: int) -> str:
    if score >= 6:
        return "HIGH"
    if score >= 3:
        return "MEDIUM"
    return "LOW"

