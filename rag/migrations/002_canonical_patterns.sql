-- =============================================================================
-- 002_canonical_patterns.sql
-- Adds tf_canonical_patterns — architectural pattern library for Agent 02.
--
-- A canonical pattern bundles 2+ resource types that together solve a common
-- architectural task (e.g. "lambda_with_s3_trigger" = aws_lambda_function +
-- aws_s3_bucket + aws_lambda_permission + aws_s3_bucket_notification). Each
-- pattern carries an HCL template and its own embedding so Agent 02 can
-- retrieve entire patterns by semantic similarity AND by resource overlap.
--
-- Used by the dual GraphRAG query: structural (resource_relations) + semantic
-- (terraform_resources) + pattern (this table).
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tf_canonical_patterns (
    id              SERIAL      PRIMARY KEY,
    provider        TEXT        NOT NULL,                 -- aws | azurerm | google
    pattern_name    TEXT        NOT NULL,                 -- lambda_with_s3_trigger
    description     TEXT        DEFAULT '',
    resource_types  TEXT[]      NOT NULL,                 -- {aws_lambda_function, aws_s3_bucket, ...}
    hcl_template    TEXT        NOT NULL,                 -- full HCL example
    embed_text      TEXT        DEFAULT '',
    embedding       vector(768),                           -- same model as terraform_resources
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_pattern UNIQUE (provider, pattern_name)
);

-- Vector similarity index — matches the ivfflat setup of terraform_resources
-- so query planner behaviour is consistent.
CREATE INDEX IF NOT EXISTS idx_tf_patterns_embedding
    ON tf_canonical_patterns
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- Provider filter — most queries scope patterns to a single provider.
CREATE INDEX IF NOT EXISTS idx_tf_patterns_provider
    ON tf_canonical_patterns (provider);

-- GIN index on resource_types — enables fast "patterns that contain any of
-- these resource types" queries using the && (array overlap) operator.
CREATE INDEX IF NOT EXISTS idx_tf_patterns_resource_types
    ON tf_canonical_patterns
    USING GIN (resource_types);
