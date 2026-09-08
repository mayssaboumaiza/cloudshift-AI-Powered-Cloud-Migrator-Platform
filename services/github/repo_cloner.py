"""
repo_cloner.py — Fonction 4b : clonage shallow des repos détectés.

Stratégie :
  - git clone --depth=1 dans /tmp/migrator/<repo_name>/
  - CAS C : si le repo est déjà cloné (plusieurs services dans le même repo),
            ne pas recloner — réutiliser le clone existant.
  - Token injecté dans l'URL de clone (jamais loggué).
  - local_path = chemin cloné + path du service
      CAS B : /tmp/migrator/acme-auth/
      CAS C : /tmp/migrator/acme-platform/auth/
"""
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger("RepoCloner")

_CLONE_BASE = Path("/tmp/migrator")


def _clone_url(token: str, full_name: str) -> str:
    return f"https://{token}@github.com/{full_name}.git"


def _masked_url(full_name: str) -> str:
    return f"https://***@github.com/{full_name}.git"


def _repo_slug(full_name: str) -> str:
    """Convertit "owner/repo" en "owner-repo" pour le nom de dossier."""
    return full_name.replace("/", "-")


def _clone_repo(token: str, full_name: str, dest: Path) -> None:
    """Clone un repo en shallow clone dans dest.

    Le token est dans l'URL mais jamais dans les logs.

    Raises:
        HTTPException 500 si git clone échoue.
    """
    url = _clone_url(token, full_name)
    logger.info(f"git clone --depth=1 {_masked_url(full_name)} → {dest}")

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    result = subprocess.run(
        ["git", "clone", "--depth=1", url, str(dest)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    if result.returncode != 0:
        stderr_safe = result.stderr.replace(token, "***") if token in result.stderr else result.stderr
        logger.error(f"git clone failed pour {full_name}: {stderr_safe}")
        raise HTTPException(
            status_code=500,
            detail=f"Impossible de cloner le repo '{full_name}': git clone a échoué",
        )

    logger.info(f"Clone réussi : {full_name} → {dest}")


def clone_repos(token: str, services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clone chaque service et enrichit la liste avec local_path.

    Optimisation CAS C : un même repo partagé par plusieurs services
    n'est cloné qu'une seule fois (réutilisation du dossier).

    Args:
        token:    Token GitHub.
        services: Liste de services issus de detect_case :
                  [{"service_name": str, "repo": str, "path": str}]

    Returns:
        Même liste enrichie avec "local_path" :
        [{"service_name": str, "repo": str, "path": str, "local_path": str}]

    Raises:
        HTTPException 500 si un clone échoue.
    """
    _CLONE_BASE.mkdir(parents=True, exist_ok=True)

    already_cloned: set[str] = set()
    enriched: list[dict[str, Any]] = []

    for svc in services:
        full_name = svc["repo"]
        service_path = svc["path"].lstrip("/")
        slug = _repo_slug(full_name)
        clone_dest = _CLONE_BASE / slug

        if full_name not in already_cloned:
            if clone_dest.exists():
                logger.info(f"Répertoire {clone_dest} déjà présent — clone ignoré")
            else:
                _clone_repo(token, full_name, clone_dest)
            already_cloned.add(full_name)

        if service_path:
            local_path = str(clone_dest / service_path)
        else:
            local_path = str(clone_dest)

        enriched.append({
            **svc,
            "local_path": local_path,
        })

    return enriched
