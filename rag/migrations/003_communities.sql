-- Migration 003: Community detection results
-- Populated by RAGBuilder._build_communities() via NetworkX Louvain algorithm.
-- community_id is an integer assigned per provider build; community_label is a
-- human-readable prefix (e.g. "aws_s3") derived from the most common resource
-- prefix inside the community.

CREATE TABLE IF NOT EXISTS resource_communities (
    resource_name   TEXT PRIMARY KEY,
    community_id    INTEGER NOT NULL,
    community_label TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rc_community_id  ON resource_communities (community_id);
CREATE INDEX IF NOT EXISTS idx_rc_community_lbl ON resource_communities (community_label);
