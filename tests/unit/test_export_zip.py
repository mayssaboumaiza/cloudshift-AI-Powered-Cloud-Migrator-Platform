"""
tests/unit/test_export_zip.py — Tests de la création de l'archive ZIP (RF-13).

export_zip_node() est un nœud LangGraph qui reçoit un MigrationState et produit
une archive ZIP contenant :
  - migration_plan.json (toujours présent)
  - preview_report.md  (si artifacts["preview_report"] est défini)

On teste la fonction directement en lui passant un dict MigrationState minimal.
"""
import json
import zipfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pipeline.nodes.planning_nodes import export_zip_node


# ── Helpers ───────────────────────────────────────────────────────────────────

def _base_state(tmp_path: Path, migration_plan: dict | None = None) -> dict:
    """Construit un MigrationState minimal pour export_zip_node."""
    return {
        "migration_id": "test-migration-001",
        "migration_plan": migration_plan or {
            "services": [
                {"source": "aws_s3_bucket", "target": "azurerm_storage_account", "strategy": "REHOST"}
            ],
            "summary": {"estimated_total_monthly": 45.0},
        },
        "artifacts": {},
        "deployment_status": "rejected",
        "thread_id": "thread-001",
        "source_cloud": "aws",
        "target_cloud": "azure",
    }


def _run_export(state: dict, tmp_path: Path) -> dict:
    """Lance export_zip_node avec output dir pointant vers tmp_path."""
    with patch("pipeline.nodes.publish_nodes.OUTPUT_DIR", str(tmp_path)), \
         patch("core.paths.get_output_dir", return_value=tmp_path):
        result = export_zip_node(state)
    return result


# ── Tests de création de l'archive ───────────────────────────────────────────

class TestExportZipCreation:

    def test_export_zip_node_returns_dict(self, tmp_path):
        """export_zip_node retourne un dict (MigrationState partiel)."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        assert isinstance(result, dict)

    def test_deployment_status_set_to_exported(self, tmp_path):
        """Le statut de déploiement est mis à 'exported' après création du ZIP."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        assert result.get("deployment_status") == "exported"

    def test_zip_path_recorded_in_artifacts(self, tmp_path):
        """Le chemin du ZIP est enregistré dans artifacts["export_zip_path"]."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        artifacts = result.get("artifacts", {})
        assert "export_zip_path" in artifacts
        zip_path = artifacts["export_zip_path"]
        assert zip_path.endswith(".zip")

    def test_zip_file_is_created_on_disk(self, tmp_path):
        """Le fichier ZIP est physiquement créé sur le disque."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]
        assert Path(zip_path).exists()

    def test_zip_file_is_not_empty(self, tmp_path):
        """L'archive ZIP n'est pas vide."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]
        assert Path(zip_path).stat().st_size > 0


# ── Tests du contenu de l'archive ─────────────────────────────────────────────

class TestExportZipContent:

    def test_zip_contains_migration_plan_json(self, tmp_path):
        """L'archive contient migration_plan.json."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        assert "migration_plan.json" in names

    def test_migration_plan_json_is_valid_json(self, tmp_path):
        """migration_plan.json dans l'archive est du JSON valide."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]

        with zipfile.ZipFile(zip_path, "r") as zf:
            content = zf.read("migration_plan.json")
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_migration_plan_json_matches_state_plan(self, tmp_path):
        """Le contenu de migration_plan.json correspond au plan dans le state."""
        plan = {
            "services": [{"source": "aws_rds_instance", "target": "azurerm_postgresql_flexible_server"}],
            "summary": {"estimated_total_monthly": 120.0},
        }
        state = _base_state(tmp_path, migration_plan=plan)
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]

        with zipfile.ZipFile(zip_path, "r") as zf:
            content = json.loads(zf.read("migration_plan.json"))
        assert content["services"][0]["source"] == "aws_rds_instance"

    def test_preview_report_included_when_present(self, tmp_path):
        """preview_report.md est inclus si artifacts["preview_report"] est défini."""
        state = _base_state(tmp_path)
        state["artifacts"]["preview_report"] = "# Migration Preview\n\nSome content here."
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        assert "preview_report.md" in names

    def test_preview_report_content_matches(self, tmp_path):
        """Le contenu de preview_report.md correspond à artifacts["preview_report"]."""
        preview = "# Migration Preview\n\nThis is the preview report."
        state = _base_state(tmp_path)
        state["artifacts"]["preview_report"] = preview
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]

        with zipfile.ZipFile(zip_path, "r") as zf:
            content = zf.read("preview_report.md").decode("utf-8")
        assert content == preview

    def test_zip_is_valid_zipfile(self, tmp_path):
        """L'archive est un ZIP valide reconnu par le module zipfile."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]
        assert zipfile.is_zipfile(zip_path)


# ── Tests avec state minimal ──────────────────────────────────────────────────

class TestExportZipEdgeCases:

    def test_export_with_empty_plan(self, tmp_path):
        """export_zip_node fonctionne même si migration_plan est vide."""
        state = _base_state(tmp_path, migration_plan={})
        result = _run_export(state, tmp_path)
        assert result.get("deployment_status") == "exported"

    def test_export_with_no_preview_report(self, tmp_path):
        """Pas de preview_report → ZIP valide sans preview_report.md."""
        state = _base_state(tmp_path)
        result = _run_export(state, tmp_path)
        zip_path = result["artifacts"]["export_zip_path"]

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        # migration_plan.json doit être présent
        assert "migration_plan.json" in names
        # preview_report.md ne doit PAS être présent (pas de rapport)
        assert "preview_report.md" not in names

    def test_export_with_none_migration_plan(self, tmp_path):
        """Si migration_plan est None, export_zip_node ne plante pas."""
        state = _base_state(tmp_path, migration_plan=None)
        # Ne doit pas lever d'exception
        result = _run_export(state, tmp_path)
        assert "deployment_status" in result
