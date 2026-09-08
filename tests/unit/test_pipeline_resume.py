"""
tests/unit/test_pipeline_resume.py — Persistance et reprise de l'état pipeline (RNF-2).

Couvre RNF-2 (Continuité de service / Reprise après interruption) :
  - MigrationState est un TypedDict sérialisable en JSON (condition LangGraph checkpoint)
  - Tous les champs critiques survivent à un aller-retour JSON
  - MigrationStateValidator refuse les états incomplets à chaque étape
  - validate_repo_url / validate_cloud sont cohérents avec les valeurs attendues
  - StateValidationError contient le nom du champ manquant

Note : on ne teste PAS le checkpointing LangGraph/PostgreSQL (appel DB).
On teste uniquement la logique de validation et la sérialisabilité JSON,
qui est la pré-condition nécessaire pour que le checkpoint DB fonctionne.
"""
import json
import pytest
from agents.pipeline_state import MigrationState
from agents.state_validator import (
    MigrationStateValidator,
    StateValidationError,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _minimal_analyze_state() -> dict:
    return {
        "thread_id": "thread-resume-001",
        "migration_id": "mig-001",
        "repo_url": "https://github.com/org/my-app",
        "source_cloud": "aws",
        "target_cloud": "azure",
    }


def _full_state() -> dict:
    """État complet représentant un checkpoint post-génération IaC."""
    return {
        "thread_id": "thread-resume-002",
        "migration_id": "mig-002",
        "current_step": "generate_iac",
        "source_cloud": "aws",
        "target_cloud": "azure",
        "repo_url": "https://github.com/org/my-app",
        "dependency_graph": {
            "nodes": [
                {"id": "aws_s3_bucket.main", "type": "aws_s3_bucket"},
                {"id": "aws_rds_instance.db", "type": "aws_rds_instance"},
            ],
            "edges": [{"from": "aws_rds_instance.db", "to": "aws_s3_bucket.main"}],
        },
        "source_services_inventory": [
            {"type": "aws_s3_bucket", "name": "main", "complexity": "LOW"},
            {"type": "aws_rds_instance", "name": "db", "complexity": "HIGH"},
        ],
        "migration_plan": {
            "resources": [
                {"source": "aws_s3_bucket", "target": "azurerm_storage_account", "strategy": "REHOST"},
                {"source": "aws_rds_instance", "target": "azurerm_postgresql_flexible_server", "strategy": "REPLATFORM"},
            ],
            "summary": {"estimated_total_monthly": 120.5},
        },
        "artifacts": {
            "terraform_dir": "/output/migrated_app",
            "export_zip_path": "/output/migrated_app.zip",
        },
        "iac_validation_success": True,
        "correction_counts": {"azurerm_storage_account": 1},
        "deployment_status": "validated",
        "user_accepted": None,
        "rejection_count": 0,
        "rejection_feedback": "",
        "monthly_budget_usd": 500.0,
        "timeline": "3 months",
        "data_residency_requirement": "EU",
        "compliance_standards": ["GDPR"],
        "high_availability_required": True,
        "network_isolation_required": False,
        "errors": [],
        "warnings": [],
    }


# ── Sérialisabilité JSON (condition nécessaire pour le checkpointing) ─────────

class TestMigrationStateJsonSerializable:
    """MigrationState est un dict Python → doit être sérialisable en JSON."""

    def test_minimal_state_is_json_serializable(self):
        state = _minimal_analyze_state()
        serialized = json.dumps(state)
        assert isinstance(serialized, str)
        assert len(serialized) > 0

    def test_full_state_is_json_serializable(self):
        state = _full_state()
        serialized = json.dumps(state)
        assert isinstance(serialized, str)

    def test_roundtrip_preserves_string_fields(self):
        state = _full_state()
        restored = json.loads(json.dumps(state))
        for key in ("thread_id", "migration_id", "source_cloud", "target_cloud", "repo_url"):
            assert restored[key] == state[key], f"Champ {key!r} perdu lors du roundtrip"

    def test_roundtrip_preserves_nested_dict(self):
        state = _full_state()
        restored = json.loads(json.dumps(state))
        assert restored["migration_plan"]["resources"][0]["strategy"] == "REHOST"

    def test_roundtrip_preserves_list_fields(self):
        state = _full_state()
        restored = json.loads(json.dumps(state))
        assert len(restored["source_services_inventory"]) == 2
        assert len(restored["compliance_standards"]) == 1

    def test_roundtrip_preserves_numeric_fields(self):
        state = _full_state()
        restored = json.loads(json.dumps(state))
        assert restored["monthly_budget_usd"] == pytest.approx(500.0)
        assert restored["rejection_count"] == 0

    def test_roundtrip_preserves_boolean_fields(self):
        state = _full_state()
        restored = json.loads(json.dumps(state))
        assert restored["iac_validation_success"] is True
        assert restored["high_availability_required"] is True
        assert restored["network_isolation_required"] is False

    def test_roundtrip_preserves_none_fields(self):
        state = _full_state()
        restored = json.loads(json.dumps(state))
        assert restored["user_accepted"] is None

    def test_empty_state_is_json_serializable(self):
        state: MigrationState = {}
        serialized = json.dumps(state)
        restored = json.loads(serialized)
        assert restored == {}


# ── Validation URL GitHub ─────────────────────────────────────────────────────

class TestValidateRepoUrl:

    def test_valid_github_url_accepted(self):
        ok, err = MigrationStateValidator.validate_repo_url(
            "https://github.com/org/my-app"
        )
        assert ok is True
        assert err is None

    def test_valid_github_url_with_depth_accepted(self):
        ok, err = MigrationStateValidator.validate_repo_url(
            "https://github.com/octocat/Hello-World"
        )
        assert ok is True

    def test_empty_url_rejected(self):
        ok, err = MigrationStateValidator.validate_repo_url("")
        assert ok is False
        assert "empty" in err.lower()

    def test_non_github_url_rejected(self):
        ok, err = MigrationStateValidator.validate_repo_url(
            "https://gitlab.com/org/repo"
        )
        assert ok is False
        assert "Invalid" in err

    def test_http_instead_of_https_rejected(self):
        ok, err = MigrationStateValidator.validate_repo_url(
            "http://github.com/org/repo"
        )
        assert ok is False

    def test_url_without_repo_rejected(self):
        ok, err = MigrationStateValidator.validate_repo_url(
            "https://github.com/org"
        )
        assert ok is False

    def test_none_url_rejected(self):
        ok, err = MigrationStateValidator.validate_repo_url(None)
        assert ok is False


# ── Validation provider cloud ─────────────────────────────────────────────────

class TestValidateCloud:

    @pytest.mark.parametrize("cloud", ["aws", "gcp", "azure", "multi"])
    def test_valid_clouds_accepted(self, cloud):
        ok, err = MigrationStateValidator.validate_cloud(cloud)
        assert ok is True
        assert err is None

    @pytest.mark.parametrize("cloud", ["AWS", "Azure", "GCP", "ibm", "oracle", ""])
    def test_invalid_clouds_rejected(self, cloud):
        ok, err = MigrationStateValidator.validate_cloud(cloud)
        assert ok is False
        assert err is not None

    def test_error_message_contains_valid_options(self):
        _, err = MigrationStateValidator.validate_cloud("ibm")
        assert "aws" in err.lower() or "azure" in err.lower() or "gcp" in err.lower()


# ── Validation par étape ──────────────────────────────────────────────────────

class TestValidateStage:

    def test_analyze_stage_valid_with_repo_url(self):
        state = _minimal_analyze_state()
        ok, errors = MigrationStateValidator.validate_stage(state, "analyze")
        assert ok is True
        assert errors == []

    def test_analyze_stage_invalid_without_repo_url(self):
        state: MigrationState = {}
        ok, errors = MigrationStateValidator.validate_stage(state, "analyze")
        assert ok is False
        assert any("repo_url" in e for e in errors)

    def test_plan_stage_valid(self):
        state = _minimal_analyze_state()
        ok, errors = MigrationStateValidator.validate_stage(state, "plan")
        assert ok is True

    def test_plan_stage_invalid_without_clouds(self):
        state: MigrationState = {"repo_url": "https://github.com/org/repo"}
        ok, errors = MigrationStateValidator.validate_stage(state, "plan")
        assert ok is False
        assert any("source_cloud" in e or "target_cloud" in e for e in errors)

    def test_generate_stage_valid(self):
        state = _full_state()
        ok, errors = MigrationStateValidator.validate_stage(state, "generate")
        assert ok is True

    def test_generate_stage_invalid_without_plan(self):
        state: MigrationState = {"target_cloud": "azure"}
        ok, errors = MigrationStateValidator.validate_stage(state, "generate")
        assert ok is False

    def test_generate_stage_invalid_with_empty_resources(self):
        state: MigrationState = {
            "target_cloud": "azure",
            "migration_plan": {"resources": []},
        }
        ok, errors = MigrationStateValidator.validate_stage(state, "generate")
        assert ok is False
        assert any("resources" in e for e in errors)

    def test_deploy_stage_valid(self):
        state: MigrationState = {"deployment_status": "validated"}
        ok, errors = MigrationStateValidator.validate_stage(state, "deploy")
        assert ok is True

    def test_unknown_stage_returns_no_errors(self):
        """Une étape inconnue ne génère pas d'erreur (pas de contraintes définies)."""
        state: MigrationState = {}
        ok, errors = MigrationStateValidator.validate_stage(state, "unknown_stage")
        assert ok is True
        assert errors == []


# ── StateValidationError ──────────────────────────────────────────────────────

class TestStateValidationError:

    def test_validate_and_raise_raises_on_invalid_state(self):
        state: MigrationState = {}
        with pytest.raises(StateValidationError) as exc:
            MigrationStateValidator.validate_and_raise(state, "analyze")
        assert "repo_url" in str(exc.value)

    def test_validate_and_raise_contains_stage_name(self):
        state: MigrationState = {}
        with pytest.raises(StateValidationError) as exc:
            MigrationStateValidator.validate_and_raise(state, "analyze")
        assert "analyze" in str(exc.value)

    def test_validate_and_raise_does_not_raise_on_valid_state(self):
        state = _minimal_analyze_state()
        # Ne doit pas lever d'exception
        MigrationStateValidator.validate_and_raise(state, "analyze")

    def test_get_validation_errors_returns_empty_list_on_valid(self):
        state = _minimal_analyze_state()
        errors = MigrationStateValidator.get_validation_errors(state, "analyze")
        assert errors == []

    def test_get_validation_errors_returns_list_on_invalid(self):
        state: MigrationState = {}
        errors = MigrationStateValidator.get_validation_errors(state, "analyze")
        assert isinstance(errors, list)
        assert len(errors) > 0

    def test_state_validation_error_is_value_error(self):
        """StateValidationError hérite de ValueError — attrapable génériquement."""
        with pytest.raises(ValueError):
            MigrationStateValidator.validate_and_raise({}, "analyze")


# ── Plan cloud invalid dans validate_stage ────────────────────────────────────

class TestValidateStageCloudCheck:

    def test_plan_stage_rejects_invalid_source_cloud(self):
        state: MigrationState = {
            "repo_url": "https://github.com/org/repo",
            "source_cloud": "ibm",
            "target_cloud": "azure",
        }
        ok, errors = MigrationStateValidator.validate_stage(state, "plan")
        assert ok is False
        assert any("ibm" in e or "Invalid cloud" in e for e in errors)

    def test_plan_stage_rejects_invalid_target_cloud(self):
        state: MigrationState = {
            "repo_url": "https://github.com/org/repo",
            "source_cloud": "aws",
            "target_cloud": "oracle",
        }
        ok, errors = MigrationStateValidator.validate_stage(state, "plan")
        assert ok is False

    def test_plan_stage_accepts_multi_as_source(self):
        """'multi' est un cloud source valide (infrastructure multi-cloud)."""
        state: MigrationState = {
            "repo_url": "https://github.com/org/repo",
            "source_cloud": "multi",
            "target_cloud": "azure",
        }
        ok, errors = MigrationStateValidator.validate_stage(state, "plan")
        assert ok is True
