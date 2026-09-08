"""
doc_relation_parser.py — Passe 2 du graph builder (fallback niveau 2).

Utilisé seulement quand tf_relation_parser n'a trouvé aucune relation
pour une ressource donnée.

Parse le texte libre (hors blocs HCL) à la recherche de mentions
de dépendances via des patterns linguistiques.
"""

import re

# Supprime tous les blocs de code pour ne garder que le texte libre
_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:\w+)?\n.*?```",
    re.DOTALL,
)

# Patterns à détecter dans le texte libre (insensible à la casse).
# Chaque groupe capturant isole le nom de la ressource cible.
_LINGUISTIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"requires\s+an?\s+([a-z][a-z0-9_]+)", re.IGNORECASE),
    re.compile(r"used\s+with\s+([a-z][a-z0-9_]+)", re.IGNORECASE),
    re.compile(r"used\s+in\s+conjunction\s+with\s+([a-z][a-z0-9_]+)", re.IGNORECASE),
    re.compile(r"depends\s+on\s+([a-z][a-z0-9_]+)", re.IGNORECASE),
    re.compile(r"associated\s+([a-z][a-z0-9_]+)", re.IGNORECASE),
    re.compile(r"must\s+create\s+([a-z][a-z0-9_]+)", re.IGNORECASE),
    re.compile(r"must\s+have\s+([a-z][a-z0-9_]+)", re.IGNORECASE),
]


def _strip_code_blocks(content: str) -> str:
    return _CODE_BLOCK_PATTERN.sub("", content)


def extract_relations_from_text(
    resource_name: str,
    doc_content: str,
    provider_prefix: str,
) -> list[tuple[str, str, str, str]]:
    """
    Parse le texte libre du doc_content (blocs HCL exclus) pour détecter
    les mentions linguistiques de dépendances entre ressources.

    Retourne une liste dédupliquée de tuples :
        (source, target, 'DOC_MENTIONS', 'doc')
    """
    text = _strip_code_blocks(doc_content)
    relations: set[tuple[str, str, str, str]] = set()

    for pattern in _LINGUISTIC_PATTERNS:
        for match in pattern.finditer(text):
            target = match.group(1)

            # Garder seulement les targets qui appartiennent au bon provider
            if not target.startswith(provider_prefix):
                continue

            # Ignorer les auto-références
            if target == resource_name:
                continue

            relations.add((resource_name, target, "DOC_MENTIONS", "doc"))

    return list(relations)
