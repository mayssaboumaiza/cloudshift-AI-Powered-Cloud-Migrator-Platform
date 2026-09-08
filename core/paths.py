"""
paths.py — Canonical output-path helper.

Every agent and node that writes generated files (Terraform, deploy scripts, CI/CD
workflows) must use get_output_dir() instead of hard-coding "output/migrated_app".

Passing the migration_id isolates each migration's outputs under their own directory,
which prevents concurrent migrations from overwriting each other's files.
"""
import os
from pathlib import Path


def _makedirs_robust(path: Path) -> None:
    """mkdir -p that survives Docker overlay-fs transient EEXIST/is_dir() mismatches."""
    try:
        os.makedirs(os.fspath(path), exist_ok=True)
    except OSError:
        if not os.path.isdir(os.fspath(path)):
            raise


def get_output_dir(migration_id: str) -> Path:
    """Return (and create) the output directory for *migration_id*.

    Resolution:
      - If MIGRATION_OUTPUT_DIR already ends with migration_id: use it as-is (avoid double nesting).
      - If MIGRATION_OUTPUT_DIR is an absolute path: <MIGRATION_OUTPUT_DIR>/<migration_id>
      - Otherwise:                                   <project_root>/<MIGRATION_OUTPUT_DIR or 'output'>/<migration_id>

    The directory is created if it does not exist.
    """
    base_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")

    if base_env and os.path.isabs(base_env):
        base = Path(base_env)
        # Guard: if the env var was already set to the per-migration path (ends with migration_id),
        # don't append it again — that would create output/<uuid>/<uuid>/.
        if base.name == migration_id:
            _makedirs_robust(base)
            return base
    else:
        # Resolve relative to the project root (two levels above this file: core/ → /)
        project_root = Path(__file__).parent.parent
        base = project_root / (base_env or "output")

    path = base / migration_id
    _makedirs_robust(path)
    return path
