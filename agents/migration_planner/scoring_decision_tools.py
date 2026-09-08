"""
scoring_decision_tools.py — Agent 01 multi-criteria scoring and 7R strategy decision.

Design principles
-----------------
* SAW (Simple Additive Weighting) with AHP-derived weights.
  Weights are computed once at import time from core.ahp_weights — a
  mathematically grounded alternative to manually assigned values.
  The weight source is exposed in every scoring response for full traceability.

* All scoring inputs are objectively sourced:
    equivalence  — pgvector cosine similarity between Terraform doc embeddings
    maturity     — GitHub multi-window commit analysis (check_service_maturity)
    budget_fit   — continuous gradient from get_pricing() vs declared budget
    region_fit   — service availability in target region
    complexity   — migration effort estimate (inverted: 1 - complexity)

* Two-phase design prevents non-viable candidates from scoring positively:
    Phase 1 — hard gates eliminate candidates before any arithmetic.
    Phase 2 — SAW formula ranks the survivors.

* 7R strategy uses confidence intervals rather than hard thresholds.
  Thresholds are the module-level _REHOST_*/_REPLATFORM_* constants (the single
  source of truth, env-overridable); they sit +0.04 above raw SBERT calibration
  to counter the semantic-tag boost on known pairs:
    eq >= 0.97          → REHOST   (high confidence)
    0.94 <= eq < 0.97   → REHOST   (medium confidence, trigger ask_human)
    0.86 <= eq < 0.94   → REPLATFORM (high confidence)
    0.82 <= eq < 0.86   → REPLATFORM (medium confidence, trigger ask_human)
    eq < 0.82           → REFACTOR (high confidence)
  decide_7r_strategy_v2 is preferred when sdk_calls_count is known: it decides on
  a 3-signal combined score (0.40*similarity + 0.35*SDK + 0.25*breaking).

Public LangChain tools (decorated with @tool):
    score_service_candidates  — Phase 1 + Phase 2 combined
    decide_7r_strategy        — 7R taxonomy decision tree with confidence zones
    lookup_terraform_mapping  — pgvector RAG lookup
"""

from __future__ import annotations

import json
import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger("Tools")

# ── AHP-derived weights ───────────────────────────────────────────────────────
# Imported from core/ahp_weights.py (Saaty AHP, CR = 0.0175 < 0.10 — consistent)
# Environment variables WEIGHT_* can override individual weights for A/B testing.
# If all overrides are 0.0 (default) the AHP values are used as-is.
try:
    from core.ahp_weights import AHP_WEIGHTS, AHP_CONSISTENCY_RATIO, AHP_SOURCES
    _ahp_available = True
except ImportError:
    logger.warning("core.ahp_weights unavailable — falling back to hardcoded SAW weights")
    AHP_WEIGHTS = {
        "equivalence": 0.4253, "maturity": 0.2618,
        "budget_fit":  0.1631, "region_fit": 0.1032, "complexity": 0.0466,
    }
    AHP_CONSISTENCY_RATIO = 0.0175
    AHP_SOURCES = {}
    _ahp_available = False


def _load_weights() -> dict[str, float]:
    """Return active SAW weights — env var overrides take precedence over AHP."""
    overrides = {
        "equivalence": float(os.getenv("WEIGHT_EQUIVALENCE", "0.0")),
        "maturity":    float(os.getenv("WEIGHT_MATURITY",    "0.0")),
        "budget_fit":  float(os.getenv("WEIGHT_BUDGET",      "0.0")),
        "region_fit":  float(os.getenv("WEIGHT_REGION",      "0.0")),
        "complexity":  float(os.getenv("WEIGHT_COMPLEXITY",  "0.0")),
    }
    if any(v > 0 for v in overrides.values()):
        total = sum(overrides.values())
        if abs(total - 1.0) > 1e-3:
            logger.warning("WEIGHT_* env vars do not sum to 1.0 (sum=%.4f) — using AHP", total)
            return AHP_WEIGHTS
        return overrides
    return AHP_WEIGHTS


# ── Hard-gate thresholds ──────────────────────────────────────────────────────
_MIN_EQUIVALENCE_GATE  = 0.50    # below this → no functional match, reject
_BUDGET_OVER_TOLERANCE = 1.50    # reject if monthly_cost > budget × 1.50

# ── 7R confidence zone thresholds ────────────────────────────────────────────
# Loaded from env for A/B calibration (see core/constants.py).
#
# SEMANTIC TAG BOOST — Why thresholds are higher than raw SBERT calibration:
#
#   corpus_builder.py injects cross-cloud equivalence keywords into embed_text
#   for ~60 known service pairs (e.g. aws_s3_bucket text includes
#   "equivalent azurerm_storage_account"). This intentionally boosts cosine
#   similarity by ~0.12-0.17 for the pairs we know about.
#
#   Problem: it creates a two-tier similarity distribution:
#     - Known pairs (boosted):   0.88–0.97  (inflated by ~0.15)
#     - Unknown pairs (natural): 0.55–0.78  (raw SBERT cosine)
#
#   Fix applied here: thresholds raised by +0.04 (conservative) so known pairs
#   don't get misclassified as REHOST when they need code changes (REPLATFORM).
#
#   Anti-boost guard (see score_service_candidates below):
#   When similarity >= 0.90 AND the pair's architecture requires SDK changes
#   (storage, serverless, managed-db), the strategy is capped at REPLATFORM
#   regardless of similarity. This prevents the tag boost from promoting
#   genuinely-different services to REHOST.
#
#   Threshold calibration (pre-tag → post-tag value actually used):
#     REHOST certain     (_REHOST_HIGH):            0.95 → 0.97  (VMs, K8s, subnets)
#     REHOST gray low    (_REHOST_LOW):             0.90 → 0.94  (trigger ask_human)
#     REPLATFORM upper   (_REPLATFORM_HIGH):        0.90 → 0.94  (= _REHOST_LOW boundary)
#     REPLATFORM certain (_REPLATFORM_CERTAIN_LOW): 0.86 → 0.86  (storage, functions)
#     REPLATFORM gray    (_REPLATFORM_LOW):         0.82 → 0.82  (trigger ask_human)
#     REFACTOR:                                     < 0.82       (databases, IAM, NoSQL)
#
_REHOST_HIGH            = float(os.getenv("THRESHOLD_REHOST_HIGH",           "0.97"))
_REHOST_LOW             = float(os.getenv("THRESHOLD_REHOST_LOW",            "0.94"))
_REPLATFORM_HIGH        = float(os.getenv("THRESHOLD_REPLATFORM_HIGH",       "0.94"))
_REPLATFORM_CERTAIN_LOW = float(os.getenv("THRESHOLD_REPLATFORM_CERTAIN_LOW","0.86"))
_REPLATFORM_LOW         = float(os.getenv("THRESHOLD_REPLATFORM_LOW",        "0.82"))

# Tie-breaking threshold
_SCORE_TIE_THRESHOLD = float(os.getenv("SCORE_TIE_THRESHOLD", "0.05"))

# ── Anti-boost guard: service categories that CANNOT be REHOST regardless of similarity ──
# Even if the tag boost pushes similarity to 0.97+, these categories require
# SDK/code changes by definition → cap at REPLATFORM.
_REHOST_EXCLUDED_CATEGORIES: frozenset[str] = frozenset({
    "storage",          # S3↔Blob: SDK change (boto3 → azure-storage-blob)
    "serverless",       # Lambda↔Functions: trigger + SDK change
    "managed_database", # RDS↔PostgresFS: connection string + driver change
    "database",         # general DB — always needs connection migration
    "nosql",            # DynamoDB↔CosmosDB: fundamentally different model
    "ai",               # Bedrock↔OpenAI: API incompatible
    "messaging",        # SQS↔ServiceBus: API change
    "search",           # OpenSearch↔CognitiveSearch: query language change
})

# Known service pairs where similarity is artificially boosted by tags.
# For these pairs, the raw pre-tag similarity is stored so decide_7r_strategy
# can use the correct (un-inflated) value for zone classification.
# Format: (source_resource, target_resource) → raw_sim_before_boost
_BOOSTED_PAIRS_RAW_SIM: dict[tuple[str, str], float] = {
    ("aws_s3_bucket",              "azurerm_storage_account"):              0.78,
    ("aws_s3_bucket",              "google_storage_bucket"):                0.80,
    ("aws_lambda_function",        "azurerm_linux_function_app"):           0.84,
    ("aws_lambda_function",        "google_cloudfunctions_function"):       0.85,
    ("aws_db_instance",            "azurerm_postgresql_flexible_server"):   0.72,
    ("aws_rds_cluster",            "azurerm_postgresql_flexible_server"):   0.74,
    ("aws_dynamodb_table",         "azurerm_cosmosdb_account"):             0.65,
    ("aws_sqs_queue",              "azurerm_servicebus_queue"):             0.79,
    ("aws_sns_topic",              "azurerm_servicebus_topic"):             0.76,
    ("aws_eks_cluster",            "azurerm_kubernetes_cluster"):           0.91,
    ("aws_eks_cluster",            "google_container_cluster"):             0.92,
    ("aws_elasticache_cluster",    "azurerm_redis_cache"):                  0.80,
    ("aws_cloudwatch_metric_alarm","azurerm_monitor_metric_alert"):         0.81,
    ("aws_iam_role",               "azurerm_user_assigned_identity"):       0.58,
    ("aws_iam_policy",             "azurerm_role_assignment"):              0.55,
    ("aws_ecr_repository",         "azurerm_container_registry"):          0.88,
    ("aws_cloudfront_distribution","azurerm_cdn_profile"):                  0.75,
    ("aws_secretsmanager_secret",  "azurerm_key_vault_secret"):             0.85,
    ("aws_kms_key",                "azurerm_key_vault_key"):                0.83,
}

# ── Published SLA lookup table (kept for reference / future use) ──────────────
_SLA_TABLE: dict[str, float] = {
    "aws_s3_bucket": 99.99, "aws_dynamodb_table": 99.999,
    "aws_db_instance": 99.95, "aws_rds_cluster": 99.95,
    "aws_lambda_function": 99.95, "aws_eks_cluster": 99.95,
    "azurerm_cosmosdb_account": 99.999, "azurerm_postgresql_flexible_server": 99.99,
    "azurerm_storage_account": 99.90, "azurerm_kubernetes_cluster": 99.95,
    "google_firestore_database": 99.999, "google_storage_bucket": 99.90,
    "google_kubernetes_engine_cluster": 99.95,
}

# ── Provider normalisation ────────────────────────────────────────────────────
_PROVIDER_ALIASES = {
    "aws": "aws", "amazon": "aws",
    "azure": "azure", "azurerm": "azure", "microsoft": "azure",
    "gcp": "gcp", "google": "gcp", "googlecloud": "gcp",
}
_PROVIDER_RESOURCE_PREFIXES = {
    "aws": "aws_", "azure": "azurerm_", "gcp": "google_",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public tools
# ─────────────────────────────────────────────────────────────────────────────

@tool
def score_service_candidates(candidates: str, constraints: str) -> str:
    """Rank cloud migration candidates using hard gates then a weighted SAW formula.

    PHASE 1 — Hard gates (elimination before any scoring):
      - equivalence < 0.50                         → rejected (no functional match)
      - region_available == False / "unavailable"  → rejected
      - maturity_status == "deprecated"            → rejected
      - monthly_cost > budget × 1.50               → rejected (>50% over budget)
      - maturity_status in {preview,beta}
        AND production == True                     → rejected

    PHASE 2 — SAW scoring with AHP-derived weights (Saaty 1980, CR=0.0175):
      score = w_equiv    × equivalence
            + w_maturity × maturity
            + w_budget   × budget_fit       (continuous gradient)
            + w_region   × region_fit
            + w_complex  × (1 - complexity)

    Weights (AHP-derived, see core/ahp_weights.py):
      equivalence ≈ 0.425 | maturity ≈ 0.262 | budget_fit ≈ 0.163
      region_fit  ≈ 0.103 | complexity ≈ 0.047

    budget_fit — continuous linear gradient (no binary jump):
      1.0                    if cost <= budget
      linear decay to 0.0   if budget < cost <= budget * 1.50
      0.0 (hard gate above)  if cost > budget * 1.50

    region_fit:
      1.00 if live / True | 0.60 if neighbor | 0.00 otherwise

    Args:
        candidates:  JSON list, each dict with:
                       service          — Terraform resource type
                       equivalence      — float 0-1
                       maturity         — float 0-1
                       monthly_cost     — float USD/month
                       region_available — True | False | "neighbor"
                       maturity_status  — "ga"|"stable"|"preview"|"beta"|"deprecated"
                       complexity       — float 0-1 (optional, default 0.5)
        constraints: JSON dict with:
                       budget     — monthly USD limit (0 = no constraint)
                       production — bool (default True)

    Returns:
        JSON list sorted by score descending. Each item includes score_breakdown,
        ahp_weights_used, ahp_consistency_ratio, weight_sources, and per-criterion
        reasoning. Rejected candidates have score=0 and reject_reason.
    """
    try:
        candidate_list   = json.loads(candidates)  if isinstance(candidates,  str) else candidates
        constraints_dict = json.loads(constraints) if isinstance(constraints, str) else constraints
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "invalid JSON input"})

    if not isinstance(candidate_list, list):
        return json.dumps({"error": "candidates must be a JSON array"})
    if not isinstance(constraints_dict, dict):
        constraints_dict = {}

    w            = _load_weights()
    budget       = float(constraints_dict.get("budget") or constraints_dict.get("budget_max") or 0)
    is_production = bool(constraints_dict.get("production", True))
    scored: list[dict] = []

    for cand in candidate_list:
        if not isinstance(cand, dict):
            continue

        eq             = max(0.0, min(1.0, float(cand.get("equivalence", 0.0) or 0.0)))
        mat            = max(0.0, min(1.0, float(cand.get("maturity",    0.0) or 0.0)))
        cost           = float(cand.get("monthly_cost", 0.0) or 0.0)
        complexity_raw = max(0.0, min(1.0, float(cand.get("complexity",  0.5) or 0.5)))
        region_raw     = cand.get("region_available", True)
        mat_status     = str(cand.get("maturity_status", "ga")).lower()
        service_name   = str(cand.get("service", "")).strip()

        region_available = region_raw not in (False, "false", "unavailable", 0)

        # ── PHASE 1: Hard gates ───────────────────────────────────────────────
        rejected, reject_reason = False, None

        if eq < _MIN_EQUIVALENCE_GATE:
            rejected = True
            reject_reason = (
                f"equivalence={eq:.2f} < {_MIN_EQUIVALENCE_GATE} — "
                "no functional match; migrating would require full refactor"
            )
        elif not region_available:
            rejected = True
            reject_reason = "service not available in the target region"
        elif mat_status == "deprecated":
            rejected = True
            reject_reason = "service is deprecated on the target cloud"
        elif budget > 0 and cost > budget * _BUDGET_OVER_TOLERANCE:
            rejected = True
            reject_reason = (
                f"estimated cost ${cost:.0f}/mo exceeds "
                f"${budget:.0f}/mo × {_BUDGET_OVER_TOLERANCE} limit"
            )
        elif mat_status in ("preview", "beta") and is_production:
            rejected = True
            reject_reason = (
                f"maturity_status='{mat_status}' — not GA for production workloads"
            )

        if rejected:
            out = cand.copy()
            out["score"] = 0.0
            out["rejected"] = True
            out["reject_reason"] = reject_reason
            out["score_breakdown"] = {}
            scored.append(out)
            continue

        # ── PHASE 2: SAW scoring ──────────────────────────────────────────────

        # budget_fit — continuous gradient (no binary jump)
        if budget <= 0:
            budget_fit = 1.0
            budget_explanation = "no budget constraint set"
        elif cost <= budget:
            budget_fit = 1.0
            budget_explanation = f"${cost:.0f}/mo <= ${budget:.0f}/mo budget"
        elif cost <= budget * _BUDGET_OVER_TOLERANCE:
            budget_fit = 1.0 - ((cost - budget) / (budget * (_BUDGET_OVER_TOLERANCE - 1.0)))
            budget_fit = max(0.0, round(budget_fit, 4))
            budget_explanation = f"${cost:.0f}/mo slightly over ${budget:.0f}/mo — gradient={budget_fit:.2f}"
        else:
            budget_fit = 0.0
            budget_explanation = f"${cost:.0f}/mo exceeds hard limit"

        region_str = str(region_raw).lower()
        if region_raw is True or region_str in ("true", "live", "yes", "available"):
            region_fit = 1.0
            region_explanation = "service confirmed available in target region"
        elif region_str == "neighbor":
            region_fit = 0.60
            region_explanation = "neighbor region (~30% latency penalty, Cloudping)"
        else:
            region_fit = 0.0
            region_explanation = "region unavailable (blocked by hard gate)"

        complexity_inverted = 1.0 - complexity_raw
        complexity_explanation = (
            f"complexity={complexity_raw:.2f} → inverted to {complexity_inverted:.2f} "
            "(lower migration effort = higher score)"
        )

        saw_score = round(min(
            w["equivalence"] * eq
            + w["maturity"]   * mat
            + w["budget_fit"] * budget_fit
            + w["region_fit"] * region_fit
            + w["complexity"] * complexity_inverted,
            1.0,
        ), 4)

        # ── Hybrid scoring: blend SAW + ontology + schema_similarity ──────────
        # formula: final = 0.70 * saw + 0.20 * ontology + 0.10 * schema_sim
        # ontology  — curated equivalence from cloud_service_ontology table
        # schema_sim — ratio of shared required_args (proxy for HCL compatibility)
        ontology_score: float | None = None
        try:
            from rag.cloud_ontology import get_ontology_score as _onto_score
            src_provider = str(constraints_dict.get("source_provider", "aws")).lower()
            src_svc      = str(cand.get("source_service", "")).lower()
            tgt_provider = str(constraints_dict.get("target_provider", "azure")).lower()
            if src_svc and service_name:
                ontology_score = _onto_score(src_provider, src_svc, tgt_provider, service_name)
        except Exception:
            pass

        # schema_similarity: shared_required_args / max(source_args, target_args)
        schema_sim: float = 0.5  # default neutral when unknown
        src_args = set(str(a) for a in (cand.get("source_required_args") or []))
        tgt_args = set(str(a) for a in (cand.get("target_required_args") or []))
        if src_args and tgt_args:
            shared = len(src_args & tgt_args)
            schema_sim = round(shared / max(len(src_args), len(tgt_args)), 4)

        if ontology_score is not None:
            score = round(min(
                0.70 * saw_score + 0.20 * ontology_score + 0.10 * schema_sim, 1.0
            ), 4)
            scoring_mode = "hybrid"
            _debug_extra = {
                "saw_score": saw_score,
                "ontology_score": round(ontology_score, 4),
                "schema_similarity": schema_sim,
                "hybrid_formula": "0.70*SAW + 0.20*ontology + 0.10*schema_sim",
            }
        else:
            score = saw_score
            scoring_mode = "saw_only"
            _debug_extra = {
                "saw_score": saw_score,
                "ontology_score": None,
                "schema_similarity": schema_sim,
                "hybrid_formula": "SAW only (ontology not available for this pair)",
            }

        logger.debug(
            "[Scoring] %s | mode=%s saw=%.4f onto=%s schema=%.4f final=%.4f",
            service_name, scoring_mode, saw_score,
            f"{ontology_score:.4f}" if ontology_score is not None else "N/A",
            schema_sim, score,
        )

        out = cand.copy()
        out["score"]    = score
        out["rejected"] = False
        out["reject_reason"] = None
        out["score_breakdown"] = {
            "equivalence_contrib":   round(w["equivalence"] * eq,                4),
            "maturity_contrib":      round(w["maturity"]    * mat,               4),
            "budget_fit_contrib":    round(w["budget_fit"]  * budget_fit,        4),
            "region_fit_contrib":    round(w["region_fit"]  * region_fit,        4),
            "complexity_contrib":    round(w["complexity"]  * complexity_inverted, 4),
            "normalized_inputs": {
                "equivalence":        round(eq,                  4),
                "maturity":           round(mat,                 4),
                "budget_fit":         round(budget_fit,          4),
                "region_fit":         round(region_fit,          4),
                "complexity_inverted":round(complexity_inverted, 4),
            },
            "weights": w,
            **_debug_extra,
        }
        out["reasoning"] = {
            "equivalence": {
                "value": round(eq, 4),
                "weight": w["equivalence"],
                "contribution": round(w["equivalence"] * eq, 4),
                "explanation": f"cosine similarity={eq:.3f} between Terraform doc embeddings (pgvector)",
            },
            "maturity": {
                "value": round(mat, 4),
                "weight": w["maturity"],
                "contribution": round(w["maturity"] * mat, 4),
                "explanation": f"GitHub multi-window commit analysis: maturity_score={mat:.3f}",
            },
            "budget_fit": {
                "value": round(budget_fit, 4),
                "weight": w["budget_fit"],
                "contribution": round(w["budget_fit"] * budget_fit, 4),
                "explanation": budget_explanation,
            },
            "region_fit": {
                "value": round(region_fit, 4),
                "weight": w["region_fit"],
                "contribution": round(w["region_fit"] * region_fit, 4),
                "explanation": region_explanation,
            },
            "complexity": {
                "value": round(complexity_inverted, 4),
                "weight": w["complexity"],
                "contribution": round(w["complexity"] * complexity_inverted, 4),
                "explanation": complexity_explanation,
            },
        }
        out["ahp_weights_used"]       = w
        out["ahp_consistency_ratio"]  = AHP_CONSISTENCY_RATIO
        out["weight_sources"]         = AHP_SOURCES
        out["scoring_mode"]           = scoring_mode
        scored.append(out)

    # Non-rejected first, then by score descending
    scored.sort(key=lambda x: (x.get("rejected", False), -x.get("score", 0)))

    # Tie-breaking: flag if top-2 non-rejected candidates are within threshold
    non_rejected = [c for c in scored if not c.get("rejected")]
    if len(non_rejected) >= 2:
        gap = non_rejected[0].get("score", 0) - non_rejected[1].get("score", 0)
        if abs(gap) < _SCORE_TIE_THRESHOLD:
            scored[0]["tie_detected"]        = True
            scored[0]["tie_gap"]             = round(gap, 4)
            scored[0]["trigger_ask_human"]   = True
            scored[0]["tie_reason"] = (
                f"Top-2 scores differ by {gap:.4f} < {_SCORE_TIE_THRESHOLD} threshold — "
                "human confirmation recommended."
            )

    return json.dumps(scored)


@tool
def decide_7r_strategy(
    source_service: str,
    target_candidate: str,
    equivalence_score: float,
    breaking_changes: str,
) -> str:
    """Apply the 7R taxonomy decision tree with confidence zones.

    Uses continuous confidence intervals instead of hard thresholds.
    Gray zones trigger trigger_ask_human=True to surface human review.

    NOTE: thresholds below reflect the module-level constants, which are the
    single source of truth (overridable via THRESHOLD_* env vars):
      _REHOST_HIGH=0.97  _REHOST_LOW=0.94  _REPLATFORM_HIGH=0.94
      _REPLATFORM_CERTAIN_LOW=0.86  _REPLATFORM_LOW=0.82
    These are raised ~+0.04 above raw SBERT calibration to counter the semantic
    tag boost on known pairs (see the constants block for the full rationale).

    Decision tree (evaluated top-to-bottom — contextual signals first):
      1. signal "service_unused" / "deprecated"          → RETIRE     (conf 0.95)
      2. signal "regulatory" / "retain"                  → RETAIN     (conf 0.90)
      3. signal "saas_alt" / "repurchase"                → REPURCHASE (conf 0.85)
      4. signal "relocate"                               → RELOCATE   (conf 0.90)
      4b. boosted pair AND SDK-change category AND eq_raw >= _REHOST_LOW
                                                         → REPLATFORM (anti-boost cap)
      5. eq >= 0.97 AND no CRITICAL                      → REHOST     (conf 0.95, certain)
      6. 0.94 <= eq < 0.97 AND no CRITICAL              → REHOST     (conf 0.75, gray zone)
      7. 0.86 <= eq < 0.94 AND no CRITICAL              → REPLATFORM (conf 0.90, certain)
      8. 0.82 <= eq < 0.86 AND no CRITICAL              → REPLATFORM (conf 0.70, gray zone)
      9. eq < 0.82 OR any CRITICAL                       → REFACTOR   (conf 0.80)

    Gray zones (steps 6 and 8) set trigger_ask_human=True in the response
    so the pipeline can route to ask_human for human confirmation.

    Threshold calibration (SBERT all-mpnet-base-v2, post +0.04 anti-boost shift):
      Certain REHOST:     aws_s3_bucket   → google_storage_bucket  (eq >= 0.97)
      Gray REHOST:        eq in [0.94-0.97) — minor config changes may be needed
      Certain REPLATFORM: aws_lambda      → google_cloudfunctions  (eq in [0.86-0.94))
      Gray REPLATFORM:    eq in [0.82-0.86) — architectural change possible
      REFACTOR:           aws_rds         → google_firestore        (eq < 0.82)
      Ref: Reimers & Gurevych (EMNLP 2019) — cosine > 0.90 ≈ equivalent

    Args:
        source_service:    Source Terraform resource type
        target_candidate:  Target Terraform resource type
        equivalence_score: 0.0-1.0 cosine similarity from pgvector
        breaking_changes:  JSON list of {severity, pattern, replacement}
                           OR dict with 'signals' and 'items' keys

    Returns:
        JSON: {strategy, rationale, confidence, certainty, trigger_ask_human,
               uncertainty_reason (if gray zone), thresholds_used}
    """
    try:
        bc_data = json.loads(breaking_changes) if isinstance(breaking_changes, str) else breaking_changes
    except (json.JSONDecodeError, TypeError):
        bc_data = []

    signals: set[str] = set()
    items:   list[dict] = []
    if isinstance(bc_data, dict):
        for s in bc_data.get("signals") or []:
            signals.add(str(s).lower())
        items = bc_data.get("items") or []
    elif isinstance(bc_data, list):
        items = bc_data

    has_critical = any(
        isinstance(b, dict) and str(b.get("severity", "")).upper() == "CRITICAL"
        for b in items
    )
    eq_raw = float(equivalence_score or 0.0)

    # Anti-boost correction: if this pair is a known boosted pair, use the
    # pre-boost similarity for zone classification. The boosted similarity is
    # kept in eq_display for the rationale so the user sees what the RAG returned.
    _src = (source_service or "").lower().strip()
    _tgt = (target_candidate or "").lower().strip()
    eq_corrected = _BOOSTED_PAIRS_RAW_SIM.get((_src, _tgt))
    if eq_corrected is not None:
        eq = eq_corrected
        _boost_note = (
            f" [anti-boost: reported={eq_raw:.3f} corrected={eq:.3f} "
            f"— semantic tag inflation removed]"
        )
        logger.debug("decide_7r_strategy: boost correction applied for (%s, %s)", _src, _tgt)
    else:
        eq = eq_raw
        _boost_note = ""

    thresholds_used = {
        "rehost_certain":     _REHOST_HIGH,
        "rehost_gray_lower":  _REHOST_LOW,
        "replatform_gray_upper": _REPLATFORM_HIGH,
        "replatform_certain_lower": _REPLATFORM_LOW,
    }

    # ── Anti-boost guard: force REPLATFORM for categories that can never be REHOST ─
    # Check source_service category from service name patterns.
    _STORAGE_PATTERNS  = ("s3", "storage", "blob", "gcs", "bucket", "object")
    _SERVERLESS_PATS   = ("lambda", "function", "cloud-run", "cloud_run")
    _DATABASE_PATS     = ("db_instance", "rds", "sql", "postgres", "mysql", "mongo",
                          "cosmos", "dynamodb", "firestore", "bigtable", "alloydb")
    _MESSAGING_PATS    = ("sqs", "sns", "pubsub", "servicebus", "eventhub", "kafka", "msk")
    _IAM_PATS          = ("iam_role", "iam_policy", "iam_user", "iam_group", "role_assignment")

    def _needs_sdk_change(svc: str) -> bool:
        s = svc.lower()
        return (
            any(p in s for p in _STORAGE_PATTERNS)
            or any(p in s for p in _SERVERLESS_PATS)
            or any(p in s for p in _DATABASE_PATS)
            or any(p in s for p in _MESSAGING_PATS)
            or any(p in s for p in _IAM_PATS)
        )

    _sdk_change_required = _needs_sdk_change(_src) or _needs_sdk_change(_tgt)

    # ── Contextual signals (priority over numeric thresholds) ─────────────────
    if "service_unused" in signals or "deprecated" in signals:
        return json.dumps({
            "strategy":         "RETIRE",
            "rationale":        f"{source_service} is unused or deprecated — remove from target plan.",
            "confidence":       0.95,
            "certainty":        "high",
            "trigger_ask_human": False,
            "thresholds_used":  thresholds_used,
        })
    if "regulatory" in signals or "regulatory_constraint" in signals or "retain" in signals:
        return json.dumps({
            "strategy":         "RETAIN",
            "rationale":        f"Regulatory or contractual constraint prevents migrating {source_service} this cycle.",
            "confidence":       0.90,
            "certainty":        "high",
            "trigger_ask_human": False,
            "thresholds_used":  thresholds_used,
        })
    if "saas_alt" in signals or "cheaper_saas" in signals or "repurchase" in signals:
        return json.dumps({
            "strategy":         "REPURCHASE",
            "rationale":        f"A SaaS alternative replaces {source_service} more cost-effectively.",
            "confidence":       0.85,
            "certainty":        "high",
            "trigger_ask_human": False,
            "thresholds_used":  thresholds_used,
        })
    if "relocate" in signals or "same_service_other_region" in signals:
        return json.dumps({
            "strategy":         "RELOCATE",
            "rationale":        f"Same service {source_service}, different region — region lift only.",
            "confidence":       0.90,
            "certainty":        "high",
            "trigger_ask_human": False,
            "thresholds_used":  thresholds_used,
        })

    # ── Numeric equivalence zones ─────────────────────────────────────────────
    #
    # Anti-boost interception (pre-zone):
    # When a known boosted pair is detected AND the service category requires SDK
    # changes, the reported similarity (eq_raw) may have pushed the pair into the
    # REHOST zone, but the corrected similarity (eq) may fall below REPLATFORM_LOW.
    # In that case the zone logic on `eq` alone would return REFACTOR — wrong.
    # The guard intercepts on eq_raw: if the reported value was in a REHOST zone
    # AND the pair needs SDK changes, cap to REPLATFORM regardless of eq.
    if _sdk_change_required and not has_critical and eq_raw >= _REHOST_LOW:
        return json.dumps({
            "strategy":         "REPLATFORM",
            "rationale":        (
                f"equivalence={eq_raw:.3f} (corrected={eq:.3f}) was in the REHOST zone "
                f"({eq_raw:.3f} >= {_REHOST_LOW}) BUT {source_service} → {target_candidate} "
                f"requires SDK/driver changes (anti-boost guard: storage/serverless/database/"
                f"messaging category). Strategy capped at REPLATFORM.{_boost_note}"
            ),
            "confidence":       0.90,
            "certainty":        "high",
            "trigger_ask_human": False,
            "anti_boost_applied": True,
            "thresholds_used":  thresholds_used,
        })

    # Zone 1: REHOST — certain
    if eq >= _REHOST_HIGH and not has_critical:
        return json.dumps({
            "strategy":         "REHOST",
            "rationale":        (
                f"equivalence={eq:.3f} >= {_REHOST_HIGH} between {source_service} "
                f"and {target_candidate}, no CRITICAL breaking changes — "
                f"reconfiguration only, no code changes required.{_boost_note}"
            ),
            "confidence":       0.95,
            "certainty":        "high",
            "trigger_ask_human": False,
            "thresholds_used":  thresholds_used,
        })

    # Zone 2: REHOST — gray zone
    if _REHOST_LOW <= eq < _REHOST_HIGH and not has_critical:
        return json.dumps({
            "strategy":         "REHOST",
            "rationale":        (
                f"equivalence={eq:.3f} in REHOST gray zone [{_REHOST_LOW}-{_REHOST_HIGH}]. "
                f"Likely reconfiguration only, but minor config differences may require SDK changes."
                f"{_boost_note}"
            ),
            "confidence":       0.75,
            "certainty":        "medium",
            "trigger_ask_human": True,
            "uncertainty_reason": (
                f"Similarity {eq:.3f} is in REHOST gray zone [{_REHOST_LOW}-{_REHOST_HIGH}]. "
                "REPLATFORM is also possible — human confirmation recommended."
            ),
            "thresholds_used":  thresholds_used,
        })

    # Zone 3: REPLATFORM — certain
    if _REPLATFORM_HIGH > eq >= _REPLATFORM_CERTAIN_LOW and not has_critical:
        return json.dumps({
            "strategy":         "REPLATFORM",
            "rationale":        (
                f"equivalence={eq:.3f} — SDK and configuration changes needed "
                f"but overall architecture is preserved.{_boost_note}"
            ),
            "confidence":       0.90,
            "certainty":        "high",
            "trigger_ask_human": False,
            "thresholds_used":  thresholds_used,
        })

    # Zone 4: REPLATFORM — gray zone
    if _REPLATFORM_LOW <= eq < _REPLATFORM_CERTAIN_LOW and not has_critical:
        return json.dumps({
            "strategy":         "REPLATFORM",
            "rationale":        (
                f"equivalence={eq:.3f} in REPLATFORM gray zone "
                f"[{_REPLATFORM_LOW}-{_REPLATFORM_CERTAIN_LOW}]. "
                f"SDK changes required; possible architectural changes.{_boost_note}"
            ),
            "confidence":       0.70,
            "certainty":        "medium",
            "trigger_ask_human": True,
            "uncertainty_reason": (
                f"Similarity {eq:.3f} is in REPLATFORM gray zone "
                f"[{_REPLATFORM_LOW}-{_REPLATFORM_CERTAIN_LOW}]. "
                "REFACTOR is also possible — human confirmation recommended."
            ),
            "thresholds_used":  thresholds_used,
        })

    # Zone 5: REFACTOR
    rationale = (
        f"At least one CRITICAL breaking change between {source_service} "
        f"and {target_candidate} — architectural rewrite required.{_boost_note}"
        if has_critical else
        f"equivalence={eq:.3f} < {_REPLATFORM_LOW} — fundamental redesign needed.{_boost_note}"
    )
    return json.dumps({
        "strategy":         "REFACTOR",
        "rationale":        rationale,
        "confidence":       0.80,
        "certainty":        "high",
        "trigger_ask_human": False,
        "thresholds_used":  thresholds_used,
    })


@tool
def lookup_terraform_mapping(
    source_service: str,
    source_cloud: str,
    target_cloud: str,
) -> str:
    """Find Terraform resource types on target_cloud semantically equivalent to source_service.

    Wraps Graph RAG search over indexed Terraform documentation (pgvector).
    Filters results to only those whose resource type starts with the correct
    provider prefix (aws_ / google_ / azurerm_) to prevent cross-provider hallucinations.

    Args:
        source_service: Full Terraform resource type or canonical service name.
                        Pass the full type (e.g. 'aws_rds_cluster') for best RAG precision.
        source_cloud:   'aws' | 'azure' | 'gcp'
        target_cloud:   'aws' | 'azure' | 'gcp'

    Returns:
        JSON list (up to 5): [{terraform_resource, description, similarity,
        required_args, documentation_url}, ...]
    """
    src_norm = _PROVIDER_ALIASES.get((source_cloud or "").lower().strip())
    tgt_norm = _PROVIDER_ALIASES.get((target_cloud or "").lower().strip())
    if not src_norm or not tgt_norm:
        return json.dumps({"error": f"unknown provider source={source_cloud} target={target_cloud}"})

    _RAG_PROVIDER = {"aws": "aws", "azure": "azurerm", "gcp": "google"}
    rag_target    = _RAG_PROVIDER[tgt_norm]
    target_prefix = _PROVIDER_RESOURCE_PREFIXES[tgt_norm]

    # ── Ontology-first: check curated equivalences before RAG ────────────────
    try:
        from rag.cloud_ontology import get_best_target
        onto = get_best_target(src_norm, source_service.lower(), tgt_norm)
        if onto and float(onto.get("equivalence_score", 0)) >= 0.70:
            ts = onto["target_service"]
            slug = ts[len(target_prefix):] if ts.startswith(target_prefix) else ts
            doc_url = (
                f"https://registry.terraform.io/providers/hashicorp/"
                f"{rag_target}/latest/docs/resources/{slug}"
            )
            logger.info(
                "lookup_terraform_mapping: ontology hit '%s'→'%s' (eq=%.2f)",
                source_service, ts, onto["equivalence_score"],
            )
            return json.dumps([{
                "terraform_resource": ts,
                "description":        onto.get("notes", "")[:300],
                "similarity":         round(float(onto["equivalence_score"]), 4),
                "required_args":      [],
                "documentation_url":  doc_url,
                "source":             "ontology",
            }])
    except Exception:
        pass

    query = f"{source_service} ({src_norm}) — functional equivalent on {tgt_norm}"

    try:
        from rag.rag_config import get_pg_connection, EMBEDDING_MODEL
        from core.embedding_model_loader import get_shared_embedder
    except ImportError as exc:
        logger.warning(f"lookup_terraform_mapping: RAG infra unavailable — {exc}")
        return json.dumps({"error": "rag-unavailable", "results": []})

    # ── Dynamic tag enrichment for unknown resources ──────────────────────────
    try:
        from rag.dynamic_tag_enricher import enrich_if_missing
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT description FROM terraform_resources WHERE id = %s LIMIT 1",
                    (source_service,),
                )
                row = cur.fetchone()
                if row:
                    enrich_if_missing(source_service, src_norm, row[0] or "", tgt_norm)
    except Exception:
        pass

    try:
        embedder = get_shared_embedder(EMBEDDING_MODEL)
        emb      = embedder.encode([query]).tolist()[0]
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, description, required_args,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM terraform_resources
                    WHERE provider = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT 15
                    """,
                    (emb, rag_target, emb),
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning(f"lookup_terraform_mapping failed: {exc}")
        return json.dumps({"error": str(exc)[:120], "results": []})

    out: list[dict] = []
    top_similarity = 0.0
    for r in rows:
        resource_type = r[0]
        if not resource_type.startswith(target_prefix):
            continue
        sim = round(float(r[3]), 4)
        if not out:
            top_similarity = sim
        slug    = resource_type[len(target_prefix):]
        doc_url = (
            f"https://registry.terraform.io/providers/hashicorp/"
            f"{rag_target}/latest/docs/resources/{slug}"
        )
        out.append({
            "terraform_resource": resource_type,
            "description":        (r[1] or "")[:300],
            "similarity":         sim,
            "required_args":      r[2] if r[2] is not None else [],
            "documentation_url":  doc_url,
        })
        if len(out) >= 5:
            break

    # ── If top similarity is low, trigger dynamic enrichment and retry once ──
    if top_similarity < 0.55 and out:
        try:
            from rag.dynamic_tag_enricher import enrich_if_missing
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT description FROM terraform_resources WHERE id = %s LIMIT 1",
                        (source_service,),
                    )
                    row = cur.fetchone()
                    if row:
                        new_tag = enrich_if_missing(source_service, src_norm, row[0] or "", tgt_norm)
                        if new_tag:
                            # Re-run the query with updated embeddings
                            emb2 = embedder.encode([query]).tolist()[0]
                            cur.execute(
                                """
                                SELECT id, description, required_args,
                                       1 - (embedding <=> %s::vector) AS similarity
                                FROM terraform_resources
                                WHERE provider = %s
                                ORDER BY embedding <=> %s::vector
                                LIMIT 15
                                """,
                                (emb2, rag_target, emb2),
                            )
                            rows2 = cur.fetchall()
                            out2: list[dict] = []
                            for r in rows2:
                                if not r[0].startswith(target_prefix):
                                    continue
                                slug = r[0][len(target_prefix):]
                                out2.append({
                                    "terraform_resource": r[0],
                                    "description":        (r[1] or "")[:300],
                                    "similarity":         round(float(r[3]), 4),
                                    "required_args":      r[2] if r[2] is not None else [],
                                    "documentation_url":  (
                                        f"https://registry.terraform.io/providers/hashicorp/"
                                        f"{rag_target}/latest/docs/resources/{slug}"
                                    ),
                                })
                                if len(out2) >= 5:
                                    break
                            if out2 and out2[0]["similarity"] > top_similarity:
                                logger.info(
                                    "lookup_terraform_mapping: dynamic enrichment improved "
                                    "'%s' similarity %.2f→%.2f",
                                    source_service, top_similarity, out2[0]["similarity"],
                                )
                                out = out2
        except Exception as exc:
            logger.debug("lookup_terraform_mapping: retry after enrichment failed — %s", exc)

    return json.dumps(out)


@tool
def decide_7r_strategy_v2(
    source_service: str,
    target_candidate: str,
    equivalence_score: float,
    sdk_calls_count: int,
    breaking_changes: str,
    contextual_signals: str = "{}",
) -> str:
    """Apply the 7R taxonomy using THREE signals for higher accuracy than decide_7r_strategy.

    Preferred over decide_7r_strategy when sdk_calls_count is known (from Stack Analyzer).
    Combines: similarity (40%) + SDK rewrite effort (35%) + breaking-change severity (25%).

    Level 1 - contextual signals (absolute priority):
      service_unused/deprecated -> RETIRE | regulatory/retain -> RETAIN | relocate -> RELOCATE
    Level 2 - critical gate: >= 3 CRITICAL breaking changes -> forced REFACTOR
    Level 3 - combined score zones:
      combined >= 0.88 -> REHOST (high conf)   | 0.82-0.88 -> REHOST (gray, ask_human)
      0.70-0.82        -> REPLATFORM (high conf) | 0.62-0.70 -> REPLATFORM (gray, ask_human)
      < 0.62           -> REFACTOR

    Args:
        source_service:      Source Terraform resource type (e.g. 'aws_s3_bucket')
        target_candidate:    Target Terraform resource type (e.g. 'azurerm_storage_account')
        equivalence_score:   Cosine similarity 0-1 from lookup_terraform_mapping
        sdk_calls_count:     Number of SDK call sites needing rewrite (from Stack Analyzer)
        breaking_changes:    JSON list of {severity: CRITICAL|HIGH|MINOR, pattern, replacement}
        contextual_signals:  JSON dict flags: {service_unused, regulatory, relocate} (optional)

    Returns:
        JSON: {strategy, confidence, certainty, combined_score, trigger_ask_human,
               uncertainty_reason, components}
    """
    try:
        bc_list   = json.loads(breaking_changes)   if isinstance(breaking_changes, str)   else breaking_changes
        ctx_dict  = json.loads(contextual_signals) if isinstance(contextual_signals, str) else contextual_signals
    except (json.JSONDecodeError, TypeError):
        bc_list, ctx_dict = [], {}

    # Delegate to the internal implementation
    result = _decide_7r_v2_impl(
        similarity=float(equivalence_score or 0.0),
        sdk_calls_count=int(sdk_calls_count or 0),
        breaking_changes=bc_list if isinstance(bc_list, list) else [],
        contextual_signals=ctx_dict if isinstance(ctx_dict, dict) else {},
    )
    result["source_service"]   = source_service
    result["target_candidate"] = target_candidate
    return json.dumps(result)


def _decide_7r_v2_impl(
    similarity: float,
    sdk_calls_count: int,
    breaking_changes: list,
    contextual_signals: dict | None = None,
) -> dict:
    """Internal 3-signal 7R implementation (not a LangChain tool)."""
    signals = contextual_signals or {}

    # Level 1 - contextual signals
    if signals.get("service_unused"):
        return {"strategy": "RETIRE", "confidence": 0.95, "certainty": "high",
                "trigger_ask_human": False, "uncertainty_reason": None}
    if signals.get("regulatory"):
        return {"strategy": "RETAIN", "confidence": 0.90, "certainty": "high",
                "trigger_ask_human": False, "uncertainty_reason": None}
    if signals.get("relocate"):
        return {"strategy": "RELOCATE", "confidence": 0.90, "certainty": "high",
                "trigger_ask_human": False, "uncertainty_reason": None}

    # Level 2 - critical breaking changes gate
    critical = [b for b in breaking_changes if isinstance(b, dict)
                and str(b.get("severity", "")).upper() == "CRITICAL"]
    if len(critical) >= 3:
        return {
            "strategy":         "REFACTOR",
            "confidence":       0.85,
            "certainty":        "high",
            "trigger_ask_human": False,
            "uncertainty_reason": None,
            "combined_score":   0.0,
            "components": {
                "similarity": similarity,
                "sdk_score":  None,
                "breaks_score": None,
                "sdk_calls_count": sdk_calls_count,
                "critical_breaking_changes": len(critical),
                "minor_breaking_changes": 0,
            },
            "reason": f"{len(critical)} CRITICAL breaking changes force REFACTOR",
        }

    # Level 3 - combined score
    sdk_score   = 1.0 - min(sdk_calls_count / 20.0, 1.0)
    minor_count = len([b for b in breaking_changes if isinstance(b, dict)
                       and str(b.get("severity", "")).upper() == "MINOR"])
    breaks_score = 1.0 - min(minor_count / 10.0, 1.0)

    combined = round(0.40 * similarity + 0.35 * sdk_score + 0.25 * breaks_score, 4)

    trigger_ask_human  = False
    uncertainty_reason = None

    if combined >= 0.88:
        strategy, confidence, certainty = "REHOST",     0.92, "high"
    elif combined >= 0.82:
        strategy, confidence, certainty = "REHOST",     0.75, "medium"
        trigger_ask_human  = True
        uncertainty_reason = f"combined={combined:.3f} in gray zone [0.82-0.88]"
    elif combined >= 0.70:
        strategy, confidence, certainty = "REPLATFORM", 0.88, "high"
    elif combined >= 0.62:
        strategy, confidence, certainty = "REPLATFORM", 0.70, "medium"
        trigger_ask_human  = True
        uncertainty_reason = f"combined={combined:.3f} in gray zone [0.62-0.70]"
    else:
        strategy, confidence, certainty = "REFACTOR",   0.82, "high"

    return {
        "strategy":           strategy,
        "confidence":         confidence,
        "certainty":          certainty,
        "combined_score":     combined,
        "trigger_ask_human":  trigger_ask_human,
        "uncertainty_reason": uncertainty_reason,
        "components": {
            "similarity":               similarity,
            "sdk_score":                round(sdk_score,    4),
            "breaks_score":             round(breaks_score, 4),
            "sdk_calls_count":          sdk_calls_count,
            "minor_breaking_changes":   minor_count,
            "critical_breaking_changes": len(critical),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers — not exposed as LangChain tools
# ─────────────────────────────────────────────────────────────────────────────

def _load_service_meta() -> dict:
    try:
        import yaml
        meta_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "mappings", "service_meta.yaml",
        )
        with open(meta_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("services_metadata", {})
    except Exception as e:
        logger.warning(f"Could not load service_meta.yaml: {e}")
        return {}


_SERVICE_META: dict = {}


def _get_service_meta(canonical_id: str) -> dict:
    global _SERVICE_META
    if not _SERVICE_META:
        _SERVICE_META = _load_service_meta()
    return _SERVICE_META.get(canonical_id, {})
