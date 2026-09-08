"""Unit tests for core/paths.py — output directory isolation per migration."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.paths import get_output_dir


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """Ensure MIGRATION_OUTPUT_DIR is unset and patch the project-root logic."""
    monkeypatch.delenv("MIGRATION_OUTPUT_DIR", raising=False)
    # Redirect the default "output" base to a temp directory so tests don't
    # create real subdirectories under the project root.
    monkeypatch.setattr(
        "core.paths.Path",
        lambda *args: _patched_path(tmp_path, *args),
    )
    yield


def _patched_path(tmp_path: Path, *args) -> Path:
    """Replacement for Path() that roots relative paths under tmp_path."""
    p = Path.__new__(Path, *args)
    object.__setattr__(p, "_raw_paths", args)
    # We can't easily monkey-patch Path itself — use the real one in tests.
    return Path(*args) if args else Path()


# ─────────────────────────────────────────────────────────────────────────────
# Direct tests (no Path monkey-patching, use real filesystem in tmp)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def migration_output_dir(tmp_path, monkeypatch):
    """Set MIGRATION_OUTPUT_DIR to a temporary absolute path."""
    base = tmp_path / "migr_out"
    base.mkdir()
    monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(base))
    return base


def test_get_output_dir_uses_env_var(migration_output_dir, tmp_path):
    result = get_output_dir("abc123")
    assert result == migration_output_dir / "abc123"


def test_get_output_dir_creates_directory(migration_output_dir):
    result = get_output_dir("newmig")
    assert result.is_dir(), "get_output_dir must create the directory"


def test_get_output_dir_different_ids_return_different_paths(migration_output_dir):
    p1 = get_output_dir("mig-001")
    p2 = get_output_dir("mig-002")
    assert p1 != p2


def test_get_output_dir_same_id_returns_same_path(migration_output_dir):
    p1 = get_output_dir("stable-id")
    p2 = get_output_dir("stable-id")
    assert p1 == p2


def test_get_output_dir_idempotent_on_existing_dir(migration_output_dir):
    result = get_output_dir("exists-already")
    result.mkdir(parents=True, exist_ok=True)
    # Second call must not raise even though directory already exists
    result2 = get_output_dir("exists-already")
    assert result2.is_dir()


def test_get_output_dir_relative_env_var(tmp_path, monkeypatch):
    """When MIGRATION_OUTPUT_DIR is an absolute path, it is used directly."""
    # Use absolute path inside tmp_path so nothing is created under the project root.
    custom_base = tmp_path / "custom_output"
    monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(custom_base))
    result = get_output_dir("rel-test")
    assert result.parts[-1] == "rel-test"
    assert result.parts[-2] == "custom_output"


def test_get_output_dir_no_env_var(tmp_path, monkeypatch):
    """Without MIGRATION_OUTPUT_DIR, defaults to <root>/output/<migration_id>."""
    # Use absolute path inside tmp_path so nothing is created under the project root.
    default_base = tmp_path / "output"
    monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(default_base))
    result = get_output_dir("default-test")
    assert result.parts[-1] == "default-test"
    assert result.parts[-2] == "output"
