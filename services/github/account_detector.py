"""
account_detector.py — Fonction 2 : détection du type de compte GitHub (org vs user).

Détermine l'endpoint API à utiliser pour lister les repos :
  /orgs/<name>/repos  → organisation
  /users/<name>/repos → utilisateur
"""
import logging
import re

from fastapi import HTTPException
from github import Github, GithubException, UnknownObjectException

logger = logging.getLogger("AccountDetector")

_URL_PATTERN = re.compile(
    r"(?:https?://)?github\.com/([^/\s]+)"
)


def _extract_name(github_url: str) -> str:
    """Extrait le nom d'organisation ou d'utilisateur depuis l'URL GitHub.

    Exemples :
      "github.com/acme"          → "acme"
      "https://github.com/acme" → "acme"
      "https://github.com/acme/repo" → "acme"

    Raises:
        HTTPException 422 si l'URL ne contient pas de nom valide.
    """
    match = _URL_PATTERN.search(github_url.strip())
    if not match:
        raise HTTPException(
            status_code=422,
            detail=f"URL GitHub invalide — format attendu : github.com/<nom>. Reçu : {github_url!r}",
        )
    return match.group(1)


def detect_account_type(token: str, github_url: str) -> dict:
    """Détecte si l'entité GitHub est une organisation ou un utilisateur.

    Returns:
        {"type": "org"|"user", "name": "<nom>"}

    Raises:
        HTTPException 422 si l'URL est malformée.
        HTTPException 404 si le compte n'existe pas.
        HTTPException 401 si le token est insuffisant.
    """
    name = _extract_name(github_url)

    try:
        g = Github(token)
        g.get_organization(name)
        logger.info(f"Compte '{name}' détecté comme organisation")
        return {"type": "org", "name": name}
    except UnknownObjectException:
        pass
    except GithubException as exc:
        if exc.status == 401:
            raise HTTPException(status_code=401, detail="Token GitHub invalide ou expiré")

    try:
        g = Github(token)
        g.get_user(name)
        logger.info(f"Compte '{name}' détecté comme utilisateur")
        return {"type": "user", "name": name}
    except UnknownObjectException:
        raise HTTPException(
            status_code=404,
            detail=f"Compte GitHub '{name}' introuvable (ni organisation ni utilisateur)",
        )
    except GithubException as exc:
        raise HTTPException(
            status_code=exc.status or 500,
            detail=f"Erreur GitHub lors de la résolution du compte '{name}': {exc.data}",
        )
