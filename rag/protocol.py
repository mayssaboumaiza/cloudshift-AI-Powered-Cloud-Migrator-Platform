"""
rag/protocol.py - Protocole abstrait pour les fournisseurs RAG.

Permet de découpler Agent 02 du singleton TerraformGraphRAG.
Tout fournisseur implémentant RAGProvider peut être injecté sans
modifier le code des agents (testabilité, substitution future).

Usage dans Agent 02 :
    from rag.protocol import RAGProvider
    def run_agent_02(state, rag: RAGProvider = None): ...

Usage dans les tests :
    class FakeRAG(RAGProvider):
        async def get_context(self, resource_name): return "mocked context"
        async def health(self): return {"available": True, "node_count": 0}
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RAGProvider(Protocol):
    """Interface minimale attendue d'un fournisseur Graph RAG."""

    async def get_context(self, resource_name: str, *, max_hops: int = 4) -> str:
        """Retourne le contexte documentaire Terraform pour une ressource donnée.

        Args:
            resource_name: Nom de la ressource Terraform (ex: 'azurerm_storage_account').
            max_hops: Nombre maximal de sauts dans le graphe de connaissance.

        Returns:
            Contexte formaté en Markdown, prêt à être injecté dans le prompt LLM.
        """
        ...

    async def health(self) -> dict:
        """Retourne l'état de santé du fournisseur RAG.

        Returns:
            dict avec au minimum :
                available (bool): True si le service est opérationnel.
                node_count (int): Nombre de noeuds dans la base de connaissance.
                populated (bool): True si la base contient des embeddings.
        """
        ...


def get_default_rag_provider() -> RAGProvider:
    """Retourne le fournisseur RAG par défaut (TerraformGraphRAG).

    Importé lazily pour éviter les imports circulaires.
    Utiliser cette fonction dans FastAPI via dependency injection plutôt
    qu'un import direct de TerraformGraphRAG.
    """
    from rag.graph_rag import TerraformGraphRAG
    return TerraformGraphRAG()
