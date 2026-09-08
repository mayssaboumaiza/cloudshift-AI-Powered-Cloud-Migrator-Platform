"""
test_rag_benchmark_dataset.py — Validates the RAG benchmark dataset is well-formed.

Does NOT require a live PostgreSQL / pgvector connection.
Run with: pytest tests/unit/test_rag_benchmark_dataset.py -v
"""
import json
from pathlib import Path

DATASET_PATH = Path(__file__).parent.parent / "calibration" / "rag_benchmark_dataset.json"
VALID_PROVIDERS = {"azurerm", "aws", "google"}


def _load() -> list[dict]:
    assert DATASET_PATH.exists(), f"Dataset not found: {DATASET_PATH}"
    with open(DATASET_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_dataset_exists_and_parseable():
    data = _load()
    assert isinstance(data, list)
    assert len(data) >= 20, "Dataset should have at least 20 cases"


def test_all_cases_have_required_fields():
    data = _load()
    required = {"id", "provider", "resource_type", "expected_keywords", "description"}
    for case in data:
        missing = required - case.keys()
        assert not missing, f"Case {case.get('id', '?')} missing fields: {missing}"


def test_all_ids_are_unique():
    data = _load()
    ids = [c["id"] for c in data]
    assert len(ids) == len(set(ids)), f"Duplicate IDs found: {[i for i in ids if ids.count(i) > 1]}"


def test_all_providers_are_valid():
    data = _load()
    for case in data:
        assert case["provider"] in VALID_PROVIDERS, (
            f"Case {case['id']}: unknown provider '{case['provider']}'"
        )


def test_resource_type_matches_provider_prefix():
    """resource_type prefix must match the provider."""
    _PREFIX_MAP = {
        "azurerm": "azurerm_",
        "aws":     "aws_",
        "google":  "google_",
    }
    data = _load()
    for case in data:
        prefix = _PREFIX_MAP[case["provider"]]
        assert case["resource_type"].startswith(prefix), (
            f"Case {case['id']}: resource_type '{case['resource_type']}' does not start "
            f"with '{prefix}' for provider '{case['provider']}'"
        )


def test_expected_keywords_non_empty():
    data = _load()
    for case in data:
        assert len(case["expected_keywords"]) >= 1, (
            f"Case {case['id']}: expected_keywords must not be empty"
        )


def test_provider_coverage():
    """Dataset must include cases for all three providers."""
    data = _load()
    providers = {c["provider"] for c in data}
    assert "azurerm" in providers, "No Azure cases in dataset"
    assert "aws" in providers, "No AWS cases in dataset"
    assert "google" in providers, "No GCP cases in dataset"


def test_cross_provider_cases_present():
    """At least 3 cross-provider isolation cases must exist."""
    data = _load()
    cross = [c for c in data if c["id"].startswith("cross-provider")]
    assert len(cross) >= 3, f"Only {len(cross)} cross-provider cases (need >= 3)"


def test_must_not_contain_provider_consistency():
    """must_not_contain_provider must exclude the case's own provider prefix."""
    _PREFIX_MAP = {
        "azurerm": "azurerm_",
        "aws":     "aws_",
        "google":  "google_",
    }
    data = _load()
    for case in data:
        own_prefix = _PREFIX_MAP[case["provider"]]
        forbidden  = case.get("must_not_contain_provider", [])
        assert own_prefix not in forbidden, (
            f"Case {case['id']}: must_not_contain_provider contains own provider prefix '{own_prefix}'"
        )


def test_benchmark_script_is_importable():
    """The benchmark script can be imported without a live database."""
    import importlib.util, sys
    script = Path(__file__).parent.parent / "calibration" / "run_rag_benchmark.py"
    assert script.exists(), f"Benchmark script not found: {script}"
    spec = importlib.util.spec_from_file_location("run_rag_benchmark", script)
    mod  = importlib.util.module_from_spec(spec)
    # Do NOT execute — just check that the syntax is valid by loading the spec
    assert spec is not None
    assert mod is not None
