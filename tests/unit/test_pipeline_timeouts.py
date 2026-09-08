"""
tests/unit/test_pipeline_timeouts.py — Validation des constantes de maîtrise des délais.

Couvre RNF-1 (Maîtrise des délais) :
  - les valeurs par défaut garantissent une terminaison bornée du pipeline
  - les limites documentées dans le Chapitre 5 du mémoire sont cohérentes avec le code
  - les constantes de sécurité (retention) sont conformes aux standards cloud

Philosophie : ces tests sont des "tests de régression de configuration".
Ils détectent toute modification accidentelle des bornes qui changerait
le comportement documenté du pipeline sans mise à jour du mémoire.
"""
import os
import pytest


# ── Import des constantes ─────────────────────────────────────────────────────

from core.constants import (
    # Bornes pipeline (agent loops)
    MAX_CORRECTION_ATTEMPTS,
    MAX_REJECTION_COUNT,
    MAX_REACT_ITERATIONS,
    MAX_INTENT_REGEN,
    # Bornes runner (TerraformRunner)
    MAX_RUNNER_FAILURE_HISTORY,
    MAX_RUNNER_TOTAL_ATTEMPTS,
    MAX_RUNNER_REGEN_COUNT,
    MAX_RUNNER_RETRY_COUNT,
    MAX_RUNNER_SIG_REPEATS,
    # Sécurité IaC
    MAX_SECURITY_REGEN,
    MAX_FILES_LLM,
    # Thresholds 7R
    THRESHOLD_REHOST_HIGH,
    THRESHOLD_REHOST_LOW,
    THRESHOLD_REPLATFORM_HIGH,
    THRESHOLD_REPLATFORM_LOW,
    # Retention IaC
    RETENTION_BACKUP_DAYS,
    RETENTION_AUDIT_DAYS,
    RETENTION_LOG_DAYS,
    RETENTION_SOFT_DELETE_DAYS,
    RETENTION_QUEUE_LOG_DAYS,
    # Provider versions
    PROVIDER_VERSION_FLOORS,
    # Cooldown
    COOLDOWN_SECONDS,
)


# ── Bornes pipeline documentées dans le mémoire ───────────────────────────────

class TestPipelineBounds:
    """Ces valeurs sont citées explicitement dans le Chapitre 5 du mémoire.
    Toute modification doit être synchronisée avec la documentation."""

    def test_max_correction_attempts_default_is_five(self):
        """Documenté : boucle fix_targeted max 5 itérations (Chapter 5, RNF-1)."""
        assert MAX_CORRECTION_ATTEMPTS == 5

    def test_max_rejection_count_default_is_two(self):
        """Documenté : max 2 cycles de correction IaC avant escalade (Chapter 5, RNF-1)."""
        assert MAX_REJECTION_COUNT == 2

    def test_max_react_iterations_default_is_three(self):
        """Documenté : Agent 03 ReAct loop max 3 itérations (Chapter 5, RNF-1)."""
        assert MAX_REACT_ITERATIONS == 3

    def test_max_intent_regen_default_is_one(self):
        """Régénération intent max 1 fois — évite boucle infinie sur validation."""
        assert MAX_INTENT_REGEN == 1

    def test_pipeline_always_terminates(self):
        """Le pipeline doit toujours se terminer : toutes les bornes sont > 0 et finies."""
        bounds = [
            MAX_CORRECTION_ATTEMPTS,
            MAX_REJECTION_COUNT,
            MAX_REACT_ITERATIONS,
            MAX_INTENT_REGEN,
        ]
        for b in bounds:
            assert isinstance(b, int)
            assert b > 0
            assert b < 100  # sanity check : pas de valeur aberrante


# ── Bornes TerraformRunner ────────────────────────────────────────────────────

class TestRunnerBounds:

    def test_max_runner_total_attempts_default_is_six(self):
        """Circuit breaker : max 6 tentatives totales avant abandon du job."""
        assert MAX_RUNNER_TOTAL_ATTEMPTS == 6

    def test_max_runner_regen_count_default_is_three(self):
        """Max 3 régénérations IaC déclenchées par le runner."""
        assert MAX_RUNNER_REGEN_COUNT == 3

    def test_max_runner_retry_count_default_is_two(self):
        """Max 2 tentatives sur erreurs transitoires (réseau, throttle)."""
        assert MAX_RUNNER_RETRY_COUNT == 2

    def test_max_runner_sig_repeats_default_is_three(self):
        """Max 3 répétitions de la même signature d'erreur avant escalade."""
        assert MAX_RUNNER_SIG_REPEATS == 3

    def test_all_runner_bounds_are_positive_integers(self):
        runner_bounds = {
            "MAX_RUNNER_FAILURE_HISTORY": MAX_RUNNER_FAILURE_HISTORY,
            "MAX_RUNNER_TOTAL_ATTEMPTS":  MAX_RUNNER_TOTAL_ATTEMPTS,
            "MAX_RUNNER_REGEN_COUNT":     MAX_RUNNER_REGEN_COUNT,
            "MAX_RUNNER_RETRY_COUNT":     MAX_RUNNER_RETRY_COUNT,
            "MAX_RUNNER_SIG_REPEATS":     MAX_RUNNER_SIG_REPEATS,
        }
        for name, val in runner_bounds.items():
            assert isinstance(val, int), f"{name} doit être un entier"
            assert val > 0, f"{name} doit être > 0"


# ── Seuils 7R documentés ─────────────────────────────────────────────────────

class TestSevenRThresholds:

    def test_rehost_high_is_095(self):
        """REHOST certain : similarité cosinus ≥ 0.95 (calibré sur SBERT all-mpnet-base-v2)."""
        assert THRESHOLD_REHOST_HIGH == pytest.approx(0.95, abs=1e-6)

    def test_rehost_low_is_090(self):
        """Zone grise REHOST : similarité ∈ [0.90, 0.95[."""
        assert THRESHOLD_REHOST_LOW == pytest.approx(0.90, abs=1e-6)

    def test_replatform_high_is_090(self):
        """REPLATFORM certain : similarité ∈ [0.82, 0.90[."""
        assert THRESHOLD_REPLATFORM_HIGH == pytest.approx(0.90, abs=1e-6)

    def test_replatform_low_is_078(self):
        """Zone REFACTOR : similarité < 0.78."""
        assert THRESHOLD_REPLATFORM_LOW == pytest.approx(0.78, abs=1e-6)

    def test_rehost_high_greater_than_rehost_low(self):
        assert THRESHOLD_REHOST_HIGH > THRESHOLD_REHOST_LOW

    def test_replatform_thresholds_ordered(self):
        assert THRESHOLD_REPLATFORM_HIGH > THRESHOLD_REPLATFORM_LOW

    def test_all_thresholds_in_zero_one_range(self):
        thresholds = [
            THRESHOLD_REHOST_HIGH,
            THRESHOLD_REHOST_LOW,
            THRESHOLD_REPLATFORM_HIGH,
            THRESHOLD_REPLATFORM_LOW,
        ]
        for t in thresholds:
            assert 0.0 < t < 1.0


# ── Retention IaC (sécurité) ──────────────────────────────────────────────────

class TestRetentionConstants:

    def test_backup_retention_at_least_7_days(self):
        """Rétention des backups DB : minimum 7 jours (bonne pratique cloud)."""
        assert RETENTION_BACKUP_DAYS >= 7

    def test_audit_retention_at_least_90_days(self):
        """Rétention des logs d'audit : minimum 90 jours (conformité RGPD)."""
        assert RETENTION_AUDIT_DAYS >= 90

    def test_log_retention_at_least_30_days(self):
        """Rétention des logs applicatifs : minimum 30 jours."""
        assert RETENTION_LOG_DAYS >= 30

    def test_soft_delete_retention_positive(self):
        assert RETENTION_SOFT_DELETE_DAYS > 0

    def test_all_retention_values_are_positive_integers(self):
        retentions = {
            "BACKUP_DAYS":     RETENTION_BACKUP_DAYS,
            "AUDIT_DAYS":      RETENTION_AUDIT_DAYS,
            "LOG_DAYS":        RETENTION_LOG_DAYS,
            "SOFT_DELETE_DAYS": RETENTION_SOFT_DELETE_DAYS,
            "QUEUE_LOG_DAYS":  RETENTION_QUEUE_LOG_DAYS,
        }
        for name, val in retentions.items():
            assert isinstance(val, int), f"{name} doit être un entier"
            assert val > 0, f"{name} doit être > 0"


# ── Versions de providers Terraform ───────────────────────────────────────────

class TestProviderVersions:

    def test_azurerm_version_floor_defined(self):
        """La version minimale du provider azurerm est définie."""
        assert "azurerm" in PROVIDER_VERSION_FLOORS
        assert len(PROVIDER_VERSION_FLOORS["azurerm"]) > 0

    def test_aws_version_floor_defined(self):
        assert "aws" in PROVIDER_VERSION_FLOORS
        assert len(PROVIDER_VERSION_FLOORS["aws"]) > 0

    def test_google_version_floor_defined(self):
        assert "google" in PROVIDER_VERSION_FLOORS
        assert len(PROVIDER_VERSION_FLOORS["google"]) > 0

    def test_provider_versions_use_constraint_syntax(self):
        """Les contraintes de version doivent contenir '>=' (bonnes pratiques Terraform)."""
        for provider, constraint in PROVIDER_VERSION_FLOORS.items():
            assert ">=" in constraint, (
                f"Provider {provider}: la contrainte doit contenir '>=' — reçu: {constraint!r}"
            )

    def test_azurerm_version_requires_at_least_v4(self):
        """azurerm v4+ requis (breaking changes v4 dans les ressources PostgreSQL/Storage)."""
        constraint = PROVIDER_VERSION_FLOORS["azurerm"]
        assert "4" in constraint


# ── Cohérence des constantes configurables via env vars ──────────────────────

class TestEnvVarOverrides:
    """Les constantes doivent être surchargeable via env vars (opérabilité)."""

    def test_max_corrections_overridable(self, monkeypatch):
        monkeypatch.setenv("MAX_CORRECTIONS", "10")
        import importlib
        import core.constants as c
        importlib.reload(c)
        assert c.MAX_CORRECTION_ATTEMPTS == 10
        # Remettre la valeur par défaut
        monkeypatch.delenv("MAX_CORRECTIONS", raising=False)
        importlib.reload(c)

    def test_cooldown_seconds_is_positive(self, monkeypatch):
        """Le cooldown par défaut entre analyse et planification est positif.

        On efface COOLDOWN_SECONDS de l'env (la CI le force à "0" pour accélérer
        les tests) afin de vérifier la valeur par défaut réelle du code.
        """
        monkeypatch.delenv("COOLDOWN_SECONDS", raising=False)
        import importlib
        import core.constants as c
        importlib.reload(c)
        assert c.COOLDOWN_SECONDS > 0
        # Remettre l'état d'origine pour les autres tests
        importlib.reload(c)
