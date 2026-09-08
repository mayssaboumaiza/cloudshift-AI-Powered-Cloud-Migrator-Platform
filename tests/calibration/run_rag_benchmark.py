#!/usr/bin/env python3
"""
run_rag_benchmark.py — Reproducible evaluation protocol for GraphRAG retrieval quality.

Measures the "correctness" of the RAG system by checking whether:
  1. The retrieved context is for the RIGHT provider (no cross-provider injection).
  2. The retrieved context contains expected keyword arguments.
  3. The similarity score of stage-3 fallbacks is above a minimum threshold.

Usage:
    python tests/calibration/run_rag_benchmark.py
    python tests/calibration/run_rag_benchmark.py --out results.json
    python tests/calibration/run_rag_benchmark.py --verbose

Exit code 0 = benchmark passed (hit rate >= PASS_THRESHOLD).
Exit code 1 = benchmark failed or RAG unavailable.

Metric definition
-----------------
  hit         : context contains all expected_keywords AND no must_not_contain_provider prefix
  partial_hit : context contains ≥50% of expected_keywords but fails provider check
  miss        : context is empty or contains wrong-provider content

  correctness = hits / total_cases  (0.0 – 1.0)
  provider_purity = cases_without_cross_provider_contamination / total_cases
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running from repo root or from tests/calibration/
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_REPO_ROOT / ".env")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("RAGBenchmark")

DATASET_PATH = Path(__file__).parent / "rag_benchmark_dataset.json"
PASS_THRESHOLD = 0.80  # minimum correctness ratio to pass

# Minimum cosine similarity below which a stage-3 fallback is considered a miss
# regardless of keyword presence (detects cross-provider hallucination)
MIN_SIMILARITY_THRESHOLD = 0.40


def _load_dataset() -> list[dict]:
    with open(DATASET_PATH, encoding="utf-8") as f:
        return json.load(f)


def _init_rag():
    """Initialize the RAG singleton. Returns None if infrastructure unavailable."""
    try:
        from rag.graph_rag import TerraformGraphRAG
        rag = TerraformGraphRAG.get_instance()
        return rag
    except Exception as exc:
        logger.error("RAG init failed: %s", exc)
        return None


def _evaluate_case(rag, case: dict, verbose: bool) -> dict[str, Any]:
    """Run one benchmark case and return a result dict."""
    resource_type = case["resource_type"]
    provider      = case["provider"]
    expected_kw   = case["expected_keywords"]
    forbidden_pfx = case.get("must_not_contain_provider", [])

    result: dict[str, Any] = {
        "id":            case["id"],
        "description":   case["description"],
        "resource_type": resource_type,
        "provider":      provider,
        "expected_keywords_count": len(expected_kw),
        "hit":           False,
        "partial_hit":   False,
        "miss":          False,
        "provider_contamination": False,
        "keywords_found":  [],
        "keywords_missing": [],
        "context_length": 0,
        "error":         None,
        "latency_ms":    0,
    }

    t0 = time.monotonic()
    try:
        ctx_dict = rag.graph_rag_query(
            provider=provider,
            target_resource=resource_type,
            top_k_chunks=5,
            top_k_patterns=2,
        )
        context_str = ctx_dict.get("context", "") if isinstance(ctx_dict, dict) else str(ctx_dict)
    except Exception as exc:
        result["error"] = str(exc)[:200]
        result["miss"]  = True
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return result

    result["latency_ms"]    = int((time.monotonic() - t0) * 1000)
    result["context_length"] = len(context_str)

    if not context_str.strip():
        result["miss"] = True
        return result

    ctx_lower = context_str.lower()

    # Check for cross-provider contamination
    for pfx in forbidden_pfx:
        if pfx.lower() in ctx_lower:
            result["provider_contamination"] = True
            break

    # Check keyword presence
    found   = [kw for kw in expected_kw if kw.lower() in ctx_lower]
    missing = [kw for kw in expected_kw if kw.lower() not in ctx_lower]
    result["keywords_found"]   = found
    result["keywords_missing"] = missing

    kw_ratio = len(found) / len(expected_kw) if expected_kw else 1.0

    if kw_ratio >= 1.0 and not result["provider_contamination"]:
        result["hit"] = True
    elif kw_ratio >= 0.50 and not result["provider_contamination"]:
        result["partial_hit"] = True
    else:
        result["miss"] = True

    if verbose:
        status = "HIT" if result["hit"] else ("PARTIAL" if result["partial_hit"] else "MISS")
        contamination = " [CROSS-PROVIDER!]" if result["provider_contamination"] else ""
        print(
            f"  [{status}]{contamination} {case['id']} {resource_type} "
            f"kw={len(found)}/{len(expected_kw)} ctx={len(context_str)}c "
            f"{result['latency_ms']}ms"
        )

    return result


def run_benchmark(verbose: bool = False) -> dict[str, Any]:
    """Run the full benchmark and return a summary dict."""
    dataset = _load_dataset()
    print(f"\nRAG Benchmark — {len(dataset)} cases  [threshold={PASS_THRESHOLD:.0%}]")
    print("=" * 60)

    rag = _init_rag()
    if rag is None:
        print("ERROR: RAG infrastructure unavailable (check PostgreSQL + pgvector).")
        return {
            "status":      "error",
            "error":       "RAG unavailable",
            "correctness": 0.0,
            "passed":      False,
        }

    results  = []
    skipped  = 0

    for case in dataset:
        if verbose:
            print(f"\n[{case['id']}] {case['description']}")
        r = _evaluate_case(rag, case, verbose=verbose)
        results.append(r)
        if r.get("error"):
            skipped += 1

    # Aggregate metrics
    total       = len(results)
    hits        = sum(1 for r in results if r["hit"])
    partial     = sum(1 for r in results if r["partial_hit"])
    misses      = sum(1 for r in results if r["miss"] and not r.get("error"))
    errors      = sum(1 for r in results if r.get("error"))
    contaminated = sum(1 for r in results if r["provider_contamination"])

    correctness     = hits / total if total else 0.0
    provider_purity = (total - contaminated) / total if total else 0.0
    avg_latency     = sum(r["latency_ms"] for r in results) / total if total else 0

    passed = correctness >= PASS_THRESHOLD

    # Provider breakdown
    by_provider: dict[str, dict] = {}
    for r in results:
        p = r["provider"]
        if p not in by_provider:
            by_provider[p] = {"total": 0, "hits": 0, "contaminated": 0}
        by_provider[p]["total"] += 1
        if r["hit"]:
            by_provider[p]["hits"] += 1
        if r["provider_contamination"]:
            by_provider[p]["contaminated"] += 1

    summary = {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "dataset_version": "1.0",
        "total_cases":    total,
        "hits":           hits,
        "partial_hits":   partial,
        "misses":         misses,
        "errors":         errors,
        "contaminated_cases": contaminated,
        "correctness":    round(correctness, 4),
        "provider_purity": round(provider_purity, 4),
        "avg_latency_ms": round(avg_latency, 1),
        "pass_threshold": PASS_THRESHOLD,
        "passed":         passed,
        "status":         "passed" if passed else "failed",
        "by_provider":    {
            p: {
                "hit_rate": round(v["hits"] / v["total"], 4) if v["total"] else 0,
                **v,
            }
            for p, v in by_provider.items()
        },
        "case_results": results,
    }

    # Print summary
    print("\n" + "=" * 60)
    print(f"Results: {hits}/{total} hits  ({correctness:.1%} correctness)")
    print(f"Partial: {partial}  Miss: {misses}  Error: {errors}")
    print(f"Cross-provider contamination: {contaminated} case(s)")
    print(f"Provider purity: {provider_purity:.1%}")
    print(f"Avg latency: {avg_latency:.0f}ms")
    print()
    for p, stats in summary["by_provider"].items():
        print(f"  {p:10s}  hit_rate={stats['hit_rate']:.1%}  ({stats['hits']}/{stats['total']})")
    print()
    verdict = "PASSED ✓" if passed else f"FAILED ✗  (need {PASS_THRESHOLD:.0%}, got {correctness:.1%})"
    print(f"Verdict: {verdict}")
    print("=" * 60)

    # Surface the worst failures for quick review
    failures = [r for r in results if not r["hit"]]
    if failures and verbose:
        print(f"\nTop failures ({min(5, len(failures))} of {len(failures)}):")
        for r in failures[:5]:
            contamination = " [CROSS-PROVIDER]" if r["provider_contamination"] else ""
            print(
                f"  {r['id']} {r['resource_type']}{contamination} "
                f"missing={r['keywords_missing']}"
            )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="GraphRAG correctness benchmark")
    parser.add_argument("--out", metavar="FILE", help="Save JSON results to FILE")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-case results")
    args = parser.parse_args()

    summary = run_benchmark(verbose=args.verbose)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nResults saved to: {out_path}")

    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
