"""
migration_artifact_service.py — Livraison des artefacts IaC vers GitHub.

Extrait de migration_service.py (accept_plan) pour respecter le principe
de responsabilité unique. MigrationService délègue ici toute logique de
livraison d'artefacts, sans dépendre des détails de l'API GitHub.

Responsabilités :
  - Résoudre le GitHub token (Vault en priorité, fallback DB Fernet)
  - Construire les listes OutputFile depuis les fichiers générés
  - Choisir la stratégie de livraison (SIMPLE / MONOREPO / etc.)
  - Appeler deliver_to_github et retourner les métadonnées de livraison
"""
import logging
import os
from typing import Optional

from core.token_encryption import decrypt_token
from services.credentials.vault_store import get_credential_store

logger = logging.getLogger("MigrationArtifactService")


def _resolve_github_token(migration_id: str, db_encrypted_token: Optional[str]) -> Optional[str]:
    """Résout le token GitHub pour une migration donnée.

    Ordre de priorité :
    1. Vault (stocké à la création de la migration)
    2. Token chiffré Fernet en DB (migrations pré-Vault)

    Returns:
        Token GitHub brut, ou None si aucun token disponible.
    """
    vault_token: Optional[str] = None
    try:
        store = get_credential_store()
        creds = store.get(user_id=str(migration_id), provider="github_token")
        vault_token = creds.get("token") if creds else None
    except Exception as e:
        logger.warning("_resolve_github_token: Vault lookup failed (%s) — trying DB fallback", e)

    if vault_token:
        return vault_token

    if db_encrypted_token:
        return decrypt_token(db_encrypted_token)

    return None


def deliver_artifacts_to_github(
    migration_id: str,
    db_encrypted_token: Optional[str],
    artifacts: dict,
    gen_files: list,
    migration_plan: dict,
    target_cloud: str,
    source_repo: str,
    output_dir: str,
) -> Optional[dict]:
    """Livre les artefacts IaC vers un nouveau repo GitHub.

    Args:
        migration_id:        ID de la migration (UUID).
        db_encrypted_token:  Token Fernet stocké en DB (fallback pre-Vault).
        artifacts:           Dict d'artefacts produits par Agent 02/03.
        gen_files:           Liste de fichiers générés (str ou dict path/content).
        migration_plan:      Plan de migration (pour choose_output_strategy).
        target_cloud:        Provider cible (aws / gcp / azure).
        source_repo:         URL du repo source.
        output_dir:          Répertoire de sortie IaC sur le filesystem.

    Returns:
        Dict de livraison {"repo_url": ..., "pr_url": ...} ou None si échec.
    """
    token = _resolve_github_token(migration_id, db_encrypted_token)
    if not token:
        logger.info(
            "deliver_artifacts_to_github: no GitHub token available for migration %s — skipping",
            migration_id,
        )
        return None

    try:
        from services.output.migration_output import (
            MigrationOutput,
            OutputFile,
            choose_output_strategy,
            deliver_to_github,
        )

        terraform_files, cicd_files, code_patches = [], [], []

        for f in (gen_files or []):
            if isinstance(f, dict):
                path = f.get("path", "")
                content = f.get("content", "")
            else:
                path = str(f)
                try:
                    full = os.path.join(output_dir, os.path.basename(path))
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError:
                    content = ""

            of = OutputFile(path=path, content=content)
            if path.endswith(".tf") or "terraform" in path.lower():
                terraform_files.append(of)
            elif ".github/workflows" in path or "cicd" in path.lower():
                cicd_files.append(of)
            else:
                code_patches.append(of)

        # Fallback : si aucun fichier .tf listé mais terraform_code disponible dans artifacts
        if not terraform_files and artifacts.get("terraform_code"):
            terraform_files.append(
                OutputFile(path="main.tf", content=artifacts["terraform_code"])
            )

        output = MigrationOutput(
            target_cloud=target_cloud,
            source_repo=source_repo,
            migration_plan=migration_plan,
            terraform_files=terraform_files,
            cicd_files=cicd_files,
            code_patches=code_patches,
        )

        strategy = choose_output_strategy(migration_plan)
        delivery = deliver_to_github(output, token.strip(), strategy=strategy)
        logger.info(
            "deliver_artifacts_to_github: GitHub delivery successful for migration %s: %s",
            migration_id,
            delivery.get("repo_url"),
        )
        return delivery

    except Exception as delivery_exc:
        logger.warning(
            "deliver_artifacts_to_github: GitHub delivery failed for migration %s (non-blocking): %s",
            migration_id,
            delivery_exc,
        )
        return None
