"""
scripts/ci_drift_check.py — CI pre-deploy drift gate.

Runs as a blocking step before any deployment. Exits non-zero on:
  - Blocking drift (missing tables, missing columns, FK mismatches)
  - Version mismatch between alembic_version and expected head
  - DB connection failure

Safe to run in parallel with other CI steps — read-only, no DDL.

Usage:
    python scripts/ci_drift_check.py              # check only
    python scripts/ci_drift_check.py --fail-fast  # exit 1 on first issue
    python scripts/ci_drift_check.py --json       # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make project root importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_utils.reconciler import (
    ReconciliationEngine,
    DriftKind,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="CI pre-deploy schema drift gate")
    parser.add_argument("--fail-fast", action="store_true",
                        help="Exit 1 on first blocking drift item found")
    parser.add_argument("--json", action="store_true",
                        help="Print machine-readable JSON report to stdout")
    parser.add_argument("--warn-only", action="store_true",
                        help="Print warnings but always exit 0 (useful for gradual rollout)")
    args = parser.parse_args()

    engine = ReconciliationEngine.from_env()

    try:
        report = engine.detect()
    except Exception as exc:
        print(f"ERROR: Cannot connect to database: {exc}", file=sys.stderr)
        return 2

    # ── Build CI report ───────────────────────────────────────────────────────
    blocking = [i for i in report.items if not i.repairable]
    warnings = [i for i in report.items if i.repairable]

    if args.json:
        print(json.dumps({
            "clean":     report.clean,
            "blocking":  [{"kind": i.kind.value, "object": i.object_name, "detail": i.detail}
                          for i in blocking],
            "warnings":  [{"kind": i.kind.value, "object": i.object_name, "detail": i.detail}
                          for i in warnings],
            "alembic":   report.alembic_current,
        }, indent=2))
    else:
        engine.print_report(report)

    # ── Fail conditions ───────────────────────────────────────────────────────
    if blocking:
        severity = "WARNING" if args.warn_only else "ERROR"
        for item in blocking:
            print(f"{severity}: [{item.kind.value}] {item.object_name} — {item.detail}",
                  file=sys.stderr)
            if args.fail_fast:
                return 0 if args.warn_only else 1

        return 0 if args.warn_only else 1

    if warnings:
        for item in warnings:
            print(f"WARNING: [{item.kind.value}] {item.object_name} — {item.detail}",
                  file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
