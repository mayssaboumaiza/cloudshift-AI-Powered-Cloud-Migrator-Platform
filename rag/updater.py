"""
updater.py — Check for and apply updates to the PostgreSQL RAG corpus.

Public API:
    updater = RAGUpdater(github_token="...")
    updater.check_and_update()         → dict (per-provider status)
    updater.force_rebuild(provider=None)

Internals:
    • needs_update() compares the latest GitHub SHA with rag_commits
    • After build_all(), calls graph_rag.rebuild() to reset the singleton
"""

import os
import sys

from rag.corpus_builder import RAGBuilder
from rag.rag_config import PROVIDERS, get_pg_connection
from rag.github_doc_fetcher import GitHubFetcher


class RAGUpdater:
    """
    Vérifie et applique les mises à jour de la base RAG.

    Usage :
    - Au démarrage de l'application : check_and_update()
    - Via cron quotidien : python -m rag.updater
    """

    def __init__(self, github_token: str = None):
        self.token = github_token or os.getenv("GITHUB_TOKEN")
        self.builder = RAGBuilder(github_token=self.token)
        self.fetcher = GitHubFetcher(token=self.token)

    def check_and_update(self) -> dict:
        """
        Vérifie si des mises à jour sont disponibles via rag_commits et les applique.
        Retourne un dict avec le statut par provider.
        """
        status = {}
        providers_to_update: list[str] = []

        for provider in PROVIDERS:
            needs, sha = self._needs_update(provider)
            if needs:
                print(f"[{provider}] Update available (SHA: {sha[:8]})")
                status[provider] = "updated"
                providers_to_update.append(provider)
            else:
                print(f"[{provider}] Already up to date")
                status[provider] = "up_to_date"

        if providers_to_update:
            self.builder.build_all(force=False)
            self._reset_graph_rag()
        else:
            print("All providers are up to date.")

        return status

    def force_rebuild(self, provider: str = None):
        """Force la reconstruction complète pour un ou tous les providers."""
        if provider:
            print(f"Force rebuild for {provider}...")
            resources = self.builder._build_provider(provider)
            self.builder._build_graph_relations(provider, resources)
            sha = self.fetcher.get_latest_commit_sha(provider)
            self.builder._save_commit(provider, sha)
        else:
            print("Force rebuild for all providers...")
            self.builder.build_all(force=True)

        self._reset_graph_rag()

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _needs_update(self, provider: str) -> tuple[bool, str]:
        """
        Compare le SHA GitHub actuel avec le SHA stocké dans rag_commits.
        Fallback sur last_commits.json si la table SQL est vide.
        """
        latest_sha = self.fetcher.get_latest_commit_sha(provider)
        known_sha = ""

        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT sha FROM rag_commits WHERE provider = %s",
                        (provider,),
                    )
                    row = cur.fetchone()
                    if row:
                        known_sha = row[0]
        except Exception:
            # Fallback JSON (avant la première migration SQL)
            commits = self.fetcher.load_last_commits()
            known_sha = commits.get(provider, "")

        return (latest_sha != known_sha, latest_sha)

    def _reset_graph_rag(self) -> None:
        """Réinitialise le singleton GraphRAG après un build."""
        try:
            from rag.graph_rag import TerraformGraphRAG
            TerraformGraphRAG.rebuild(TerraformGraphRAG)
        except Exception as e:
            print(f"Warning: could not reset GraphRAG singleton: {e}")


if __name__ == "__main__":
    token = os.getenv("GITHUB_TOKEN")
    updater = RAGUpdater(github_token=token)

    if len(sys.argv) > 1 and sys.argv[1] == "force":
        provider_arg = sys.argv[2] if len(sys.argv) > 2 else None
        updater.force_rebuild(provider=provider_arg)
    else:
        updater.check_and_update()
