"""
tf_relation_parser.py — Passe 1 du graph builder.

Extrait les dépendances réelles entre ressources Terraform en parsant
les blocs HCL présents dans les fichiers .md déjà téléchargés.

Aucune requête GitHub n'est effectuée ici.
"""

import re

_IGNORED_PREFIXES = frozenset({"var", "local", "data", "module", "path"})

# Matches: some_resource_type.instance_name.attribute
_REF_PATTERN = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\.(\w+)\.(\w+)")

_HCL_BLOCK_PATTERN = re.compile(r"```(?:hcl|terraform)\n(.*?)```", re.DOTALL)


def extract_relations_from_doc(
    resource_name: str,
    doc_content: str,
    provider_prefix: str,
) -> list[tuple[str, str, str, str]]:
    """
    Parse les blocs ```hcl ... ``` du doc_content pour détecter les
    références croisées entre ressources Terraform.

    Retourne une liste dédupliquée de tuples :
        (source, target, 'REFERENCES', 'doc')
    """
    relations: set[tuple[str, str, str, str]] = set()

    for block_match in _HCL_BLOCK_PATTERN.finditer(doc_content):
        block = block_match.group(1)

        for ref_match in _REF_PATTERN.finditer(block):
            first_token = ref_match.group(1).split("_")[0]

            # Ignorer les préfixes réservés HCL
            if first_token in _IGNORED_PREFIXES:
                continue

            target = ref_match.group(1)

            # Garder seulement les références au bon provider
            if not target.startswith(provider_prefix):
                continue

            # Ignorer les auto-références
            if target == resource_name:
                continue

            relations.add((resource_name, target, "REFERENCES", "doc"))

    return list(relations)
