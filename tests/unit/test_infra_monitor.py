"""
tests/unit/test_infra_monitor.py — Tests de surveillance infra post-déploiement (RF-14).

Couvre RF-14 (Monitoring de l'infrastructure déployée) :
  - get_deployed_resources() parse correctement terraform.tfstate
  - get_deployed_resources() retourne [] si tfstate absent ou invalide
  - check_resource_health() gère l'absence du SDK azure-mgmt-resource
  - _STATE_MAP convertit correctement les états ARM → health status
  - detect_drift() identifie les dérives de localisation et de kind
  - _ARM_API_VERSIONS couvre les types de ressources clés
  - _get_tfstate_path() construit le chemin correct

Aucun appel Azure n'est émis : tous les SDK Azure sont mockés.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

_VALID_TFSTATE = {
    "version": 4,
    "terraform_version": "1.7.0",
    "resources": [
        {
            "mode": "managed",
            "type": "azurerm_storage_account",
            "name": "main",
            "instances": [
                {
                    "attributes": {
                        "id": "/subscriptions/sub-1/resourceGroups/my-rg/providers/Microsoft.Storage/storageAccounts/mystorage",
                        "name": "mystorage",
                        "location": "westeurope",
                        "resource_group_name": "my-rg",
                        "account_replication_type": "LRS",
                        "kind": "StorageV2",
                    }
                }
            ],
        },
        {
            "mode": "managed",
            "type": "azurerm_postgresql_flexible_server",
            "name": "db",
            "instances": [
                {
                    "attributes": {
                        "id": "/subscriptions/sub-1/resourceGroups/my-rg/providers/Microsoft.DBforPostgreSQL/flexibleServers/mydb",
                        "name": "mydb",
                        "location": "westeurope",
                        "resource_group_name": "my-rg",
                        "version": "14",
                        "administrator_login": "psqladmin",
                        "sku_name": "Standard_D2s_v3",
                    }
                }
            ],
        },
        {
            "mode": "data",          # mode data → ignoré
            "type": "azurerm_resource_group",
            "name": "existing",
            "instances": [
                {"attributes": {"id": "/subscriptions/sub-1/resourceGroups/existing-rg", "name": "existing-rg"}}
            ],
        },
        {
            "mode": "managed",
            "type": "azurerm_virtual_network",
            "name": "vnet",
            "instances": [
                {
                    "attributes": {
                        "id": "",     # id vide → ignoré
                        "name": "myvnet",
                        "location": "westeurope",
                    }
                }
            ],
        },
    ],
}

_EMPTY_TFSTATE = {"version": 4, "terraform_version": "1.7.0", "resources": []}

_ARM_ENV = {
    "ARM_SUBSCRIPTION_ID": "sub-1",
    "ARM_TENANT_ID":        "tenant-1",
    "ARM_CLIENT_ID":        "client-1",
    "ARM_CLIENT_SECRET":    "secret-1",
}


def _write_tfstate(tmp_path: Path, content: dict, migration_id: str = "mig-001") -> Path:
    runs_dir = tmp_path / "runs" / migration_id
    runs_dir.mkdir(parents=True)
    tf_path = runs_dir / "terraform.tfstate"
    tf_path.write_text(json.dumps(content), encoding="utf-8")
    return tf_path


# ── get_deployed_resources ────────────────────────────────────────────────────

class TestGetDeployedResources:

    def test_returns_list_for_valid_tfstate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(tmp_path / "output" / "migrated_app"))
        _write_tfstate(tmp_path, _VALID_TFSTATE, "mig-001")

        with patch("services.infra_monitor._get_tfstate_path",
                   return_value=tmp_path / "runs" / "mig-001" / "terraform.tfstate"):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("mig-001")

        assert isinstance(resources, list)

    def test_managed_resources_included(self, tmp_path):
        _write_tfstate(tmp_path, _VALID_TFSTATE, "mig-test")

        with patch("services.infra_monitor._get_tfstate_path",
                   return_value=tmp_path / "runs" / "mig-test" / "terraform.tfstate"):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("mig-test")

        types = [r["type"] for r in resources]
        assert "azurerm_storage_account" in types
        assert "azurerm_postgresql_flexible_server" in types

    def test_data_mode_resources_excluded(self, tmp_path):
        """Les resources en mode 'data' ne doivent pas figurer dans les résultats."""
        _write_tfstate(tmp_path, _VALID_TFSTATE, "mig-data")

        with patch("services.infra_monitor._get_tfstate_path",
                   return_value=tmp_path / "runs" / "mig-data" / "terraform.tfstate"):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("mig-data")

        types = [r["type"] for r in resources]
        assert "azurerm_resource_group" not in types

    def test_resources_without_id_excluded(self, tmp_path):
        """Les instances sans attribut 'id' sont exclues."""
        _write_tfstate(tmp_path, _VALID_TFSTATE, "mig-noid")

        with patch("services.infra_monitor._get_tfstate_path",
                   return_value=tmp_path / "runs" / "mig-noid" / "terraform.tfstate"):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("mig-noid")

        names = [r["name"] for r in resources]
        assert "vnet" not in names

    def test_resource_fields_present(self, tmp_path):
        """Chaque resource a les champs attendus."""
        _write_tfstate(tmp_path, _VALID_TFSTATE, "mig-fields")

        with patch("services.infra_monitor._get_tfstate_path",
                   return_value=tmp_path / "runs" / "mig-fields" / "terraform.tfstate"):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("mig-fields")

        for r in resources:
            assert "type" in r
            assert "name" in r
            assert "id" in r
            assert "resource_group_name" in r
            assert "tfstate_attrs" in r

    def test_returns_empty_list_when_tfstate_missing(self, tmp_path):
        non_existent = tmp_path / "runs" / "no-such-migration" / "terraform.tfstate"
        with patch("services.infra_monitor._get_tfstate_path", return_value=non_existent):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("no-such-migration")
        assert resources == []

    def test_returns_empty_list_for_empty_tfstate(self, tmp_path):
        _write_tfstate(tmp_path, _EMPTY_TFSTATE, "mig-empty")

        with patch("services.infra_monitor._get_tfstate_path",
                   return_value=tmp_path / "runs" / "mig-empty" / "terraform.tfstate"):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("mig-empty")
        assert resources == []

    def test_returns_empty_list_on_invalid_json(self, tmp_path):
        runs_dir = tmp_path / "runs" / "mig-bad"
        runs_dir.mkdir(parents=True)
        bad_path = runs_dir / "terraform.tfstate"
        bad_path.write_text("this is not valid json {{{{", encoding="utf-8")

        with patch("services.infra_monitor._get_tfstate_path", return_value=bad_path):
            from services.infra_monitor import get_deployed_resources
            resources = get_deployed_resources("mig-bad")
        assert resources == []


# ── _STATE_MAP cohérence ──────────────────────────────────────────────────────

class TestStateMap:
    """Le mapping état ARM → health status doit être exhaustif et correct."""

    def test_succeeded_maps_to_healthy(self):
        from services.infra_monitor import _STATE_MAP
        assert _STATE_MAP["Succeeded"] == "healthy"

    def test_failed_maps_to_error(self):
        from services.infra_monitor import _STATE_MAP
        assert _STATE_MAP["Failed"] == "error"

    def test_creating_maps_to_creating(self):
        from services.infra_monitor import _STATE_MAP
        assert _STATE_MAP["Creating"] == "creating"

    def test_updating_maps_to_updating(self):
        from services.infra_monitor import _STATE_MAP
        assert _STATE_MAP["Updating"] == "updating"

    def test_not_found_maps_to_not_found(self):
        from services.infra_monitor import _STATE_MAP
        assert _STATE_MAP["NotFound"] == "not_found"

    def test_all_values_are_valid_status_strings(self):
        from services.infra_monitor import _STATE_MAP
        valid_statuses = {"healthy", "updating", "creating", "deleting", "error", "not_found", "unknown"}
        for arm_state, health_status in _STATE_MAP.items():
            assert health_status in valid_statuses, (
                f"ARM state {arm_state!r} → {health_status!r} n'est pas un statut valide"
            )

    def test_running_and_available_also_healthy(self):
        from services.infra_monitor import _STATE_MAP
        assert _STATE_MAP["Running"] == "healthy"
        assert _STATE_MAP["Available"] == "healthy"


# ── check_resource_health — Azure SDK absent ──────────────────────────────────

class TestCheckResourceHealthNoSDK:
    """Quand azure-mgmt-resource n'est pas installé, la fonction retourne 'unknown' pour chaque ressource."""

    def test_returns_list_of_same_length(self):
        resources = [
            {"type": "azurerm_storage_account", "name": "stor", "id": "/subscriptions/x/..."},
        ]
        with patch.dict("sys.modules", {"azure.mgmt.resource": None}):
            from importlib import import_module, reload
            import services.infra_monitor as im
            # Forcer l'ImportError
            original = im.check_resource_health
            # Appel direct avec ARM env — vérifie la robustesse
            result = im.check_resource_health(resources, {})
        assert isinstance(result, list)
        assert len(result) == len(resources)

    def test_health_status_unknown_when_sdk_absent(self):
        resources = [
            {"type": "azurerm_storage_account", "name": "stor", "id": "/subs/x/RG/stor"},
        ]
        # Simuler ImportError sur azure.mgmt.resource
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "azure.mgmt.resource":
                raise ImportError("azure-mgmt-resource not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from services.infra_monitor import check_resource_health
            result = check_resource_health(resources, _ARM_ENV)

        assert all(r.get("health_status") in ("unknown", "error") for r in result)


# ── detect_drift ──────────────────────────────────────────────────────────────

class TestDetectDrift:

    def _make_resource(self, rid: str, rtype: str, location: str = "westeurope",
                       kind: str = "") -> dict:
        return {
            "id":           rid,
            "type":         rtype,
            "name":         "main",
            "display_name": "main",
            "tfstate_attrs": {
                "location": location,
                "kind":     kind,
            },
        }

    def _make_health(self, rid: str, location: str = "westeurope",
                     kind: str = "", status: str = "healthy") -> dict:
        return {
            "id":                 rid,
            "health_status":      status,
            "provisioning_state": "Succeeded",
            "arm_properties": {
                "location": location,
                "kind":     kind,
            },
        }

    def test_no_drift_when_state_matches(self):
        from services.infra_monitor import detect_drift
        rid = "/subs/x/RG/main/stor"
        resources     = [self._make_resource(rid, "azurerm_storage_account", "westeurope")]
        health_results = [self._make_health(rid, "westeurope")]
        drifts = detect_drift(resources, health_results)
        assert drifts == []

    def test_location_drift_detected(self):
        from services.infra_monitor import detect_drift
        rid = "/subs/x/RG/main/stor"
        resources     = [self._make_resource(rid, "azurerm_storage_account", "westeurope")]
        health_results = [self._make_health(rid, "northeurope")]   # changed location
        drifts = detect_drift(resources, health_results)
        assert len(drifts) == 1
        drift_attrs = [d["attribute"] for d in drifts[0]["drifts"]]
        assert "location" in drift_attrs

    def test_kind_drift_detected(self):
        from services.infra_monitor import detect_drift
        rid = "/subs/x/RG/main/stor"
        resources     = [self._make_resource(rid, "azurerm_storage_account", "westeurope", kind="StorageV2")]
        health_results = [self._make_health(rid, "westeurope", kind="BlobStorage")]
        drifts = detect_drift(resources, health_results)
        assert len(drifts) == 1
        drift_attrs = [d["attribute"] for d in drifts[0]["drifts"]]
        assert "kind" in drift_attrs

    def test_drift_record_has_expected_fields(self):
        from services.infra_monitor import detect_drift
        rid = "/subs/x/RG/main/stor"
        resources     = [self._make_resource(rid, "azurerm_storage_account", "westeurope")]
        health_results = [self._make_health(rid, "northeurope")]
        drifts = detect_drift(resources, health_results)
        record = drifts[0]
        assert "resource_id" in record
        assert "resource_type" in record
        assert "resource_name" in record
        assert "drifts" in record

    def test_location_drift_has_severity_high(self):
        from services.infra_monitor import detect_drift
        rid = "/subs/x/RG/main/stor"
        resources     = [self._make_resource(rid, "azurerm_storage_account", "westeurope")]
        health_results = [self._make_health(rid, "eastus")]
        drifts = detect_drift(resources, health_results)
        location_drift = next(d for d in drifts[0]["drifts"] if d["attribute"] == "location")
        assert location_drift["severity"] == "high"

    def test_error_health_status_skipped(self):
        """Les ressources en état 'error' ou 'not_found' sont ignorées dans la détection de drift."""
        from services.infra_monitor import detect_drift
        rid = "/subs/x/RG/main/stor"
        resources      = [self._make_resource(rid, "azurerm_storage_account", "westeurope")]
        health_results = [self._make_health(rid, "northeurope", status="error")]
        drifts = detect_drift(resources, health_results)
        assert drifts == []

    def test_empty_resources_returns_empty_list(self):
        from services.infra_monitor import detect_drift
        assert detect_drift([], []) == []


# ── _ARM_API_VERSIONS ─────────────────────────────────────────────────────────

class TestArmApiVersions:
    """Les types de ressources Terraform clés ont des versions ARM définies."""

    @pytest.mark.parametrize("rtype", [
        "azurerm_storage_account",
        "azurerm_postgresql_flexible_server",
        "azurerm_kubernetes_cluster",
        "azurerm_cognitive_account",
        "azurerm_key_vault",
        "azurerm_container_registry",
    ])
    def test_key_resource_type_has_api_version(self, rtype):
        from services.infra_monitor import _ARM_API_VERSIONS
        assert rtype in _ARM_API_VERSIONS, f"Manque version ARM pour {rtype!r}"

    def test_api_versions_are_date_format(self):
        """Toutes les versions suivent le format YYYY-MM-DD ou YYYY-MM-DD-preview."""
        import re
        from services.infra_monitor import _ARM_API_VERSIONS
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}(-preview)?$")
        for rtype, ver in _ARM_API_VERSIONS.items():
            assert pattern.match(ver), (
                f"Version ARM invalide pour {rtype!r}: {ver!r} — format attendu: YYYY-MM-DD[-preview]"
            )

    def test_default_api_version_is_defined(self):
        from services.infra_monitor import _DEFAULT_API_VERSION
        assert len(_DEFAULT_API_VERSION) > 0

    def test_default_version_is_date_format(self):
        import re
        from services.infra_monitor import _DEFAULT_API_VERSION
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}(-preview)?$")
        assert pattern.match(_DEFAULT_API_VERSION)


# ── _get_tfstate_path ─────────────────────────────────────────────────────────

class TestGetTfstatePath:

    def test_returns_path_object(self):
        from services.infra_monitor import _get_tfstate_path
        p = _get_tfstate_path("mig-abc")
        assert isinstance(p, Path)

    def test_path_ends_with_terraform_tfstate(self):
        from services.infra_monitor import _get_tfstate_path
        p = _get_tfstate_path("mig-abc")
        assert p.name == "terraform.tfstate"

    def test_path_contains_migration_id(self):
        from services.infra_monitor import _get_tfstate_path
        p = _get_tfstate_path("my-migration-007")
        assert "my-migration-007" in str(p)

    def test_path_uses_env_var_when_set(self, monkeypatch, tmp_path):
        output_dir = tmp_path / "output" / "migrated_app"
        monkeypatch.setenv("MIGRATION_OUTPUT_DIR", str(output_dir))
        from importlib import reload
        import services.infra_monitor as im
        reload(im)
        p = im._get_tfstate_path("mig-env")
        assert "mig-env" in str(p)
        assert "terraform.tfstate" == p.name
