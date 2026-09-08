-- =============================================================================
-- 001_init_pgvector.sql
-- Initial schema for the Terraform RAG corpus: three PostgreSQL tables
-- + pgvector index.
--
-- Prerequisite: PostgreSQL 14+ with the pgvector extension installed.
--   docker : pgvector/pgvector:pg16
--   bare   : CREATE EXTENSION vector (package postgresql-16-pgvector)
-- =============================================================================

-- Activer pgvector (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- TABLE : terraform_resources
-- One row per documented Terraform resource (e.g. aws_s3_bucket).
--
-- Choices:
--   • id TEXT PRIMARY KEY  → resource_name (canonical, stable)
--   • embedding vector(768) → fixed dimension from all-mpnet-base-v2
--   • required_args / optional_args / blocks in JSONB → native queries
--   • updated_at → detect updates without full re-fetch
-- =============================================================================
CREATE TABLE IF NOT EXISTS terraform_resources (
    id            TEXT        PRIMARY KEY,          -- ex: aws_s3_bucket
    provider      TEXT        NOT NULL,             -- aws | azurerm | google
    description   TEXT        DEFAULT '',
    required_args JSONB       DEFAULT '[]'::JSONB,  -- [{name, description}, ...]
    optional_args JSONB       DEFAULT '[]'::JSONB,  -- [{name}, ...]
    blocks        JSONB       DEFAULT '[]'::JSONB,  -- [{name, args:[...]}, ...]
    example       TEXT        DEFAULT '',           -- HCL example extrait du .md
    file_sha      TEXT        DEFAULT '',           -- SHA du fichier GitHub (cache)
    embed_text    TEXT        DEFAULT '',           -- texte soumis à l'encodeur
    embedding     vector(768),                      -- vecteur all-mpnet-base-v2
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Index de similarité vectorielle (cosinus, opérateur <=>).
-- ivfflat : index approximatif, lecture rapide sur grands corpus.
--   lists = 100 → bon compromis recall/vitesse pour ~5 000–50 000 lignes.
--   Si le corpus < 1 000 lignes, la recherche séquentielle est aussi rapide.
-- Note : pgvector 0.5+ supporte aussi HNSW (CREATE INDEX ... USING hnsw).
CREATE INDEX IF NOT EXISTS idx_terraform_resources_embedding
    ON terraform_resources
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Filtre par provider (utilisé dans toutes les requêtes WHERE provider = ?)
CREATE INDEX IF NOT EXISTS idx_terraform_resources_provider
    ON terraform_resources (provider);

-- =============================================================================
-- TABLE : resource_relations
-- Remplace le DiGraph NetworkX sauvegardé dans terraform_graph.json.
-- Chaque ligne = une arête orientée entre deux ressources Terraform.
--
-- Types de relation :
--   COMPANION   → co-requis selon Checkov (défini dans _COMPANIONS)
--   RELATED_TO  → même famille (même préfixe à 3 tokens)
--   DEPENDS_ON  → dépendance Terraform réelle (issue de `terraform graph`)
--   BELONGS_TO  → resource → provider (meta-nœuds du graphe d'origine)
--
-- Choix :
--   • UNIQUE(source, target, relation) → idempotent avec ON CONFLICT DO NOTHING
--   • source_type distingue l'origine de l'arête (builder vs tfgraph)
--     mais n'entre pas dans la contrainte d'unicité (une même arête logique
--     ne doit exister qu'une fois, quelle que soit sa source).
-- =============================================================================
CREATE TABLE IF NOT EXISTS resource_relations (
    id          SERIAL      PRIMARY KEY,
    source      TEXT        NOT NULL,   -- resource_name source (normalisé)
    target      TEXT        NOT NULL,   -- resource_name cible  (normalisé)
    relation    TEXT        NOT NULL,   -- COMPANION | RELATED_TO | DEPENDS_ON | BELONGS_TO
    source_type TEXT        DEFAULT 'builder', -- 'builder' ou 'tfgraph'
    created_at  TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_relation UNIQUE (source, target, relation)
);

-- Traversée sortante : tous les voisins d'un nœud source (cas le plus fréquent)
CREATE INDEX IF NOT EXISTS idx_resource_relations_source
    ON resource_relations (source);

-- Traversée entrante : qui pointe vers un nœud cible (reverse lookup)
CREATE INDEX IF NOT EXISTS idx_resource_relations_target
    ON resource_relations (target);

-- Traversée filtrée par type de relation (ex: WHERE relation = 'COMPANION')
CREATE INDEX IF NOT EXISTS idx_resource_relations_source_relation
    ON resource_relations (source, relation);

-- =============================================================================
-- TABLE : rag_commits
-- Remplace last_commits.json dans rag/data/.
-- Stocke le dernier SHA de commit GitHub connu par provider.
--
-- Choix :
--   • provider TEXT PRIMARY KEY → une ligne par provider (aws / azurerm / google)
--   • ON CONFLICT ... DO UPDATE → upsert natif, pas de double insert
--   • updated_at → traçabilité de la dernière synchronisation
-- =============================================================================
CREATE TABLE IF NOT EXISTS rag_commits (
    provider   TEXT        PRIMARY KEY,   -- aws | azurerm | google
    sha        TEXT        NOT NULL,      -- SHA du dernier commit indexé
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
