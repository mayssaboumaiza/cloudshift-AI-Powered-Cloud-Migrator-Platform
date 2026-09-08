"""
ahp_weights.py — AHP (Analytic Hierarchy Process, Saaty 1980) weight derivation
                 for the Cloud Migrator SAW scoring formula.

PURPOSE
-------
Replaces manually assigned SAW weights with mathematically derived weights.
AHP does not replace SAW — it only computes the weights that SAW uses.

FLOW
----
  BEFORE: hardcoded weights (0.40/0.20/0.15/0.15/0.10) → SAW → score → 7R
  AFTER : AHP pairwise matrix → derived weights → SAW → score → 7R

The pipeline LangGraph graph is unchanged.

CRITERIA (5)
------------
  1. equivalence   — cosine similarity of Terraform doc embeddings (pgvector)
  2. maturity      — GitHub multi-window commit analysis (check_service_maturity)
  3. budget_fit    — estimated monthly cost vs declared budget (get_pricing)
  4. region_fit    — service availability in target region
  5. complexity    — migration effort estimate (inverted: lower complexity → higher score)

PAIRWISE MATRIX JUSTIFICATION
------------------------------
Each cell A[i][j] answers: "How many times more important is criterion i vs j?"
Scale: 1 = equally important, 3 = moderately more, 5 = strongly more,
       7 = very strongly more, 9 = absolutely more (Saaty 1980)

  equiv vs maturity   = 2 → AWS MAP Framework: functional fit is the primary filter
                             before provider maturity (Gartner G00767542, 2022)
  equiv vs budget     = 3 → FinOps Foundation: cost is a post-selection validation,
                             not a go/no-go criterion
  equiv vs region     = 4 → RGPD Art.44-49: regional constraint is secondary to
                             functional compatibility
  equiv vs complexity = 7 → Agile: effort is a planning factor, not a faisability gate
  maturity vs budget  = 2 → HashiCorp Provider Tiers: deprecated/experimental providers
                             represent operational risk that outweighs cost savings
  maturity vs region  = 3 → Provider maturity has broader production impact than
                             geographic constraint
  maturity vs complex = 5
  budget vs region    = 2 → FinOps Foundation: cost optimisation precedes geographic fit
  budget vs complex   = 4
  region vs complex   = 3 → RGPD: legal constraint (data residency) > technical effort

CONSISTENCY RATIO
-----------------
CR = CI / RI where RI = 1.12 for n=5 (Saaty random consistency index)
CR < 0.10 confirms the judgements are coherent (not random)
"""

from __future__ import annotations

import numpy as np

# ── Pairwise comparison matrix ────────────────────────────────────────────────
# Rows/cols: [equivalence, maturity, budget_fit, region_fit, complexity]
_MATRIX = np.array([
    [1,     2,    3,    4,    7  ],   # equivalence
    [1/2,   1,    2,    3,    5  ],   # maturity
    [1/3,   1/2,  1,    2,    4  ],   # budget_fit
    [1/4,   1/3,  1/2,  1,    3  ],   # region_fit
    [1/7,   1/5,  1/4,  1/3,  1  ],   # complexity
], dtype=float)

_CRITERIA = ["equivalence", "maturity", "budget_fit", "region_fit", "complexity"]
_N = len(_CRITERIA)

# ── Weight derivation (geometric mean of normalised columns) ─────────────────
_col_sums  = _MATRIX.sum(axis=0)
_normalised = _MATRIX / _col_sums
_raw_weights = _normalised.mean(axis=1)

# Saaty random consistency index for n=5
_RI = 1.12

_lambda_max = float(np.mean(np.dot(_MATRIX, _raw_weights) / _raw_weights))
_CI = (_lambda_max - _N) / (_N - 1)
_CR = _CI / _RI

assert _CR < 0.10, (
    f"AHP consistency ratio CR={_CR:.4f} >= 0.10 — "
    "the pairwise comparison matrix is inconsistent. "
    "Review the judgement values in _MATRIX."
)

# ── Public API ────────────────────────────────────────────────────────────────

_rounded = [round(float(w), 6) for w in _raw_weights]
# Correct floating-point rounding drift so sum(weights) == 1.0 exactly
_drift = 1.0 - sum(_rounded)
_rounded[0] = round(_rounded[0] + _drift, 6)

AHP_WEIGHTS: dict[str, float] = dict(zip(_CRITERIA, _rounded))
"""Derived SAW weights, sum = 1.0, CR < 0.10 (consistent).

Actual computed values (verified): {equivalence: 0.425322, maturity: 0.261830,
                   budget_fit: 0.163079, region_fit: 0.103158, complexity: 0.046611}
These match the report (Annex C / Table 2.5) rounded to 4 decimals
(0.4253 / 0.2618 / 0.1631 / 0.1032 / 0.0466).
"""

AHP_CONSISTENCY_RATIO: float = round(_CR, 6)
"""Saaty Consistency Ratio. Must be < 0.10. Value ≈ 0.018 for this matrix."""

AHP_LAMBDA_MAX: float = round(_lambda_max, 6)
"""Principal eigenvalue approximation. Expected ≈ 5.08 for n=5."""

AHP_SOURCES: dict[str, str] = {
    "equivalence": "AWS MAP Framework — Gartner G00767542, 2022",
    "maturity":    "HashiCorp Provider Tiers (official tier classification)",
    "budget_fit":  "FinOps Foundation — 50% budget threshold as WAF margin",
    "region_fit":  "RGPD Art.44-49 data residency obligations",
    "complexity":  "Agile Framework — sprint effort factor (planning, not gate)",
}
"""Academic / industry source justifying each criterion's relative weight."""

AHP_MATRIX_LABELS: list[str] = _CRITERIA
"""Ordered list matching rows and columns of the pairwise matrix."""


# ── Validation helper ─────────────────────────────────────────────────────────

def validate_weights() -> dict:
    """Return a validation report — useful for tests and logging."""
    weight_sum = sum(AHP_WEIGHTS.values())
    return {
        "weights":           AHP_WEIGHTS,
        "weight_sum":        round(weight_sum, 8),
        "sum_ok":            abs(weight_sum - 1.0) < 1e-4,
        "consistency_ratio": AHP_CONSISTENCY_RATIO,
        "cr_ok":             AHP_CONSISTENCY_RATIO < 0.10,
        "lambda_max":        AHP_LAMBDA_MAX,
        "n":                 _N,
        "order":             sorted(AHP_WEIGHTS, key=lambda k: -AHP_WEIGHTS[k]),
    }


# ── CLI validation ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    report = validate_weights()
    print("\n-- AHP Weight Derivation Report --")
    print("Criteria order (most -> least important):")
    for crit in report["order"]:
        w = AHP_WEIGHTS[crit]
        bar = "#" * int(w * 50)
        src = AHP_SOURCES[crit]
        print(f"  {crit:<15} {w:.4f}  {bar:<22}  [{src}]")
    print(f"\nlambda_max = {report['lambda_max']:.4f}  (expected ~{_N + 0.08:.2f} for n={_N})")
    print(f"CI         = {_CI:.4f}")
    print(f"RI         = {_RI} (Saaty, n={_N})")
    print(f"CR         = {report['consistency_ratio']:.4f}  (threshold < 0.10)")
    cr_status = "[OK] CONSISTENT" if report["cr_ok"] else "[FAIL] INCONSISTENT -- revise matrix"
    print(f"Status: {cr_status}")
    print(f"Sum(weights) = {report['weight_sum']:.8f}  ({'[OK]' if report['sum_ok'] else '[FAIL] NOT 1.0'})")
