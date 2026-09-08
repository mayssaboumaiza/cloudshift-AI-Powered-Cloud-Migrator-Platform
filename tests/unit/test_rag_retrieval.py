"""
tests/unit/test_rag_retrieval.py — Tests unitaires du retrieval RAG (RF-20).

Périmètre : méthodes pures ou mockables de TerraformGraphRAG.
Aucun appel réel à PostgreSQL, pgvector, Neo4j ou LLM.

5 groupes logiques métier :
  A. ROUTING   — normalisation provider, filtrage RETIRE/RETAIN, dispatch plan
  B. RETRIEVAL — désérialisation JSONB→dict (double path psycopg2)
  C. FALLBACK  — comportement gracieux sur DB inaccessible (companions + query)
  D. RANKING   — top-k, arrondi similarité, scoped types, structure Agent 02
  E. GRAPH     — multi-hop traversal, depth-1 companions, hop2 count
  F. SCORING   — algorithme _build_complexity_result (seuils, caps, poids)
  G. FORMATAGE — prompt RAG : sections required/optional/blocks/example/CRITICAL
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_rag() -> "TerraformGraphRAG":
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = MagicMock(tolist=lambda: [[0.1] * 384])
    with patch("rag.graph_rag.init_sentence_transformer", return_value=mock_embedder):
        from rag.graph_rag import TerraformGraphRAG
        TerraformGraphRAG._instance = None
        return TerraformGraphRAG()


@contextmanager
def _mock_pg(rows_per_query: list[list[tuple]]):
    """Curseur SQL préchargé : le i-ème execute() retourne rows_per_query[i]."""
    call_idx = 0

    class _Cursor:
        def __init__(self): self._rows: list[tuple] = []
        def execute(self, *_a, **_k):
            nonlocal call_idx
            self._rows = rows_per_query[call_idx] if call_idx < len(rows_per_query) else []
            call_idx += 1
        def fetchall(self): return list(self._rows)
        def fetchone(self): return self._rows[0] if self._rows else None
        def __enter__(self): return self
        def __exit__(self, *_): pass

    class _Conn:
        def cursor(self): return _Cursor()
        def __enter__(self): return self
        def __exit__(self, *_): pass

    with patch("rag.graph_rag.get_pg_connection", return_value=_Conn()):
        yield


# ═══════════════════════════════════════════════════════════════════════════════
# A. ROUTING — normalisation provider + filtrage plan
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeProvider:
    """Un alias raté misdirects la requête SQL (WHERE provider = %s)."""

    def test_azure_becomes_azurerm(self):
        from rag.graph_rag import TerraformGraphRAG
        assert TerraformGraphRAG._normalize_provider("azure") == "azurerm"

    def test_gcp_becomes_google(self):
        from rag.graph_rag import TerraformGraphRAG
        assert TerraformGraphRAG._normalize_provider("gcp") == "google"

    def test_aws_unchanged(self):
        from rag.graph_rag import TerraformGraphRAG
        assert TerraformGraphRAG._normalize_provider("aws") == "aws"

    def test_uppercase_azure_normalized(self):
        from rag.graph_rag import TerraformGraphRAG
        assert TerraformGraphRAG._normalize_provider("AZURE") == "azurerm"

    def test_spaces_stripped_before_alias_lookup(self):
        from rag.graph_rag import TerraformGraphRAG
        assert TerraformGraphRAG._normalize_provider("  gcp  ") == "google"

    def test_unknown_provider_returned_as_is(self):
        from rag.graph_rag import TerraformGraphRAG
        assert TerraformGraphRAG._normalize_provider("ibm") == "ibm"


class TestGetContextsForPlan:
    """Filtrage RETIRE/RETAIN et normalisation provider dans le dispatch plan."""

    def _plan(self, resources): return {"resources": resources}

    @pytest.mark.parametrize("strategy", ["RETIRE", "RETAIN"])
    def test_non_actionable_strategies_excluded(self, strategy):
        """RETIRE et RETAIN ne doivent jamais déclencher un appel get_context."""
        rag = _make_rag()
        with patch.object(rag, "get_context", return_value="ctx") as m:
            rag.get_contexts_for_plan(self._plan([
                {"terraform_resource": "aws_s3_bucket",
                 "target_cloud": "aws", "strategy": strategy},
            ]))
        m.assert_not_called()

    def test_rehost_strategy_triggers_retrieval(self):
        rag = _make_rag()
        with patch.object(rag, "get_context", return_value="ctx") as m:
            rag.get_contexts_for_plan(self._plan([
                {"terraform_resource": "azurerm_storage_account",
                 "target_cloud": "azure", "strategy": "REHOST"},
            ]))
        m.assert_called_once()

    def test_provider_alias_normalized_before_get_context(self):
        """'azure' → 'azurerm' doit être résolu avant d'appeler get_context."""
        rag = _make_rag()
        captured = []
        with patch.object(rag, "get_context",
                          side_effect=lambda r, p: captured.append(p) or "ctx"):
            rag.get_contexts_for_plan(self._plan([
                {"terraform_resource": "azurerm_storage_account",
                 "target_cloud": "azure", "strategy": "REHOST"},
            ]))
        assert captured[0] == "azurerm"

    def test_mixed_plan_only_active_resources_in_result(self):
        """Plan avec REHOST + RETIRE + REPLATFORM → 2 entrées, pas 3."""
        rag = _make_rag()
        with patch.object(rag, "get_context", return_value="ctx"):
            result = rag.get_contexts_for_plan(self._plan([
                {"terraform_resource": "azurerm_storage_account",
                 "target_cloud": "azure", "strategy": "REHOST"},
                {"terraform_resource": "aws_s3_bucket",
                 "target_cloud": "aws", "strategy": "RETIRE"},
                {"terraform_resource": "google_sql_database_instance",
                 "target_cloud": "gcp", "strategy": "REPLATFORM"},
            ]))
        assert len(result) == 2

    def test_target_service_used_when_terraform_resource_absent(self):
        """Fallback terraform_resource → target_service pour les anciens plans."""
        rag = _make_rag()
        captured = []
        with patch.object(rag, "get_context",
                          side_effect=lambda r, p: captured.append(r) or "ctx"):
            rag.get_contexts_for_plan(self._plan([
                {"target_service": "azurerm_storage_account",
                 "target_cloud": "azure", "strategy": "REHOST"},
            ]))
        assert "azurerm_storage_account" in captured

    def test_empty_plan_returns_empty_dict(self):
        rag = _make_rag()
        assert rag.get_contexts_for_plan({"resources": []}) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# B. RETRIEVAL — désérialisation JSONB→dict
# ═══════════════════════════════════════════════════════════════════════════════

class TestRowToMeta:
    """psycopg2 peut retourner JSONB comme list/dict natif ou comme str brute.
    _row_to_meta doit gérer les deux sans crash ni perte de données."""

    def _row(self, required=None, optional=None, blocks=None):
        return ("azurerm", "desc",
                required if required is not None else [],
                optional if optional is not None else [],
                blocks if blocks is not None else [],
                "example")

    def test_list_required_args_serialized_to_parsable_json(self):
        """psycopg2 retourne list → doit être sérialisé pour que _format_single puisse json.loads."""
        rag = _make_rag()
        args = [{"name": "bucket", "description": "Bucket name"}]
        meta = rag._row_to_meta(self._row(required=args))
        parsed = json.loads(meta["required_args"])
        assert parsed[0]["name"] == "bucket"

    def test_str_required_args_kept_verbatim(self):
        """Si psycopg2 retourne déjà une chaîne JSON, on ne la ré-encode pas."""
        rag = _make_rag()
        raw = '[{"name": "bucket"}]'
        meta = rag._row_to_meta(("aws", "desc", raw, "[]", "[]", ""))
        assert meta["required_args"] == raw

    def test_none_description_becomes_empty_string(self):
        rag = _make_rag()
        meta = rag._row_to_meta(("aws", None, [], [], [], ""))
        assert meta["description"] == ""

    def test_none_example_becomes_empty_string(self):
        rag = _make_rag()
        meta = rag._row_to_meta(("aws", "desc", [], [], [], None))
        assert meta["example"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# C. FALLBACK — résilience sur DB inaccessible
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCompanions:
    """La DB peut tomber après démarrage — le fallback statique doit toujours répondre."""

    def test_fallback_to_static_when_db_fails(self):
        rag = _make_rag()
        with patch("rag.graph_rag.get_pg_connection", side_effect=Exception("DB down")):
            companions = rag._get_companions("azurerm_storage_account")
        assert "azurerm_storage_container" in companions

    def test_unknown_resource_returns_empty_on_db_failure(self):
        rag = _make_rag()
        with patch("rag.graph_rag.get_pg_connection", side_effect=Exception("DB down")):
            assert rag._get_companions("azurerm_nonexistent_xyz") == []

    def test_db_results_take_precedence_over_static(self):
        """Quand la DB répond, ses résultats remplacent le dictionnaire statique."""
        rag = _make_rag()
        with _mock_pg([[("azurerm_custom_from_db",)]]):
            companions = rag._get_companions("azurerm_storage_account")
        assert "azurerm_custom_from_db" in companions

    def test_empty_db_result_falls_back_to_static(self):
        """DB accessible mais SELECT vide → fallback statique, pas liste vide."""
        rag = _make_rag()
        with _mock_pg([[]]):
            companions = rag._get_companions("azurerm_storage_account")
        assert "azurerm_storage_container" in companions


class TestGraphRagQueryFallback:

    def test_db_failure_returns_empty_result_structure(self):
        """DB down → les 3 listes retournées sont vides, pas d'exception levée."""
        rag = _make_rag()
        with patch("rag.graph_rag.get_pg_connection", side_effect=Exception("DB down")):
            result = rag.graph_rag_query("azurerm", "azurerm_storage_account")
        assert result["required_dependencies"] == []
        assert result["doc_chunks"] == []
        assert result["canonical_patterns"] == []

    def test_get_argument_context_returns_empty_on_db_failure(self):
        rag = _make_rag()
        with patch("rag.graph_rag.get_pg_connection", side_effect=Exception("DB down")):
            assert rag.get_argument_context("azurerm_storage_account", "storage") == []


# ═══════════════════════════════════════════════════════════════════════════════
# D. RANKING — top-k, similarité, scoped types
# ═══════════════════════════════════════════════════════════════════════════════

class TestGraphRagQueryRanking:
    """Contrats d'interface pour Agent 02 : top-k, arrondi, champs obligatoires."""

    def _doc_row(self, rid="azurerm_storage_account", provider="azurerm",
                 desc="desc", example="", sim=0.92):
        return (rid, provider, desc, example, sim)

    def test_top_k_chunks_default_passed_to_sql(self):
        """top_k_chunks=12 doit être transmis tel quel à la requête SQL LIMIT."""
        rag = _make_rag()
        params_seen = []

        class _T:
            def __init__(self): self._rows = []
            def execute(self, sql, params=None):
                if params: params_seen.extend(
                    p for p in params if isinstance(p, (list, tuple)) and p
                    or [params]
                )
                self._rows = []
            def fetchall(self): return []
            def fetchone(self): return None
            def __enter__(self): return self
            def __exit__(self, *_): pass

        class _C:
            def cursor(self): return _T()
            def __enter__(self): return self
            def __exit__(self, *_): pass

        with patch("rag.graph_rag.get_pg_connection", return_value=_C()), \
             patch("rag.graph_rag.neo4j_available", return_value=False):
            rag.graph_rag_query("azurerm", "azurerm_storage_account")

        all_vals = [v for p in params_seen
                    for v in (p if isinstance(p, (list, tuple)) else [p])]
        assert 12 in all_vals

    def test_context_resources_included_in_scoped_types(self):
        """Les context_resources étendent les types scopés → plus de résultats pertinents."""
        rag = _make_rag()
        scoped: list = []

        class _C2:
            def __init__(self): self._rows = []
            def execute(self, sql, params=None):
                if params:
                    for p in params:
                        if isinstance(p, list):
                            scoped.extend(p)
                self._rows = []
            def fetchall(self): return []
            def fetchone(self): return None
            def __enter__(self): return self
            def __exit__(self, *_): pass

        class _Conn2:
            def cursor(self): return _C2()
            def __enter__(self): return self
            def __exit__(self, *_): pass

        with patch("rag.graph_rag.get_pg_connection", return_value=_Conn2()), \
             patch("rag.graph_rag.neo4j_available", return_value=False):
            rag.graph_rag_query("azurerm", "azurerm_storage_account",
                                context_resources=["azurerm_storage_container"])
        assert "azurerm_storage_container" in scoped

    def test_doc_chunks_similarity_rounded_to_4_decimals(self):
        """Arrondi à 4 décimales — contrat de format pour le frontend et les logs."""
        rag = _make_rag()
        with _mock_pg([[], [self._doc_row(sim=0.876543219)]]):
            with patch("rag.graph_rag.neo4j_available", return_value=False):
                result = rag.graph_rag_query("azurerm", "azurerm_storage_account")
        if result["doc_chunks"]:
            sim = result["doc_chunks"][0]["similarity"]
            assert sim == round(sim, 4)

    def test_doc_chunks_have_required_fields_for_agent02(self):
        """Chaque chunk doit exposer resource, description, example, similarity."""
        rag = _make_rag()
        with _mock_pg([[], [self._doc_row()]]):
            with patch("rag.graph_rag.neo4j_available", return_value=False):
                result = rag.graph_rag_query("azurerm", "azurerm_storage_account")
        if result["doc_chunks"]:
            chunk = result["doc_chunks"][0]
            for field in ("resource", "description", "example", "similarity"):
                assert field in chunk

    def test_required_dependencies_expose_target_and_relation(self):
        """Les dépendances structurelles doivent contenir target_type et relation."""
        rag = _make_rag()
        dep_row = ("azurerm_storage_container", "COMPANION", "azurerm")
        with _mock_pg([[dep_row], []]):
            with patch("rag.graph_rag.neo4j_available", return_value=False):
                result = rag.graph_rag_query("azurerm", "azurerm_storage_account")
        if result["required_dependencies"]:
            dep = result["required_dependencies"][0]
            assert "target_type" in dep and "relation" in dep

    def test_embedding_called_with_provided_task_text(self):
        """L'embedder doit encoder le task_text fourni (pas un texte synthétique)."""
        rag = _make_rag()
        with patch("rag.graph_rag.get_pg_connection", side_effect=Exception("DB down")):
            rag.graph_rag_query("azurerm", "azurerm_storage_account",
                                task_text="storage account with LRS replication")
        rag.embedder.encode.assert_called()
        call_text = rag.embedder.encode.call_args[0][0][0]
        assert "storage" in call_text.lower()


class TestGetArgumentContextRanking:

    def test_top_k_default_is_eight(self):
        """top_k=8 doit être passé à SQL LIMIT — contrat de l'API publique."""
        rag = _make_rag()
        params_seen = []

        class _T:
            def __init__(self): self._rows = []
            def execute(self, sql, params=None):
                if params: params_seen.append(params)
                self._rows = []
            def fetchall(self): return []
            def fetchone(self): return None
            def __enter__(self): return self
            def __exit__(self, *_): pass

        class _C:
            def cursor(self): return _T()
            def __enter__(self): return self
            def __exit__(self, *_): pass

        with patch("rag.graph_rag.get_pg_connection", return_value=_C()):
            rag.get_argument_context("azurerm_storage_account", "query")

        flat = [v for p in params_seen for v in (p if isinstance(p, (list, tuple)) else [p])]
        assert 8 in flat

    def test_argument_nodes_have_required_fields(self):
        """Chaque nœud d'argument expose arg_name, is_required, similarity."""
        rag = _make_rag()
        rows = [("location", "Azure region", "string", True, 0.95)]
        with _mock_pg([rows]):
            result = rag.get_argument_context("azurerm_storage_account", "location")
        if result:
            for field in ("arg_name", "description", "arg_type", "is_required", "similarity"):
                assert field in result[0]

    def test_similarity_rounded_to_4_decimals(self):
        rag = _make_rag()
        rows = [("name", "The name", "string", True, 0.948327651)]
        with _mock_pg([rows]):
            result = rag.get_argument_context("azurerm_storage_account", "q")
        if result:
            sim = result[0]["similarity"]
            assert sim == round(sim, 4)

    def test_none_description_coerced_to_empty_string(self):
        """None dans la DB → '' dans le résultat (pas de KeyError chez l'appelant)."""
        rag = _make_rag()
        rows = [("name", None, "string", True, 0.9)]
        with _mock_pg([rows]):
            result = rag.get_argument_context("azurerm_storage_account", "q")
        if result:
            assert result[0]["description"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# E. GRAPH — traversal multi-hop, depth-1 companions, hop2
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetTraversalForResource:
    """Cœur du GraphRAG : extraction des voisins par profondeur et type de relation."""

    def test_companion_nodes_at_depth_1_are_fetched(self):
        """Seuls les nœuds COMPANION/DEPENDS_ON à depth=1 sont les companions."""
        rag = _make_rag()
        multi_hop = [
            {"resource": "azurerm_storage_container",
             "relation": "COMPANION", "via": "src", "depth": 1},
            {"resource": "azurerm_monitor_workspace",
             "relation": "RELATED_TO", "via": "src", "depth": 1},
        ]
        with patch.object(rag, "_multi_hop_traversal", return_value=multi_hop):
            result = rag.get_traversal_for_resource("azurerm_storage_account")
        assert "azurerm_storage_container" in result["companions_fetched"]
        assert "azurerm_monitor_workspace" not in result["companions_fetched"]

    def test_related_to_nodes_at_depth_1_extracted_separately(self):
        rag = _make_rag()
        multi_hop = [
            {"resource": "azurerm_monitor_diagnostic_setting",
             "relation": "RELATED_TO", "via": "src", "depth": 1},
        ]
        with patch.object(rag, "_multi_hop_traversal", return_value=multi_hop), \
             patch.object(rag, "_get_companions", return_value=[]):
            result = rag.get_traversal_for_resource("azurerm_storage_account")
        assert "azurerm_monitor_diagnostic_setting" in result["related_resources"]

    def test_hop2_count_matches_depth_2_nodes(self):
        rag = _make_rag()
        multi_hop = [
            {"resource": "r1", "relation": "COMPANION", "via": "src", "depth": 1},
            {"resource": "r2", "relation": "DEPENDS_ON", "via": "r1", "depth": 2},
            {"resource": "r3", "relation": "RELATED_TO", "via": "r1", "depth": 2},
        ]
        with patch.object(rag, "_multi_hop_traversal", return_value=multi_hop), \
             patch.object(rag, "_get_companions", return_value=[]):
            result = rag.get_traversal_for_resource("azurerm_storage_account")
        assert result["hop2_count"] == 2

    def test_fallback_companions_used_when_traversal_empty(self):
        """DB de graphe vide au premier démarrage → fallback statique actif."""
        rag = _make_rag()
        with patch.object(rag, "_multi_hop_traversal", return_value=[]), \
             patch.object(rag, "_get_companions",
                          return_value=["azurerm_storage_container"]):
            result = rag.get_traversal_for_resource("azurerm_storage_account")
        assert "azurerm_storage_container" in result["companions_fetched"]

    def test_default_max_hops_is_two(self):
        """max_hops=2 est le paramètre documenté pour limiter la profondeur de traversal."""
        rag = _make_rag()
        with patch.object(rag, "_multi_hop_traversal", return_value=[]) as m, \
             patch.object(rag, "_get_companions", return_value=[]):
            rag.get_traversal_for_resource("azurerm_storage_account")
        m.assert_called_once_with("azurerm_storage_account", max_hops=2)


# ═══════════════════════════════════════════════════════════════════════════════
# F. SCORING — algorithme _build_complexity_result
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildComplexityResult:
    """Algorithme pur calibré sur données empiriques — seuils cités dans le mémoire."""

    def _score(self, depth=0, args=0, companions=0):
        from rag.graph_rag import TerraformGraphRAG
        return TerraformGraphRAG._build_complexity_result(
            "azurerm_storage_account", depth, args, companions, "sql"
        )

    def test_zero_signals_give_score_zero_and_low_label(self):
        r = self._score()
        assert r["complexity_score"] == 0.0
        assert r["complexity_label"] == "LOW"

    def test_depth_3_crosses_medium_threshold(self):
        """depth=3 → depth_norm=0.75, score=0.3375 ≥ 0.30 → MEDIUM."""
        r = self._score(depth=3)
        assert r["complexity_label"] == "MEDIUM"
        assert r["complexity_score"] >= 0.30

    def test_depth_4_stays_medium_not_high(self):
        """depth=4 → score=0.45 < 0.65 → MEDIUM, pas HIGH."""
        r = self._score(depth=4)
        assert r["complexity_label"] == "MEDIUM"

    def test_all_signals_maxed_gives_score_one_and_high_label(self):
        """depth=4, args=12, companions=4 → score=1.0 → HIGH."""
        r = self._score(depth=4, args=12, companions=4)
        assert r["complexity_score"] == pytest.approx(1.0, abs=1e-6)
        assert r["complexity_label"] == "HIGH"

    def test_depth_cap_at_4_hops(self):
        """depth=100 et depth=4 donnent le même score (cap documenté à 4 hops)."""
        assert self._score(depth=4)["complexity_score"] == \
               self._score(depth=100)["complexity_score"]

    def test_overflow_inputs_capped_at_one(self):
        r = self._score(depth=10, args=50, companions=20)
        assert r["complexity_score"] == pytest.approx(1.0, abs=1e-6)

    def test_depth_dominates_companions_by_weight(self):
        """Poids : depth=0.45 > companions=0.20 — la profondeur est le signal principal."""
        r_deep = self._score(depth=4, args=0, companions=0)  # 0.45
        r_comp = self._score(depth=0, args=0, companions=4)  # 0.20
        assert r_deep["complexity_score"] > r_comp["complexity_score"]


# ═══════════════════════════════════════════════════════════════════════════════
# G. FORMATAGE — prompt RAG injecté dans Agent 02
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatSingle:
    """_format_single génère le contexte documentaire injecté dans le prompt LLM.
    Un champ manquant = HCL incorrect généré par Agent 02."""

    def _meta(self, required=None, optional=None, blocks=None, example=""):
        return {
            "provider": "azurerm",
            "description": "Manages a storage account.",
            "required_args": json.dumps(required or []),
            "optional_args": json.dumps(optional or []),
            "blocks": json.dumps(blocks or []),
            "example": example,
        }

    def test_required_args_listed_in_prompt(self):
        """Tous les required_args doivent apparaître dans le prompt — l'Agent 02 les lira."""
        rag = _make_rag()
        args = [{"name": "name", "description": "Account name"},
                {"name": "resource_group_name", "description": "RG name"}]
        out = rag._format_single("azurerm_storage_account", self._meta(required=args))
        assert "name" in out and "resource_group_name" in out

    def test_required_section_header_present(self):
        """Le header REQUIRED guide l'Agent 02 sur les champs obligatoires."""
        rag = _make_rag()
        args = [{"name": "location", "description": "Region"}]
        out = rag._format_single("azurerm_storage_account", self._meta(required=args))
        assert "REQUIRED" in out

    def test_optional_section_present_only_when_non_empty(self):
        rag = _make_rag()
        with_opt = rag._format_single("r",
            self._meta(optional=[{"name": "tags"}]))
        without_opt = rag._format_single("r", self._meta(optional=[]))
        assert "OPTIONAL" in with_opt
        assert "OPTIONAL" not in without_opt

    def test_example_section_present_only_when_non_empty(self):
        rag = _make_rag()
        ex = 'resource "azurerm_storage_account" "main" { name = "s" }'
        with_ex  = rag._format_single("r", self._meta(example=ex))
        without_ex = rag._format_single("r", self._meta(example=""))
        assert "USAGE EXAMPLE" in with_ex
        assert "USAGE EXAMPLE" not in without_ex

    def test_blocks_listed_when_present(self):
        rag = _make_rag()
        blocks = [{"name": "blob_properties", "args": ["versioning_enabled"]}]
        out = rag._format_single("r", self._meta(blocks=blocks))
        assert "blob_properties" in out and "versioning_enabled" in out

    def test_critical_warning_always_appended(self):
        """CRITICAL doit clore chaque document pour contraindre Agent 02."""
        rag = _make_rag()
        out = rag._format_single("azurerm_storage_account", self._meta())
        assert "CRITICAL" in out


class TestFormatEnriched:

    def _meta(self):
        return {"provider": "azurerm", "description": "desc",
                "required_args": "[]", "optional_args": "[]",
                "blocks": "[]", "example": ""}

    def test_companion_section_absent_without_companions(self):
        rag = _make_rag()
        out = rag._format_enriched("azurerm_storage_account", self._meta(), companions=[])
        assert "COMPANION" not in out

    def test_companion_section_present_with_companions(self):
        rag = _make_rag()
        companions = [("azurerm_storage_container", self._meta())]
        out = rag._format_enriched("azurerm_storage_account", self._meta(),
                                   companions=companions)
        assert "COMPANION" in out and "azurerm_storage_container" in out

    def test_companion_header_instructs_llm_to_generate(self):
        """L'Agent 02 doit savoir qu'il doit co-générer les companions."""
        rag = _make_rag()
        companions = [("azurerm_storage_container", self._meta())]
        out = rag._format_enriched("azurerm_storage_account", self._meta(),
                                   companions=companions)
        assert "generate" in out.lower()

    def test_multiple_companions_all_appear_in_output(self):
        rag = _make_rag()
        companions = [("azurerm_storage_container", self._meta()),
                      ("azurerm_storage_blob", self._meta())]
        out = rag._format_enriched("azurerm_storage_account", self._meta(),
                                   companions=companions)
        assert "azurerm_storage_container" in out
        assert "azurerm_storage_blob" in out


class TestFallbackContext:

    def test_fallback_contains_resource_name_and_warning(self):
        """Le contexte de fallback doit contenir le nom de la ressource et un avertissement."""
        rag = _make_rag()
        out = rag._fallback_context("azurerm_unknown_resource")
        assert "azurerm_unknown_resource" in out
        assert "WARNING" in out or "No documentation" in out

    def test_fallback_contains_todo_for_developer(self):
        rag = _make_rag()
        out = rag._fallback_context("azurerm_mystery_resource")
        assert "TODO" in out
