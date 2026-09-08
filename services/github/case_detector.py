"""
case_detector.py — Fonction 4a : détection CAS A / CAS B.

Règle :
  CAS A — repo unique, 1 seul service (tout à la racine, pas de sous-dossiers)
            Exemple : alice/my-app avec main.tf + variables.tf à la racine
            → Pipeline direct, 1 dependency_graph

  CAS B — multi-services (plusieurs repos OU monorepo avec sous-dossiers)
            Exemple B1 : selected_repos = [alice/auth, alice/api]  (2 repos)
            Exemple B2 : alice/platform avec auth/ api/ worker/ (3 sous-dossiers)
            → Plusieurs dependency_graphs fusionnés via merge_graphs()

Fichiers marqueurs : Dockerfile, package.json, requirements.txt, go.mod
"""
import logging
from typing import Any

from fastapi import HTTPException
from github import Github, GithubException, UnknownObjectException

logger = logging.getLogger("CaseDetector")

_MARKER_FILES = {"Dockerfile", "package.json", "requirements.txt", "go.mod"}


def _get_repo_object(g: Github, full_name: str):
    """Récupère l'objet PyGithub pour un repo (full_name = owner/repo)."""
    try:
        return g.get_repo(full_name)
    except UnknownObjectException:
        raise HTTPException(
            status_code=404,
            detail=f"Repo '{full_name}' introuvable ou inaccessible",
        )
    except GithubException as exc:
        raise HTTPException(
            status_code=exc.status or 500,
            detail=f"Erreur GitHub pour le repo '{full_name}': {exc.data}",
        )


def _find_service_subdirs(repo) -> list[str]:
    """Retourne les sous-dossiers racine qui contiennent au moins un fichier marqueur.

    Utilise l'API GitHub pour lister les contenus, sans cloner.
    """
    try:
        root_contents = repo.get_contents("")
    except GithubException as exc:
        logger.warning(f"Impossible de lister la racine de {repo.full_name}: {exc}")
        return []

    service_dirs: list[str] = []

    for item in root_contents:
        if item.type != "dir":
            continue

        try:
            dir_contents = repo.get_contents(item.path)
        except GithubException:
            continue

        filenames = {f.name for f in dir_contents if f.type == "file"}
        if filenames & _MARKER_FILES:
            service_dirs.append(item.name)
            logger.debug(f"  Sous-dossier service détecté : {item.name}")

    return service_dirs


def detect_case(token: str, selected_repos: list[str]) -> dict[str, Any]:
    """Détecte le cas d'architecture et construit la liste des services.

    Args:
        token:          Token GitHub.
        selected_repos: full_names des repos sélectionnés (ex: ["acme/auth", "acme/api"]).

    Returns:
        {
          "case": "A" | "B",
          "services": [
            {
              "service_name": str,
              "repo":         str,   # full_name
              "path":         str,   # "/" ou "/sous-dossier"
            }
          ]
        }

    Raises:
        HTTPException 400 si selected_repos est vide.
        HTTPException 404 si un repo est introuvable.
    """
    if not selected_repos:
        raise HTTPException(
            status_code=400,
            detail="selected_repos ne peut pas être vide",
        )

    g = Github(token)

    # ── CAS B : plusieurs repos distincts ────────────────────────────────────
    if len(selected_repos) > 1:
        services = []
        for full_name in selected_repos:
            repo = _get_repo_object(g, full_name)
            services.append({
                "service_name": repo.name,
                "repo": repo.full_name,
                "path": "/",
            })
        logger.info(f"CAS B (multi-repos) — {len(services)} services")
        return {"case": "B", "services": services}

    # ── Repo unique : inspecter le contenu ────────────────────────────────────
    full_name = selected_repos[0]
    repo = _get_repo_object(g, full_name)

    service_dirs = _find_service_subdirs(repo)

    if service_dirs:
        # CAS B : monorepo — chaque sous-dossier est un service indépendant
        services = [
            {
                "service_name": subdir,
                "repo": repo.full_name,
                "path": f"/{subdir}",
            }
            for subdir in service_dirs
        ]
        logger.info(f"CAS B (monorepo) — {len(services)} services dans {repo.full_name}")
        return {"case": "B", "services": services}

    # CAS A : repo unique, 1 seul service, tout à la racine
    logger.info(f"CAS A — 1 service à la racine de {repo.full_name}")
    return {
        "case": "A",
        "services": [
            {
                "service_name": repo.name,
                "repo": repo.full_name,
                "path": "/",
            }
        ],
    }
