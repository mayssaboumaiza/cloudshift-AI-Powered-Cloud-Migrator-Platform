# Journal de bord — Session corrections post-audit CloudShift

Date : 2026-06-27 · Périmètre : 4 objectifs (vérifications, corrections .tex, architecture, fiches jury).
Méthode : lecture avant édition, `str_replace` uniquement, grep de vérification après chaque lot, compilation pdflatex finale.

---

## OBJECTIF 1 — Vérifications (verdicts chiffrés)

| Réf | Sujet | Verdict final |
|---|---|---|
| V1 | Nekrasov 83,2 % | **Table 16 (GR-LLMSum)** = TV 83,2 / overall 62,6 ; **Table 13** = Base/Naive/GraphRAG 37,2-70,2-80,3 / 27,1-52,5-58,4. Rapport mélangeait 2 tables → corrigé en tableau 2 niveaux |
| V2 | Tests par fichier | audit_api=27 ✅ · auth=**19** (≠20) · no_network=**15** (≠14) · rag=**56** (≠57) · sse_schema=25 · **661 cas** confirmé |
| V3 | SecurityPolicyEngine | **13 types** (≠50) |
| V4 | Azure Monitor | **RÉEL** (`MonitorManagementClient`, `azure-mgmt-monitor>=6.0.0`) — aucune correction |
| V5 | Python | **3.12** (Dockerfile) |
| V6 | Réseaux Docker | **internal_net / external_net** (≠ migrator_network) |
| V7 | Provider azurerm | harmonisé à **~> 3.99** |
| V8 | Pipelines agents | I/O réels documentés → utilisés en OBJ3 |

## OBJECTIF 2 — Corrections .tex appliquées

**chp1** : durée stage « 16 semaines (≈4 mois) » + découpage 3+3+3+4+3=16 · IBM+Txture → IBM watsonx · « La case rouge » → « Le bloc rouge » · « aucun outil » → « aucune des solutions majeures étudiées » · 7R « formalisé par AWS, repris dans l'industrie ».

**chp2** : GPT-4.1 → famille GPT-5 (tableau 2.1, compromis, synthèse, conclusion) · tableau 2.3 en **2 niveaux** (T13+T16) · 19,36 % vs 37,2 % explicité (benchmarks distincts) · +131 % requalifié « taux de succès global » + astérisque preprint + label badge corrigé (vs génération directe) · GraphRAG distingué de Microsoft · « AES brut bit-flipping » → « sans authentification de message » · figure Vault AES-256 → « Secrets JIT (KV v2) » · « tableau 2.6 » → `\ref`.

**chp3** : 33→**34** US · 78→**74** j-h · « vingt exigences mesurables » → « (RF-01 à RF-20) … vérifiables (RNF-01 à RNF-08) … 34 US » · ajout phrase mapping RF-01..20 · CU-03 « Gérer utilisateurs » → « Inviter un utilisateur » (section, figure) · rate limit 5/min → « 5 à 10/min selon endpoint ».

**chp4** : GPT-4.1 → **GPT-5.1** (Agent 01) et **GPT-5.4-mini** (Agent 03) · équation Score : ajout des **variables** (×équivalence…) · « 50 types » → **13 types**.

**chp5** : 3 fichiers tests fantômes corrigés (`test_agent01_tools`→`test_7r_confidence,test_hybrid_scoring` ; `test_sse_contract`→`test_sse_schema` ; `test_api_credentials`→`test_vault_store,test_vault_helpers`) · GPT-4.1 → GPT-5.1/5.4-mini · Python 3.11→**3.12** · 57→**56** tests RAG (et 78−22, 28 %) · « 20 tests »→« 19 cas » · typo « l'ingénieurDevOps » → « Ingénieur DevOps » · no_network 14→**15** · RNF-01 exigence/mesuré clarifié · 66,05 s annoté.

**Annexes** : λmax 5,099→**5,0783** et calcul 0,0783/4,48 (ANX-1 critique) · gpt-4.1→gpt-5.1 · migrator_network → internal_net/external_net · azurerm ~>3.90 → ~>3.99.

**résumé** : « un »→« trois » scénarios E2E + « 661 tests » (FR + EN).
**intro** : description chp2 réalignée (retrait « protocoles d'interopérabilité »).
**abréviations** : **A2A retiré** (fantôme) · ajout ACID, API, CI/CD, REST, SDK, UML.
**conclusion** : perspectives ancrées dans le code (IVFFlat→HNSW, re-ranker, tfstate distant, 6 paires).
**webographie** : aws7r URL/titre corrigés.

**Compilation finale** : `pdflatex` exit 0, **0 erreur**, **0 référence/citation non résolue**, PDF 6,26 Mo.

## OBJECTIF 3 — Architecture

Ajout sous-section **« Pipelines de données des agents »** (chp4) : figure TikZ flux inter-agents via `MigrationState` + tableau des entrées/sorties réelles des 3 agents (source : signatures `run_agent_0x`). Référencée depuis l'intro multi-agents. Compile sans erreur.

## OBJECTIF 4 — Fiches jury

`docs/FICHES_JURY.md` : chiffres-clés vérifiés + 8 sections de Q/R (modèle LLM, SAW-AHP, Graph RAG, architecture, sécurité, tests, choix/limites) + checklist de vérifs personnelles avant soutenance.

## Restant à faire manuellement (non automatisable)

- Relancer `pytest --cov` la veille → vrai % couverture
- Vérifier visuellement captures chp5 (aucune ne doit afficher GPT-4.1)
- Confirmer index pgvector (IVFFlat/HNSW), température LLM
- Vérifier que les figures use-case (`diag1.png` etc.) sont les bonnes versions
