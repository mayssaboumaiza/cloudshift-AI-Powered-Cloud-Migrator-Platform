"""
tests/unit/test_stack_analyzer_no_network.py — Garantit l'absence d'appels réseau
durant la phase d'analyse (RNF-6 Confidentialité).

Le stack_analyzer est 100% déterministe et local : il ne doit jamais contacter
un service externe (LLM, API cloud, CDN) pendant l'analyse syntaxique.
Seul PyGithub est autorisé (fetch du dépôt source) — et il est mocké ici
pour que les tests restent hors réseau.

Modules sous test :
  - services/stack_analyzer/iac_parsers.py  (parsing HCL2, CloudFormation, ARM)
  - services/stack_analyzer/service_classifier.py
  - services/stack_analyzer/graph_builder.py
  - services/stack_analyzer/github_tools.py (_pygithub_fetch_file)
"""
import pytest
from unittest.mock import patch, MagicMock, call

# ── Helpers locaux ────────────────────────────────────────────────────────────

_TF_MINIMAL = '''
resource "azurerm_storage_account" "main" {
  name                     = "mystorageaccount"
  resource_group_name      = "my-rg"
  location                 = "westeurope"
  account_tier             = "Standard"
  account_replication_type = "LRS"
}
'''

_CFN_MINIMAL = """
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  MyBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: my-bucket
"""

_ARM_MINIMAL = """{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
  "contentVersion": "1.0.0.0",
  "resources": [
    {
      "type": "Microsoft.Storage/storageAccounts",
      "apiVersion": "2021-04-01",
      "name": "mystorageacc",
      "location": "westeurope",
      "sku": { "name": "Standard_LRS" },
      "kind": "StorageV2"
    }
  ]
}"""


# ── RNF-6 : parsers locaux — aucun appel HTTP ─────────────────────────────────

class TestIacParsersNoNetwork:
    """Les fonctions de parsing n'émettent aucun appel HTTP/HTTPS."""

    def test_parse_tf_hcl2_no_http(self):
        """_parse_tf_hcl2 parse du contenu HCL sans appel réseau."""
        import httpx, requests, urllib.request

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")) as m_hx, \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")) as m_rq:

            from services.stack_analyzer.iac_parsers import _parse_tf_hcl2
            resources, _refs = _parse_tf_hcl2(_TF_MINIMAL, "main.tf")

            m_hx.assert_not_called()
            m_rq.assert_not_called()
            assert len(resources) >= 1
            # "service" contient le nom normalisé ; "file" contient le chemin passé
            assert isinstance(resources[0]["service"], str)
            assert resources[0]["file"] == "main.tf"

    def test_parse_cf_yaml_no_http(self):
        """_parse_cf_yaml parse du CloudFormation YAML sans appel réseau."""
        import httpx, requests

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.iac_parsers import _parse_cf_yaml
            # _parse_cf_yaml requiert (content, fpath)
            resources = _parse_cf_yaml(_CFN_MINIMAL, "template.yaml")
            assert len(resources) >= 1

    def test_parse_arm_template_no_http(self):
        """_parse_arm_template parse du JSON ARM sans appel réseau."""
        import httpx, requests

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.iac_parsers import _parse_arm_template
            # _parse_arm_template requiert (content, fpath)
            resources = _parse_arm_template(_ARM_MINIMAL, "azuredeploy.json")
            assert len(resources) >= 1


# ── RNF-6 : service_classifier — aucun appel réseau ──────────────────────────

class TestServiceClassifierNoNetwork:

    def test_get_service_type_no_http(self):
        """_get_service_type résout le type d'un service sans appel réseau."""
        import httpx, requests

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.service_classifier import _get_service_type
            result = _get_service_type("azurerm_storage_account")
            assert isinstance(result, str)
            assert len(result) > 0

    def test_normalize_service_name_no_http(self):
        """_normalize_service_name ne fait aucun appel réseau."""
        import httpx, requests

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.service_classifier import _normalize_service_name
            result = _normalize_service_name("aws_s3_bucket")
            assert isinstance(result, str)

    def test_get_complexity_no_http(self):
        """_get_complexity est une fonction pure, aucun appel réseau."""
        import httpx, requests

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.service_classifier import _get_complexity
            # Seuils réels : score >= 6 → HIGH, >= 3 → MEDIUM, sinon LOW
            assert _get_complexity(1) == "LOW"
            assert _get_complexity(4) == "MEDIUM"
            assert _get_complexity(8) == "HIGH"


# ── RNF-6 : graph_builder — aucun appel réseau ───────────────────────────────

class TestGraphBuilderNoNetwork:

    def test_build_services_inventory_no_http(self):
        """_build_services_inventory construit l'inventaire sans appel réseau."""
        import httpx, requests

        dependency_graph = {
            "nodes": [
                {"id": "azurerm_storage_account.main", "type": "azurerm_storage_account"},
                {"id": "azurerm_postgresql_flexible_server.db", "type": "azurerm_postgresql_flexible_server"},
            ],
            "edges": [],
        }

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.graph_builder import _build_services_inventory
            inventory = _build_services_inventory(dependency_graph)
            assert isinstance(inventory, list)

    def test_empty_graph_no_http(self):
        """_empty_graph retourne un template sans appel réseau."""
        import httpx, requests

        with patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.graph_builder import _empty_graph
            result = _empty_graph("test error")
            assert "nodes" in result or isinstance(result, dict)


# ── RNF-6 : github_tools — PyGithub est le SEUL accès réseau autorisé ────────

class TestGithubToolsNetworkIsolation:
    """_pygithub_fetch_file utilise PyGithub (réseau autorisé).
    On vérifie que lorsque PyGithub est absent (_HAS_PYGITHUB=False),
    la fonction retourne '' sans tenter d'autres appels réseau."""

    def test_fetch_file_returns_empty_when_pygithub_unavailable(self):
        """Sans PyGithub, _pygithub_fetch_file retourne '' immédiatement."""
        import httpx, requests

        with patch("services.stack_analyzer.github_tools._HAS_PYGITHUB", False), \
             patch.object(httpx, "get", side_effect=AssertionError("HTTP GET interdit")), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get interdit")):

            from services.stack_analyzer.github_tools import _pygithub_fetch_file
            result = _pygithub_fetch_file("owner", "repo", "main.tf", "main", "token")
            assert result == ""

    def test_fetch_file_uses_pygithub_not_requests(self):
        """_pygithub_fetch_file utilise uniquement PyGithub, pas requests."""
        import requests

        mock_github = MagicMock()
        mock_repo = MagicMock()
        mock_content = MagicMock()
        mock_content.decoded_content = b'resource "aws_s3_bucket" "b" {}'
        mock_repo.get_contents.return_value = mock_content
        mock_github.return_value.get_repo.return_value = mock_repo

        with patch("services.stack_analyzer.github_tools._HAS_PYGITHUB", True), \
             patch("services.stack_analyzer.github_tools._Github", mock_github), \
             patch.object(requests, "get", side_effect=AssertionError("requests.get ne doit pas être appelé")):

            from services.stack_analyzer import github_tools
            # Recharger pour prendre en compte le patch
            github_tools._HAS_PYGITHUB = True
            github_tools._Github = mock_github

            result = github_tools._pygithub_fetch_file("owner", "repo", "main.tf", "main", "gh-token")
            # requests.get n'a pas été appelé (sinon AssertionError levée)
            assert isinstance(result, str)


# ── RNF-6 : iac_parsers — pas d'appels LLM ───────────────────────────────────

class TestIacParsersNoLLM:
    """Les parsers IaC n'invoquent jamais un LLM Azure OpenAI."""

    def test_parse_tf_hcl2_does_not_call_azure_openai(self):
        """Le parsing HCL2 n'utilise aucun LLM."""
        with patch("openai.AzureOpenAI", side_effect=AssertionError("LLM interdit")) as m_llm:
            from services.stack_analyzer.iac_parsers import _parse_tf_hcl2
            _parse_tf_hcl2(_TF_MINIMAL, "main.tf")
            m_llm.assert_not_called()

    def test_parse_cf_yaml_does_not_call_azure_openai(self):
        with patch("openai.AzureOpenAI", side_effect=AssertionError("LLM interdit")) as m_llm:
            from services.stack_analyzer.iac_parsers import _parse_cf_yaml
            _parse_cf_yaml(_CFN_MINIMAL, "template.yaml")
            m_llm.assert_not_called()


# ── Cohérence : résultats de parsing sont déterministes ──────────────────────

class TestParsingDeterminism:
    """Le même input produit toujours le même output (zéro stochastique)."""

    def test_tf_parsing_is_deterministic(self):
        from services.stack_analyzer.iac_parsers import _parse_tf_hcl2
        r1, _ = _parse_tf_hcl2(_TF_MINIMAL, "main.tf")
        r2, _ = _parse_tf_hcl2(_TF_MINIMAL, "main.tf")
        assert r1 == r2

    def test_cf_parsing_is_deterministic(self):
        from services.stack_analyzer.iac_parsers import _parse_cf_yaml
        r1 = _parse_cf_yaml(_CFN_MINIMAL, "template.yaml")
        r2 = _parse_cf_yaml(_CFN_MINIMAL, "template.yaml")
        assert r1 == r2

    def test_service_type_resolution_is_deterministic(self):
        from services.stack_analyzer.service_classifier import _get_service_type
        t1 = _get_service_type("aws_rds_instance")
        t2 = _get_service_type("aws_rds_instance")
        assert t1 == t2
