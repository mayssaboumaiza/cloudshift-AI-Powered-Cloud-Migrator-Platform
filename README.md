# AI-Powered Cloud Migrator

Plateforme intelligente de migration inter-cloud basée sur une architecture multi-agents LangGraph. Analyse automatiquement un dépôt GitHub, planifie la migration selon le framework **7R**, génère le code **Terraform** cible, le valide et orchestre le déploiement — le tout avec supervision humaine en temps réel via SSE.

**Clouds supportés :** AWS → Azure, AWS → GCP, Azure → GCP (et inversement)

---

## Architecture

```
Frontend (React/Vite :3000)
  → REST + SSE → FastAPI (:8000)
    → LangGraph Pipeline (11 nœuds)
        1. analyze_repo     — Stack Analyzer : HCL/Bicep/ARM/Helm/CFN, graphe de dépendances
        2. cooldown
        3. build_plan       — Agent 01 : ReAct max 3 iter, 7R taxonomy, Azure GPT-4o
        4. check_plan
        5. ask_human        — SSE → approbation humaine du plan
        6. generate_iac     — Agent 02 : ReAct manuel, Graph RAG, génération Terraform
        7. validate_intent  — vérification déterministe région + budget
        8. validate_iac     — MCP IaC service (terraform validate + Checkov)
        9. fix_targeted     — Agent 02 boucle de correction (max 3 par module)
       10. deploy           — Agent 03 : deploy.sh + CI/CD YAML via Jinja2
       11. export_zip
      → PostgreSQL 16 + pgvector  (état LangGraph + embeddings Graph RAG)
      → MCP IaC service (:8001)   (terraform fmt/validate/plan/apply + Checkov)
```

---

## Prérequis

- Python 3.11+
- Docker & Docker Compose
- Token GitHub (Classic) avec permissions `repo`
- Endpoint Azure OpenAI (GPT-4o)
- PostgreSQL 16 avec extension `pgvector`

---

## Installation

### 1. Variables d'environnement

```bash
cp .env.example .env
```

Remplir dans `.env` :

```env
GITHUB_TOKEN=ghp_...
AZURE_AI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_AI_API_KEY=...
AZURE_MODEL=gpt-4o
AZURE_OPENAI_API_VERSION=2025-01-01-preview
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost        # Docker : "postgres"
DB_PORT=5432
DB_NAME=cloud_migrator
```

### 2. Lancement complet (Docker Compose)

```bash
docker-compose up -d --build
```

Lance : FastAPI (:8000), Frontend (:3000), PostgreSQL + pgvector, MCP IaC service (:8001).

### 3. Lancement en développement

```bash
# Backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app:app --host localhost --port 8000 --reload

# Frontend
cd frontend
npm install
npm run dev   # http://localhost:3000
```

---

## Migrations base de données

```bash
alembic upgrade head                          # Appliquer toutes les migrations
alembic revision --autogenerate -m "desc"     # Créer une migration
alembic downgrade -1                          # Rollback d'une version
```

---

## Tests

```bash
# Tests unitaires (sans PostgreSQL)
pytest service/tests/ -v --ignore=service/tests/test_graph.py -x --tb=short

# Par marqueur
pytest -m agent02 -v
pytest -m integration -v

# Tests d'intégration (requiert PostgreSQL)
export DB_NAME=cloud_migrator_test
alembic upgrade head
pytest service/tests/test_integration.py -v
```

Marqueurs pytest disponibles : `slow`, `agent02`, `agent03`, `agent04`, `agent05`, `integration`

---

## Linting

```bash
ruff check service/ api/ configuration/ core/ data/ agents/
ruff format service/ api/ configuration/ core/ data/ agents/
```

---

## Variables d'environnement optionnelles

| Variable | Défaut | Description |
|----------|--------|-------------|
| `API_KEY` | — | Secret pour l'en-tête `X-API-Key` |
| `FERNET_KEY` | — | Chiffrement des tokens GitHub (auto-généré si absent) |
| `CORS_ORIGINS` | — | Origines CORS autorisées (séparées par virgule) |
| `COOLDOWN_SECONDS` | `5` | Pause entre analyze_repo et build_plan |
| `AZURE_MAX_TOKENS` | `4096` | Max tokens par requête GPT-4o |
| `AZURE_REQUEST_TIMEOUT` | `180` | Timeout HTTP requête LLM (secondes) |
| `AZURE_MODEL_01` | `gpt-4o` | Modèle Agent 01 |
| `AZURE_MODEL_02` | `gpt-4o` | Modèle Agent 02 |
| `AZURE_MODEL_03` | `gpt-4o-mini` | Modèle Agent 03 |
| `MIGRATION_OUTPUT_DIR` | `output/` | Répertoire de sortie Terraform |
| `IAC_MAX_FILES` | `200` | Limite de fichiers IaC par catégorie |
| `DEBUG_AGENT` | — | Mettre `1` pour dumper `agent_02/03_messages_debug.json` |

---

## Événements SSE

| Événement | Déclencheur |
|-----------|-------------|
| `service_selection_required` | Aucune IaC détectée → sélection manuelle |
| `human_input_required` | Nœud `ask_human` — approbation du plan |
| `progress` | Chaque transition de nœud LangGraph |
| `complete` / `error` | Fin du pipeline |

---

## Sécurité

- Les tokens GitHub sont chiffrés avec Fernet avant stockage en base.
- Aucun secret n'est loggué (masquage automatique).
- Les agents reçoivent uniquement les résultats d'analyse, jamais les clés d'accès directement.
- `API_KEY` optionnel pour sécuriser tous les endpoints (sauf `/health` et `/metrics`).

---

## Structure du projet

```
app.py                      # FastAPI entry point
api/                        # Routes, schemas, SSE middleware
agents/
  pipeline_graph.py         # Workflow LangGraph (11 nœuds)
  pipeline_state.py         # TypedDict état partagé
  analyzer/                 # Stack Analyzer (zéro LLM)
  migration_planner/        # Agent 01 — planification 7R
  iac_generator/            # Agent 02 — génération Terraform
  deployer/                 # Agent 03 — deploy.sh + CI/CD
  nodes/                    # Fonctions nœuds LangGraph
rag/                        # Graph RAG (pgvector)
configuration/              # Settings Pydantic, DB async
core/                       # Constantes, enums, crypto Fernet
data/                       # Modèles SQLAlchemy, repositories
alembic/versions/           # 6 fichiers de migration
frontend/src/               # React/Vite
output/                     # Terraform généré par migration
```
