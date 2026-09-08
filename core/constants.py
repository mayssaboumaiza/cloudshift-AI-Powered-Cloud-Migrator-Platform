"""
constants.py — Application-wide constants, all configurable via environment variables.

Design rule: every numeric constant that controls agent behaviour must be
readable from the environment so that it can be tuned without a code change.
The default value in os.getenv() is the production-safe value.
"""

import os

# ── IaC fix loop (Agent 02) ───────────────────────────────────────────────────
MAX_CORRECTION_ATTEMPTS = int(os.getenv("MAX_CORRECTIONS", "5"))  # raised 3→5: complex repos need more fix cycles
MAX_REJECTION_COUNT     = int(os.getenv("MAX_REJECTION_COUNT", "2"))

# ── Intent validation (validate_intent_node) ─────────────────────────────────
MAX_INTENT_REGEN       = int(os.getenv("MAX_INTENT_REGEN", "1"))
INTENT_BUDGET_TOLERANCE = float(os.getenv("INTENT_BUDGET_TOLERANCE", "1.20"))

# ── ReAct loop budgets ────────────────────────────────────────────────────────
MAX_REACT_ITERATIONS = int(os.getenv("MAX_REACT_ITERATIONS", "3"))

# ── 7R cosine similarity thresholds ──────────────────────────────────────────
# Calibration pairs (measured on known service pairs, SBERT all-mpnet-base-v2):
#   ≥ 0.95 → REHOST certain   (aws_s3_bucket     → google_storage_bucket, sim 0.95-0.97)
#   0.90-0.95 → REHOST gray zone
#   0.82-0.90 → REPLATFORM certain (aws_lambda → google_cloudfunctions, sim 0.88-0.92)
#   0.78-0.82 → REPLATFORM gray zone
#   < 0.78 → REFACTOR          (aws_rds → google_firestore, sim 0.65-0.75)
THRESHOLD_REHOST_HIGH     = float(os.getenv("THRESHOLD_REHOST_HIGH",     "0.95"))
THRESHOLD_REHOST_LOW      = float(os.getenv("THRESHOLD_REHOST_LOW",      "0.90"))
THRESHOLD_REPLATFORM_HIGH = float(os.getenv("THRESHOLD_REPLATFORM_HIGH", "0.90"))
THRESHOLD_REPLATFORM_LOW  = float(os.getenv("THRESHOLD_REPLATFORM_LOW",  "0.78"))

# ── SAW scoring weights (AHP-derived — see core/ahp_weights.py) ──────────────
# Loaded at runtime from AHP module; env vars allow override for A/B testing.
# Sum must equal 1.0 — validated by assert in scoring_decision_tools.py.
WEIGHT_EQUIVALENCE = float(os.getenv("WEIGHT_EQUIVALENCE", "0.0"))   # 0.0 = use AHP
WEIGHT_MATURITY    = float(os.getenv("WEIGHT_MATURITY",    "0.0"))
WEIGHT_BUDGET      = float(os.getenv("WEIGHT_BUDGET",      "0.0"))
WEIGHT_REGION      = float(os.getenv("WEIGHT_REGION",      "0.0"))
WEIGHT_COMPLEXITY  = float(os.getenv("WEIGHT_COMPLEXITY",  "0.0"))

# ── Tie-breaking threshold ────────────────────────────────────────────────────
SCORE_TIE_THRESHOLD = float(os.getenv("SCORE_TIE_THRESHOLD", "0.05"))

# ── Timing ────────────────────────────────────────────────────────────────────
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "10"))

# ── MCP IaC service ───────────────────────────────────────────────────────────
MCP_TIMEOUT_SECONDS = int(os.getenv("MCP_TIMEOUT_SECONDS", "30"))

# ── LLM file limits ───────────────────────────────────────────────────────────
MAX_FILES_LLM = int(os.getenv("MAX_FILES_LLM", "5"))

# ── Runner (TerraformRunner circuit breaker) ─────────────────────────────────
MAX_RUNNER_FAILURE_HISTORY = int(os.getenv("MAX_RUNNER_FAILURE_HISTORY", "10"))
MAX_RUNNER_TOTAL_ATTEMPTS  = int(os.getenv("MAX_RUNNER_TOTAL_ATTEMPTS",  "6"))
MAX_RUNNER_REGEN_COUNT     = int(os.getenv("MAX_RUNNER_REGEN_COUNT",     "3"))
MAX_RUNNER_RETRY_COUNT     = int(os.getenv("MAX_RUNNER_RETRY_COUNT",     "2"))
MAX_RUNNER_SIG_REPEATS     = int(os.getenv("MAX_RUNNER_SIG_REPEATS",     "3"))

# ── Security ─────────────────────────────────────────────────────────────────
MAX_SECURITY_REGEN = int(os.getenv("MAX_SECURITY_REGEN", "1"))

# ── Terraform provider version floors ────────────────────────────────────────
# Single source of truth — imported by iac_guidance.py and iac_fixers.py.
# Override via env for quick experiments without code changes.
PROVIDER_VERSION_AZURERM = os.getenv("TF_PROVIDER_AZURERM", ">= 4.0, < 5.0")
PROVIDER_VERSION_AWS     = os.getenv("TF_PROVIDER_AWS",     ">= 5.0, < 6.0")
PROVIDER_VERSION_GOOGLE  = os.getenv("TF_PROVIDER_GOOGLE",  ">= 5.0, < 6.0")

PROVIDER_VERSION_FLOORS: dict[str, str] = {
    "azurerm": PROVIDER_VERSION_AZURERM,
    "aws":     PROVIDER_VERSION_AWS,
    "google":  PROVIDER_VERSION_GOOGLE,
}

# ── IaC security retention defaults ──────────────────────────────────────────
# Named constants — avoid magic numbers scattered across iac_fixers.py.
RETENTION_BACKUP_DAYS       = int(os.getenv("RETENTION_BACKUP_DAYS",       "7"))   # DB backup
RETENTION_AUDIT_DAYS        = int(os.getenv("RETENTION_AUDIT_DAYS",        "90"))  # SQL audit
RETENTION_LOG_DAYS          = int(os.getenv("RETENTION_LOG_DAYS",          "30"))  # Log Analytics
RETENTION_SOFT_DELETE_DAYS  = int(os.getenv("RETENTION_SOFT_DELETE_DAYS",  "7"))   # blob soft-delete
RETENTION_QUEUE_LOG_DAYS    = int(os.getenv("RETENTION_QUEUE_LOG_DAYS",    "10"))  # queue logging

# ── Adaptive cooldown ────────────────────────────────────────────────────────
COOLDOWN_SMALL_THRESHOLD  = int(os.getenv("COOLDOWN_SMALL_THRESHOLD",  "5"))
COOLDOWN_MEDIUM_THRESHOLD = int(os.getenv("COOLDOWN_MEDIUM_THRESHOLD", "15"))
COOLDOWN_SMALL_SLEEP  = int(os.getenv("COOLDOWN_SMALL_SLEEP",  "2"))
COOLDOWN_MEDIUM_SLEEP = int(os.getenv("COOLDOWN_MEDIUM_SLEEP", "5"))
COOLDOWN_LARGE_SLEEP  = int(os.getenv("COOLDOWN_LARGE_SLEEP",  "10"))
