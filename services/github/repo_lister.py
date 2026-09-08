"""
repo_lister.py — Fonction 3 : liste les repos GitHub d'un compte (org ou user).

Filtre automatiquement :
  - repos archivés  (excluded)
  - tous les autres repos actifs (inclus, y compris les forks)
"""
import logging
from typing import Any

from fastapi import HTTPException
from github import Github, GithubException

logger = logging.getLogger("RepoLister")


def list_repos(token: str, account: dict) -> list[dict[str, Any]]:
    """Récupère tous les repos actifs du compte (archives exclues, forks inclus).

    Args:
        token:   Token GitHub.
        account: Résultat de detect_account_type — {"type": "org"|"user", "name": str}.

    Returns:
        Liste de dicts :
        {
          "name"           : str,
          "full_name"      : str,
          "description"    : str | None,
          "private"        : bool,
          "fork"           : bool,    # True si c'est un fork
          "language"       : str | None,
          "updated_at"     : str,   # ISO 8601
          "default_branch" : str,
        }

    Raises:
        HTTPException 404 si le compte n'existe pas.
        HTTPException 500 en cas d'erreur GitHub inattendue.
    """
    account_type = account.get("type")
    name = account.get("name", "")

    try:
        g = Github(token)

        if account_type == "org":
            entity = g.get_organization(name)
            repos = entity.get_repos(type="all")
        else:
            authenticated_user = g.get_user()
            if authenticated_user.login.lower() == name.lower():
                repos = authenticated_user.get_repos(
                    type="owner",
                    sort="updated",
                    direction="desc",
                )
            else:
                repos = g.get_user(name).get_repos(
                    type="owner",
                    sort="updated",
                    direction="desc",
                )

        result = []
        for repo in repos:
            if repo.archived:
                continue
            if repo.fork:
                continue
            result.append({
                "name": repo.name,
                "full_name": repo.full_name,
                "description": repo.description,
                "private": repo.private,
                "fork": repo.fork,
                "language": repo.language,
                "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
                "default_branch": repo.default_branch or "main",
            })

        logger.info(f"[{name}] {len(result)} repos actifs trouvés (archives exclus)")
        return result

    except GithubException as exc:
        if exc.status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Compte '{name}' introuvable",
            )
        raise HTTPException(
            status_code=exc.status or 500,
            detail=f"Erreur GitHub lors de la liste des repos de '{name}': {exc.data}",
        )
