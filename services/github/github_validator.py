"""
github_validator.py — Fonction 1 : validation du token GitHub via PyGithub.
"""
import logging

from fastapi import HTTPException
from github import Github, GithubException

logger = logging.getLogger("GitHubValidator")


def validate_github_token(token: str) -> bool:
    """Vérifie que le token GitHub est valide et actif.

    Raises:
        HTTPException 401 si le token est invalide ou expiré.
    """
    try:
        g = Github(token)
        login = g.get_user().login
        logger.info(f"Token valide — compte: {login[:2]}***")
        return True
    except GithubException as exc:
        logger.warning(f"Token invalide — status={exc.status}")
        raise HTTPException(
            status_code=401,
            detail="Token GitHub invalide ou expiré",
        )
    except Exception as exc:
        logger.error(f"Erreur inattendue lors de la validation du token: {type(exc).__name__}")
        raise HTTPException(
            status_code=401,
            detail="Token GitHub invalide ou expiré",
        )
