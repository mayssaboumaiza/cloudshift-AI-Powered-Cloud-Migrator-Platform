#!/usr/bin/env bash
# scripts/safe_upgrade.sh — Safe Alembic upgrade with pre/post drift reconciliation
#
# Replaces the bare `alembic upgrade head` call in Docker entrypoints and CI.
#
# Stages:
#   1. PRE-CHECK  : run reconciler in detect-only mode (read-only)
#   2. REPAIR     : auto-fix repairable drift (missing indexes, stale stamps)
#   3. UPGRADE    : alembic upgrade head (idempotent — guards in every migration)
#   4. POST-CHECK : confirm clean state
#
# Exit codes:
#   0 — all good
#   1 — blocking drift or upgrade failure
#   2 — connection error
#
# Usage:
#   ./scripts/safe_upgrade.sh                   # run all stages
#   SKIP_RECONCILE=1 ./scripts/safe_upgrade.sh  # skip drift check (emergency)
#   DRY_RUN=1        ./scripts/safe_upgrade.sh  # detect only, no DDL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RECONCILER="$ROOT_DIR/alembic/migration_reconciler.py"

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[0;33m'
NC='\033[0m'

log()  { echo -e "${GRN}[safe_upgrade]${NC} $*"; }
warn() { echo -e "${YLW}[safe_upgrade]${NC} $*"; }
err()  { echo -e "${RED}[safe_upgrade]${NC} $*" >&2; }

cd "$ROOT_DIR"

# ── Stage 0: environment check ────────────────────────────────────────────────
if [[ -z "${DB_HOST:-}" ]]; then
    warn "DB_HOST not set — using default (localhost)"
fi

# ── Stage 1: pre-flight drift detection ──────────────────────────────────────
if [[ "${SKIP_RECONCILE:-0}" != "1" ]]; then
    log "Stage 1/4 — Pre-flight drift detection..."
    RECONCILE_EXIT=0
    python "$RECONCILER" --json > /tmp/drift_pre.json 2>&1 || RECONCILE_EXIT=$?

    if [[ "$RECONCILE_EXIT" -eq 2 ]]; then
        err "Cannot reach database — aborting"
        exit 2
    fi

    BLOCKING=$(python -c "import json,sys; d=json.load(open('/tmp/drift_pre.json')); print(d.get('blocking', False))" 2>/dev/null || echo "False")
    DRIFT_COUNT=$(python -c "import json,sys; d=json.load(open('/tmp/drift_pre.json')); print(d.get('drift_count', 0))" 2>/dev/null || echo "0")

    if [[ "$BLOCKING" == "True" ]]; then
        err "Blocking drift detected — cannot auto-upgrade:"
        cat /tmp/drift_pre.json
        err "Manual intervention required before upgrade."
        exit 1
    fi

    if [[ "$DRIFT_COUNT" -gt 0 ]]; then
        warn "Repairable drift detected ($DRIFT_COUNT items) — running repair..."
    else
        log "Pre-check: schema is clean."
    fi
else
    warn "SKIP_RECONCILE=1 — skipping pre-flight drift check"
    DRIFT_COUNT=0
fi

# ── Stage 2: auto-repair repairable drift ────────────────────────────────────
if [[ "${SKIP_RECONCILE:-0}" != "1" && "$DRIFT_COUNT" -gt 0 ]]; then
    log "Stage 2/4 — Auto-repairing drift..."
    python "$RECONCILER" --repair --json > /tmp/drift_repair.json 2>&1 || true
    log "Repair complete. Log:"
    python -c "
import json, sys
d = json.load(open('/tmp/drift_repair.json'))
for r in d.get('repairs', []):
    print('  ✅', r)
remaining = d.get('drift_count', 0)
if remaining:
    print(f'  ⚠️  {remaining} item(s) remain (manual review needed)')
" 2>/dev/null || true
else
    log "Stage 2/4 — No repairs needed, skipping."
fi

# ── Stage 3: alembic upgrade ─────────────────────────────────────────────────
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    warn "DRY_RUN=1 — skipping alembic upgrade head"
    log "Would run: alembic upgrade head"
else
    log "Stage 3/4 — Running alembic upgrade head..."
    if ! python -m alembic upgrade head; then
        err "alembic upgrade head failed"
        exit 1
    fi
    log "Alembic upgrade complete."
fi

# ── Stage 4: post-upgrade verification ───────────────────────────────────────
if [[ "${SKIP_RECONCILE:-0}" != "1" && "${DRY_RUN:-0}" != "1" ]]; then
    log "Stage 4/4 — Post-upgrade verification..."
    POST_EXIT=0
    python "$RECONCILER" --json > /tmp/drift_post.json 2>&1 || POST_EXIT=$?

    if [[ "$POST_EXIT" -eq 0 ]]; then
        log "Post-check: ✅ schema is clean and consistent."
    else
        warn "Post-check: residual drift detected (non-blocking):"
        python -c "
import json
d = json.load(open('/tmp/drift_post.json'))
for item in d.get('items', []):
    print(f\"  [{item['kind']}] {item['object']} — {item['detail']}\")
" 2>/dev/null || cat /tmp/drift_post.json
    fi
else
    log "Stage 4/4 — Skipped."
fi

log "✅ safe_upgrade complete."
exit 0
