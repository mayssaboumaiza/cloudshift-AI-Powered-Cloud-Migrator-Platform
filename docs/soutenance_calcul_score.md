# 🎓 Calcul de Score Agent 01 — Fiche de révision soutenance

> Méthode : **AHP** (poids) + **SAW** (somme pondérée) + couche **hybride** (ontologie/schéma)
> Fichiers source : `core/ahp_weights.py` · `agents/migration_planner/scoring_decision_tools.py`

---

## 1. À quoi sert le score ?

Quand une ressource AWS doit migrer vers Azure, **plusieurs services Azure sont possibles**.
Le score **note chaque candidat** pour choisir le meilleur **automatiquement et objectivement**.

> Exemple : `aws_db_instance` (RDS) → candidats : `azurerm_postgresql_flexible_server`,
> `azurerm_mssql_server`, `azurerm_cosmosdb_account`… **Lequel ? → le score décide.**

Le candidat au meilleur score est retenu → détermine la **stratégie 7R** → Agent 02 génère le Terraform.

---

## 2. Le calcul en 3 étapes

### Étape 1 — Hard Gates (élimination directe)
Avant tout calcul, un candidat est **rejeté (score = 0)** s'il échoue à un critère bloquant :

| Gate | Rejet si… |
|------|-----------|
| Équivalence | `equivalence < 0.50` (pas de correspondance fonctionnelle) |
| Région | service indisponible dans la région cible |
| Dépréciation | `maturity_status == deprecated` |
| Budget | coût > budget × tolérance |
| Maturité | `preview`/`beta` en production |

→ Logique en 2 temps : **éliminer les inacceptables, puis classer les acceptables.**

### Étape 2 — Score SAW (somme pondérée)

```
saw_score =  0.4253 × equivalence
           + 0.2618 × maturity
           + 0.1631 × budget_fit
           + 0.1032 × region_fit
           + 0.0466 × (1 − complexity)   ← complexité INVERSÉE
```

| Critère | Poids | Valeur vient de | Note |
|---------|-------|-----------------|------|
| equivalence | 42.5% | similarité cosinus (pgvector) | critère décisif |
| maturity | 26.2% | analyse commits GitHub | fiabilité |
| budget_fit | 16.3% | coût vs budget (gradient continu) | |
| region_fit | 10.3% | dispo région (1.0 / 0.60 voisin / 0) | |
| complexity | 4.7% | graphe Neo4j (LOW/MED/HIGH) | **inversée** |

> ⚠️ **Complexité inversée** : faible complexité = bon = score élevé (`1 − complexity`).

### Étape 3 — Score hybride (si ontologie disponible)

```
final = 0.70 × saw_score + 0.20 × ontology_score + 0.10 × schema_similarity
```

- **70%** SAW (les 5 critères)
- **20%** ontologie (correspondances AWS↔Azure curées, table `cloud_service_ontology`)
- **10%** similarité de schéma (arguments requis partagés)

> Si aucune correspondance d'ontologie n'existe pour la paire → **score = SAW seul**.

---

## 3. AHP — d'où viennent les poids ?

**On compare les 5 CRITÈRES deux par deux** (pas les services !).
Question : *« le critère X est-il plus important que Y, et combien de fois ? »* (échelle Saaty 1-9).

### La matrice de comparaison (ton code)
```
                equiv  matur  budget  region  complex
equivalence  [   1     2      3       4       7   ]
maturity     [  1/2    1      2       3       5   ]
budget_fit   [  1/3   1/2     1       2       4   ]
region_fit   [  1/4   1/3    1/2      1       3   ]
complexity   [  1/7   1/5    1/4     1/3      1   ]
```
Lecture : `equivalence vs budget = 3` → « l'équivalence est 3× plus importante que le budget ».

### Justification de chaque comparaison (source dans le code)
| Comparaison | Valeur | Justification |
|-------------|--------|---------------|
| equiv vs maturity | 2 | AWS MAP / Gartner : le fit fonctionnel filtre avant la maturité |
| equiv vs budget | 3 | FinOps : le coût est une validation post-sélection |
| equiv vs region | 4 | RGPD Art.44-49 : la région est secondaire au fit fonctionnel |
| equiv vs complexity | 7 | Agile : l'effort est un facteur de planning, pas une barrière |
| maturity vs budget | 2 | HashiCorp Tiers : un provider obsolète = risque > économie |
| maturity vs region | 3 | la maturité a un impact production plus large |
| maturity vs complexity | 5 | — |
| budget vs region | 2 | FinOps : l'optimisation coût précède le fit géographique |
| budget vs complexity | 4 | — |
| region vs complexity | 3 | RGPD : contrainte légale > effort technique |

### Dérivation des poids (formule)
```python
normalised = MATRIX / MATRIX.sum(axis=0)   # normaliser chaque colonne
weights    = normalised.mean(axis=1)        # moyenne de chaque ligne
```
Résultat : `{equivalence: 0.4253, maturity: 0.2618, budget_fit: 0.1631, region_fit: 0.1032, complexity: 0.0466}` (somme = 1.0)

---

## 4. Le Consistency Ratio (CR)

Vérifie que les comparaisons **ne se contredisent pas** (transitivité : si A>B et B>C alors A>C).

```
λ_max = 5.0783                  (valeur propre principale)
CI    = (λ_max − n)/(n−1) = 0.0196
RI    = 1.12                    (indice aléatoire Saaty, n=5)
CR    = CI / RI = 0.0175
```

**Règle de Saaty : CR < 0.10 → cohérent.** Mon CR = **0.0175** → très cohérent. ✅

---

## 5. Flux complet
```
Ressource AWS
   ↓
Candidats Azure  →  ❌ Hard gates (élimine les mauvais → score 0)
   ↓
✅ valides  →  SAW (somme pondérée AHP)
   ↓
+ couche hybride (70% SAW / 20% ontologie / 10% schéma)
   ↓
Meilleur score gagne
   ↓
Stratégie 7R  →  Agent 02 génère le Terraform
```

---

## 6. Q/R du jury

**Q1 — Pourquoi AHP et pas des poids à la main ?**
> Les poids manuels sont arbitraires et injustifiables. AHP part de comparaisons simples
> entre 2 critères et en dérive les poids mathématiquement, avec vérification de cohérence (CR).
> Méthode reconnue (Saaty 1980).

**Q2 — C'est quoi le CR ?**
> Un indicateur qui vérifie que mes comparaisons ne se contredisent pas (transitivité).
> Si CR < 0.10 → cohérent. Le mien = 0.0175. Formule : CR = CI/RI.

**Q2bis — On compare quoi exactement ?**
> Les 5 critères de décision entre eux, deux par deux (10 paires), sur l'échelle Saaty 1-9.
> Pas les services — les critères.

**Q3 — Pourquoi équivalence 42% et complexité 5% ?**
> L'équivalence est le critère décisif (sans fit fonctionnel, la migration n'a pas de sens).
> La complexité n'est qu'un facteur d'effort. Chaque poids est justifié par une source
> (Gartner, FinOps, RGPD).

**Q4 — Si un service est trop cher / indisponible ?**
> Filtres bloquants (hard gates) avant le score : le candidat est éliminé directement (score 0).
> Le calcul pondéré ne s'applique qu'aux candidats valides.

**Q5 — Pourquoi inverser la complexité ?**
> Une faible complexité est positive. En l'inversant (1 − complexity), une migration simple
> obtient un score élevé.

**Q6 — D'où viennent les valeurs des critères ?**
> equivalence = cosinus pgvector · maturity = commits GitHub · budget = coût vs budget ·
> region = dispo réelle · complexity = graphe Neo4j. Aucune n'est inventée par le LLM.

**Q7 (piège) — Le score dépend-il du LLM ? Reproductible ?**
> Non. Le score est déterministe (formule mathématique). Le LLM propose les candidats,
> mais le classement repose sur AHP+SAW. Mêmes entrées → même score. Auditable.

---

## 7. À retenir (3 mots-clés)
| Mot | Sens simple |
|-----|-------------|
| **AHP** | calcule les poids scientifiquement |
| **SAW** | la somme pondérée (la note) |
| **CR < 0.10** | preuve que les choix sont cohérents |

**Phrase de conclusion :**
> « AHP calcule les poids de façon rigoureuse, SAW combine les critères en une note,
> le meilleur candidat est retenu. Tout est traçable et justifié par des sources. »
