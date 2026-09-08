"""
builder.py — Build and maintain the Terraform RAG corpus in PostgreSQL.

Public API:
    builder = RAGBuilder(github_token="...")
    builder.build_all(force=False)    → int (total resources indexed)
    builder._build_provider(provider) → dict[name, TerraformResource]

Persistence:
    • INSERT INTO terraform_resources
    • _build_graph_relations() populates resource_relations
    • ON CONFLICT DO NOTHING / DO UPDATE for full idempotency
"""

import gc
import json
import logging
import time

import psycopg2.extras

from rag.rag_config import (
    EMBEDDING_MODEL,
    PROVIDERS,
    get_pg_connection,
)
from rag.github_doc_fetcher import GitHubFetcher
from rag.terraform_doc_parser import TerraformDocParser, TerraformResource
from rag import tf_relation_parser, doc_relation_parser
from rag.companions import COMPANIONS as _COMPANIONS
from core.embedding_model_loader import init_sentence_transformer

logger = logging.getLogger("RAGBuilder")


def _community_label(members: list[str]) -> str:
    """Heuristic: most common 3-token prefix among community members."""
    from collections import Counter
    prefixes = []
    for m in members:
        parts = m.split("_")
        prefixes.append("_".join(parts[:3]) if len(parts) >= 3 else m)
    return Counter(prefixes).most_common(1)[0][0] if prefixes else "unknown"




class RAGBuilder:
    """Construit et maintient la base RAG PostgreSQL pour le Graph RAG."""

    def __init__(self, github_token: str = None):
        self.fetcher = GitHubFetcher(token=github_token)
        self.parser = TerraformDocParser()
        self.embedder = init_sentence_transformer(EMBEDDING_MODEL, logger=logger)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def build_all(self, force: bool = False) -> int:
        """
        Construit la base RAG complète pour les 3 providers.
        Si force=False, skip les providers déjà à jour (vérifié via rag_commits).
        Retourne le nombre total de ressources indexées.
        """
        total = 0
        for provider in PROVIDERS:
            print(f"\n[{provider}] Checking for updates...")
            needs_update, latest_sha = self._needs_update(provider)

            if not needs_update and not force:
                print(f"[{provider}] Already up to date (SHA: {latest_sha[:8]})")
                total += self._count_provider(provider)
                continue

            print(f"[{provider}] Fetching docs (SHA: {latest_sha[:8]})...")
            resources = self._build_provider(provider)
            self._build_graph_relations(provider, resources)
            self._build_communities(provider, resources)
            self._build_argument_nodes(provider, resources)
            self._save_commit(provider, latest_sha)
            total += len(resources)
            print(f"[{provider}] Done: {len(resources)} resources indexed")

        print(f"\nRAG build complete: {total} resources total")

        # Sync to Neo4j (if available) — mirrors the PG graph for Cypher traversal
        self._sync_neo4j()

        return total

    def _sync_neo4j(self):
        """Sync the built graph from PostgreSQL to Neo4j (best-effort, non-blocking)."""
        try:
            from rag.rag_config import neo4j_available
            if not neo4j_available():
                print("\nNeo4j not reachable — skipping sync (SQL fallback will be used)")
                return
            from rag.neo4j_sync import Neo4jSync
            print("\nSyncing graph to Neo4j...")
            sync = Neo4jSync()
            result = sync.sync_all()
            sync.close()
            print(f"Neo4j sync complete: {result}")
        except Exception as e:
            print(f"Neo4j sync failed (non-fatal): {e}")

    def rebuild_argument_nodes_from_db(self) -> int:
        """Repopulate tf_arguments from data already in terraform_resources (no GitHub download).

        Faster alternative to force=True when tf_arguments is incomplete but
        terraform_resources is already fully indexed. Uses required_args (name+description)
        and optional_args (name only, max 5) stored in the DB.
        Returns total argument nodes upserted.
        """
        from rag.terraform_doc_parser import TerraformArgument

        total = 0
        for provider in PROVIDERS:
            print(f"\n[{provider}] Loading resources from DB...")
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, required_args, optional_args FROM terraform_resources WHERE provider = %s",
                        (provider,),
                    )
                    rows = cur.fetchall()

            resources = {}
            for rid, req_json, opt_json in rows:
                req_list = req_json if isinstance(req_json, list) else []
                opt_list = opt_json if isinstance(opt_json, list) else []

                class _Resource:
                    pass

                r = _Resource()
                r.resource_name = rid
                r.required_args = [
                    TerraformArgument(
                        name=a.get("name", ""),
                        required=True,
                        description=a.get("description", ""),
                        type=a.get("type", ""),
                    )
                    for a in req_list if a.get("name")
                ]
                r.optional_args = [
                    TerraformArgument(
                        name=a.get("name", ""),
                        required=False,
                        description=a.get("description", ""),
                        type=a.get("type", ""),
                    )
                    for a in opt_list if a.get("name")
                ]
                resources[rid] = r

            print(f"[{provider}] {len(resources)} resources loaded, building argument nodes...")
            self._build_argument_nodes(provider, resources)
            self._build_communities(provider, resources)
            total += len(resources)

        print(f"\nRebuild complete: {total} resources processed")
        return total

    def _build_provider(self, provider: str) -> dict:
        """Télécharge, parse et indexe tous les docs d'un provider."""
        print("  Listing files...")
        files = self.fetcher.list_doc_files(provider)
        print(f"  Found {len(files)} doc files")

        resources: dict[str, TerraformResource] = {}
        batch_docs: list[str] = []
        batch_resources: list[TerraformResource] = []
        batch_size = 32

        already_indexed = self._get_existing_ids(provider)
        skipped = 0
        indexed_count = 0
        total = len(files)

        for i, file_info in enumerate(files):
            try:
                content = self.fetcher.download_file_content(file_info["download_url"])
                resource = self.parser.parse(
                    content=content,
                    filename=file_info["name"],
                    provider=provider,
                    file_sha=file_info["sha"],
                )
                resource.raw_content = content  # used by tf/doc relation parsers
                resources[resource.resource_name] = resource

                if resource.resource_name in already_indexed:
                    skipped += 1
                    continue

                batch_docs.append(self._build_embed_text(resource))
                batch_resources.append(resource)

                if len(batch_docs) >= batch_size:
                    inserted = self._insert_batch(batch_docs, batch_resources)
                    indexed_count += inserted
                    batch_docs, batch_resources = [], []
                    print(f"  Indexed {i + 1}/{total} files... ({indexed_count} inserted)")
                    gc.collect()

                time.sleep(0.05)

            except Exception as e:
                print(f"  Warning: skip {file_info['name']}: {e}")
                continue

        # Flush du dernier batch
        if batch_docs:
            inserted = self._insert_batch(batch_docs, batch_resources)
            indexed_count += inserted

        if skipped:
            print(f"  Skipped {skipped} already-indexed resources (resume mode)")

        return resources

    # -------------------------------------------------------------------------
    # Graph relations — 3 passes après chaque provider
    # -------------------------------------------------------------------------

    def _build_communities(self, provider: str, resources: dict) -> None:
        """Detect resource communities using NetworkX Louvain and persist to resource_communities."""
        try:
            import networkx as nx
            from networkx.algorithms.community import louvain_communities
        except ImportError:
            print(f"  [communities] networkx not available — skipping")
            return

        resource_ids = list(resources.keys())
        if len(resource_ids) < 3:
            return

        G = nx.Graph()
        G.add_nodes_from(resource_ids)

        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT source, target FROM resource_relations
                        WHERE source = ANY(%s) AND target = ANY(%s)
                          AND relation IN ('COMPANION', 'DEPENDS_ON', 'REFERENCES', 'RELATED_TO')
                        """,
                        (resource_ids, resource_ids),
                    )
                    for source, target in cur.fetchall():
                        G.add_edge(source, target)
        except Exception as e:
            print(f"  [communities] DB query failed: {e}")
            return

        try:
            communities = louvain_communities(G, seed=42)
        except Exception as e:
            print(f"  [communities] Louvain failed: {e}")
            return

        provider_offset = list(PROVIDERS.keys()).index(provider) * 10000
        rows: list[tuple] = []
        for community_id, members in enumerate(communities):
            label = _community_label(list(members))
            scoped_id = provider_offset + community_id
            for member in members:
                rows.append((member, scoped_id, label))

        if not rows:
            return

        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO resource_communities (resource_name, community_id, community_label)
                        VALUES %s
                        ON CONFLICT (resource_name) DO UPDATE SET
                            community_id    = EXCLUDED.community_id,
                            community_label = EXCLUDED.community_label,
                            updated_at      = NOW()
                        """,
                        rows,
                    )
                conn.commit()
            print(f"  Communities: {len(communities)} communities, {len(rows)} resources ({provider})")
        except Exception as e:
            print(f"  [communities] Insert failed: {e}")

    def _build_graph_relations(self, provider: str, resources: dict) -> None:
        """
        Construit les arêtes du graphe dans resource_relations — 4 passes :

        PASSE 1 — tf_relation_parser  : REFERENCES depuis blocs HCL (source principale)
        PASSE 2 — doc_relation_parser : DOC_MENTIONS depuis texte libre (fallback niv. 2)
        PASSE 3 — _COMPANIONS statique: COMPANION (fallback niv. 3, si 1+2 vides)
        PASSE 4 — RELATED_TO auto     : même préfixe 3 tokens (s'applique à tous)
        """
        resource_ids = list(resources.keys())
        provider_prefix = PROVIDERS[provider]["resource_prefix"]

        # Edges collectés avant insertion : (source, target, relation, source_type)
        all_edges: list[tuple[str, str, str, str]] = []

        # BELONGS_TO conservé pour les stats provider
        for rid in resource_ids:
            all_edges.append((rid, f"__provider__{provider}", "BELONGS_TO", "builder"))

        # ── PASSE 1 : tf_relation_parser ──────────────────────────────────────
        has_p1: set[str] = set()
        for resource in resources.values():
            raw = getattr(resource, "raw_content", "")
            rels = tf_relation_parser.extract_relations_from_doc(
                resource_name=resource.resource_name,
                doc_content=raw,
                provider_prefix=provider_prefix,
            )
            if rels:
                has_p1.add(resource.resource_name)
                all_edges.extend(rels)

        # ── PASSE 2 : doc_relation_parser (fallback si passe 1 vide) ──────────
        has_p12: set[str] = set(has_p1)
        for resource in resources.values():
            if resource.resource_name in has_p1:
                continue
            raw = getattr(resource, "raw_content", "")
            rels = doc_relation_parser.extract_relations_from_text(
                resource_name=resource.resource_name,
                doc_content=raw,
                provider_prefix=provider_prefix,
            )
            if rels:
                has_p12.add(resource.resource_name)
                all_edges.extend(rels)

        # ── PASSE 3 : _COMPANIONS statique — toujours inséré (pas seulement fallback)
        # graph_rag_query() cherche des edges COMPANION en DB pour les "required_dependencies"
        # de Agent 02. Sans ces edges, le signal structurel est absent même si les passes
        # 1+2 ont produit des REFERENCES pour ces ressources.
        for source, companions in _COMPANIONS.items():
            if source not in resources:
                continue
            for companion in companions:
                all_edges.append((source, companion, "COMPANION", "builder"))

        # ── PASSE 4 : RELATED_TO auto — même préfixe 3 tokens ─────────────────
        prefix_map: dict[str, list[str]] = {}
        for rid in resource_ids:
            parts = rid.split("_")
            prefix = "_".join(parts[:3]) if len(parts) >= 3 else rid
            prefix_map.setdefault(prefix, []).append(rid)

        for siblings in prefix_map.values():
            for i, a in enumerate(siblings):
                for b in siblings[i + 1:]:
                    all_edges.append((a, b, "RELATED_TO", "builder"))
                    all_edges.append((b, a, "RELATED_TO", "builder"))

        if not all_edges:
            return

        unique_edges = list(set(all_edges))

        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO resource_relations (source, target, relation, source_type)
                        VALUES %s
                        ON CONFLICT (source, target, relation) DO NOTHING
                        """,
                        unique_edges,
                    )
        except Exception as e:
            print(f"  [graph] relation insert failed: {e}")
            return

        p1_count = sum(1 for e in unique_edges if e[2] == "REFERENCES")
        p2_count = sum(1 for e in unique_edges if e[2] == "DOC_MENTIONS")
        p3_count = sum(1 for e in unique_edges if e[2] == "COMPANION")
        p4_count = sum(1 for e in unique_edges if e[2] == "RELATED_TO")
        print(
            f"  Graph: {len(unique_edges)} edges for {provider} "
            f"(REFERENCES={p1_count}, DOC_MENTIONS={p2_count}, "
            f"COMPANION={p3_count}, RELATED_TO={p4_count})"
        )

    # -------------------------------------------------------------------------
    # Argument-level nodes (Nekrasov et al. 2025 — Graph RAG)
    # -------------------------------------------------------------------------

    def _build_argument_nodes(self, provider: str, resources: dict) -> None:
        """Populate tf_arguments with argument-level embeddings.

        For each (resource, argument) pair we generate a focused embedding of:
            "{resource_name}.{arg_name}: {description} (type: {arg_type}, required: {is_req})"
        so that Agent 02 can retrieve at argument granularity rather than whole-resource chunks.
        After inserting argument nodes we insert HAS_ARGUMENT and IS_REQUIRED edges.

        Table tf_arguments must exist (created by Alembic migration 004).
        If it doesn't exist, this method silently skips.
        """
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM tf_arguments LIMIT 1")
        except Exception:
            print(f"  [args] tf_arguments table not found — run migration 004 first, skipping")
            return

        batch_texts: list[str] = []
        batch_rows: list[tuple] = []   # (id, resource_id, arg_name, arg_type, description, is_required)
        batch_size = 64
        arg_edges: list[tuple] = []    # (source, target, relation)
        total_inserted = 0

        def flush_batch(texts: list, rows: list) -> int:
            if not texts:
                return 0
            embeddings = self.embedder.encode(
                texts, batch_size=64, show_progress_bar=False, convert_to_numpy=True
            ).tolist()
            insert_rows = [(*row, emb) for row, emb in zip(rows, embeddings)]
            inserted = 0
            try:
                with get_pg_connection() as conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(
                            cur,
                            """
                            INSERT INTO tf_arguments
                                (id, resource_id, arg_name, arg_type, description, is_required, embedding)
                            VALUES %s
                            ON CONFLICT (resource_id, arg_name) DO UPDATE SET
                                arg_type    = EXCLUDED.arg_type,
                                description = EXCLUDED.description,
                                is_required = EXCLUDED.is_required,
                                embedding   = EXCLUDED.embedding
                            """,
                            insert_rows,
                        )
                        inserted = cur.rowcount
            except Exception as e:
                print(f"  [args] batch insert failed: {e}")
            return inserted

        for resource in resources.values():
            rid = resource.resource_name
            # Collect required + optional args from the resource object
            seen: dict[str, tuple] = {}
            for a in resource.required_args:
                if a.name and a.name not in seen:
                    seen[a.name] = (a.name, getattr(a, "description", ""), getattr(a, "type", ""), True)
            for a in resource.optional_args:
                if a.name and a.name not in seen:
                    seen[a.name] = (a.name, getattr(a, "description", ""), getattr(a, "type", ""), False)
            arg_list = list(seen.values())

            for arg_name, arg_desc, arg_type, is_req in arg_list:
                if not arg_name:
                    continue
                arg_id = f"{rid}.{arg_name}"
                embed_text = (
                    f"{rid}.{arg_name}: {arg_desc} "
                    f"(type: {arg_type or 'any'}, required: {is_req})"
                ).strip()
                batch_texts.append(embed_text)
                batch_rows.append((arg_id, rid, arg_name, arg_type or "", arg_desc or "", is_req))
                arg_edges.append((rid, arg_id, "HAS_ARGUMENT"))

                if len(batch_texts) >= batch_size:
                    total_inserted += flush_batch(batch_texts, batch_rows)
                    batch_texts, batch_rows = [], []

        total_inserted += flush_batch(batch_texts, batch_rows)

        # Insert HAS_ARGUMENT / IS_REQUIRED edges into tf_argument_edges
        if arg_edges:
            try:
                unique_edges = list(set(arg_edges))
                with get_pg_connection() as conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(
                            cur,
                            """
                            INSERT INTO tf_argument_edges (source, target, relation)
                            VALUES %s
                            ON CONFLICT (source, target, relation) DO NOTHING
                            """,
                            unique_edges,
                        )
            except Exception as e:
                print(f"  [args] edge insert failed: {e}")

        print(f"  Argument nodes: {total_inserted} upserted, {len(arg_edges)} edges ({provider})")

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _get_existing_ids(self, provider: str) -> set:
        """Retourne les IDs déjà indexés pour ce provider (pour reprise)."""
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM terraform_resources WHERE provider = %s",
                        (provider,),
                    )
                    return {row[0] for row in cur.fetchall()}
        except Exception:
            return set()

    def _count_provider(self, provider: str) -> int:
        """Compte les ressources indexées pour un provider."""
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM terraform_resources WHERE provider = %s",
                        (provider,),
                    )
                    return cur.fetchone()[0]
        except Exception:
            return 0

    def _needs_update(self, provider: str) -> tuple[bool, str]:
        """
        Compare le SHA GitHub actuel avec le SHA stocké dans rag_commits.
        Fallback sur last_commits.json si la table est vide (premier démarrage).
        """
        latest_sha = self.fetcher.get_latest_commit_sha(provider)
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT sha FROM rag_commits WHERE provider = %s",
                        (provider,),
                    )
                    row = cur.fetchone()
                    known_sha = row[0] if row else ""
        except Exception:
            # Fallback JSON si la table n'existe pas encore
            commits = self.fetcher.load_last_commits()
            known_sha = commits.get(provider, "")

        return (latest_sha != known_sha, latest_sha)

    def _save_commit(self, provider: str, sha: str) -> None:
        """Sauvegarde le SHA dans rag_commits (upsert)."""
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rag_commits (provider, sha, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (provider) DO UPDATE
                        SET sha = EXCLUDED.sha,
                            updated_at = NOW()
                    """,
                    (provider, sha),
                )

    # Cross-cloud equivalence hints injected into embed_text so that pgvector
    # similarity search can bridge service names across providers.
    # Format: resource_id → extra keywords (space-separated, lowercase).
    _SEMANTIC_TAGS: dict[str, str] = {
        # ── AWS ──────────────────────────────────────────────────────────────
        "aws_rds_cluster":           "relational database postgresql mysql aurora managed rds equivalent azure postgresql flexible server",
        "aws_db_instance":           "relational database postgresql mysql rds managed instance equivalent azure postgresql flexible server",
        "aws_rds_cluster_instance":  "relational database postgresql mysql aurora rds node",
        "aws_s3_bucket":             "object storage blob files static assets equivalent azure storage account",
        "aws_lambda_function":       "serverless function compute event-driven equivalent azure functions linux function app",
        "aws_iam_role":              "identity access management role permissions equivalent azure role definition user assigned identity",
        "aws_iam_policy":            "access control policy permissions iam equivalent azure policy definition role definition",
        "aws_dynamodb_table":        "nosql key-value document table equivalent azure cosmosdb cosmos db account",
        "aws_kinesis_stream":        "streaming real-time event data ingestion equivalent azure event hub eventhub namespace",
        "aws_kinesis_firehose_delivery_stream": "streaming data delivery ingestion s3 equivalent azure event hub stream analytics",
        "aws_cognito_user_pool":     "user authentication identity pool oauth oidc equivalent azure active directory b2c aadb2c",
        "aws_elasticache_cluster":   "cache redis memcached in-memory equivalent azure redis cache",
        "aws_sqs_queue":             "message queue decoupled async equivalent azure service bus queue",
        "aws_sns_topic":             "pub sub topic notification message equivalent azure service bus topic event grid",
        "aws_vpc":                   "virtual private cloud network isolated equivalent azure virtual network vnet",
        "aws_subnet":                "subnet network segment equivalent azure subnet virtual network",
        "aws_security_group":        "firewall rules inbound outbound traffic equivalent azure network security group nsg",
        "aws_instance":              "virtual machine ec2 compute server linux equivalent azure linux virtual machine",
        "aws_eks_cluster":           "kubernetes container orchestration k8s equivalent azure kubernetes service aks",
        "aws_ecs_cluster":           "container orchestration fargate equivalent azure container apps aci",
        "aws_cloudwatch_metric_alarm": "monitoring alert metrics observability equivalent azure monitor metric alert",
        "aws_cloudwatch_log_group":  "log storage observability monitoring equivalent azure log analytics workspace",
        "aws_kms_key":               "encryption key management cryptographic cmk equivalent azure key vault key",
        "aws_secretsmanager_secret": "secret credential password api key equivalent azure key vault secret",
        "aws_api_gateway_rest_api":  "api gateway rest http proxy routing equivalent azure api management apim",
        "aws_wafv2_web_acl":         "web application firewall waf security equivalent azure front door waf policy",
        # ── AWS rare / data services ──────────────────────────────────────────
        "aws_neptune_cluster":              "graph database property graph gremlin sparql equivalent azure cosmos db gremlin api",
        "aws_neptune_cluster_instance":     "graph database node property graph equivalent azure cosmos db gremlin",
        "aws_glue_job":                     "etl data pipeline spark serverless data integration equivalent azure data factory pipeline",
        "aws_glue_catalog_database":        "data catalog metadata governance glue equivalent azure purview data catalog synapse",
        "aws_glue_catalog_table":           "data catalog table schema metadata equivalent azure purview synapse analytics",
        "aws_emr_cluster":                  "big data spark hadoop mapreduce cluster managed equivalent azure hdinsight databricks",
        "aws_redshift_cluster":             "data warehouse analytics sql columnar olap equivalent azure synapse analytics dedicated pool",
        "aws_redshift_serverless_namespace": "serverless data warehouse analytics equivalent azure synapse analytics serverless",
        "aws_athena_workgroup":             "serverless sql query analytics presto equivalent azure synapse analytics serverless sql pool",
        "aws_athena_database":              "serverless analytics database glue catalog equivalent azure synapse analytics",
        "aws_opensearch_domain":            "search engine elasticsearch opensearch full-text equivalent azure cognitive search ai search",
        "aws_msk_cluster":                  "managed kafka messaging streaming broker equivalent azure event hubs kafka protocol",
        "aws_msk_serverless_cluster":       "serverless kafka messaging equivalent azure event hubs kafka serverless",
        "aws_sagemaker_endpoint":           "ml inference serving model deployment machine learning equivalent azure ml online endpoint",
        "aws_sagemaker_endpoint_config":    "ml inference config machine learning model equivalent azure ml endpoint deployment",
        "aws_step_functions_state_machine": "workflow orchestration state machine serverless equivalent azure logic apps durable functions",
        "aws_route53_zone":                 "dns zone domain name resolution equivalent azure dns zone",
        "aws_route53_record":               "dns record domain routing equivalent azure dns record set",
        "aws_cloudfront_distribution":      "cdn content delivery network edge equivalent azure front door cdn profile",
        "aws_elasticbeanstalk_environment": "paas platform app hosting managed runtime equivalent azure app service web app",
        "aws_codecommit_repository":        "git version control source code equivalent azure devops repos git",
        "aws_codepipeline":                 "ci cd pipeline deployment automation equivalent azure devops pipelines",
        "aws_codebuild_project":            "build ci continuous integration equivalent azure devops pipelines build",
        "aws_ecr_repository":               "container registry docker image equivalent azure container registry acr",
        "aws_ecs_service":                  "container orchestration service fargate equivalent azure container apps aci",
        "aws_batch_job_queue":              "batch compute hpc job scheduling equivalent azure batch",
        # ── Azure → AWS ──────────────────────────────────────────────────────
        "azurerm_postgresql_flexible_server":  "relational database managed postgresql equivalent aws rds aurora cluster db instance",
        "azurerm_mysql_flexible_server":       "relational database managed mysql equivalent aws rds aurora",
        "azurerm_mssql_server":                "relational database sql server managed equivalent aws rds sql server",
        "azurerm_storage_account":             "object storage blob files static assets equivalent aws s3 bucket",
        "azurerm_linux_function_app":          "serverless function compute event-driven equivalent aws lambda function",
        "azurerm_function_app":                "serverless function compute event-driven equivalent aws lambda function",
        "azurerm_user_assigned_identity":      "managed identity service principal equivalent aws iam role",
        "azurerm_role_definition":             "access control role permissions equivalent aws iam role policy",
        "azurerm_policy_definition":           "governance policy compliance equivalent aws iam policy scp",
        "azurerm_cosmosdb_account":            "nosql globally distributed database equivalent aws dynamodb",
        "azurerm_eventhub_namespace":          "event streaming real-time data ingestion hub equivalent aws kinesis stream",
        "azurerm_eventhub":                    "event streaming message equivalent aws kinesis stream shard",
        "azurerm_aadb2c_directory":            "user authentication identity b2c oauth oidc equivalent aws cognito user pool",
        "azurerm_redis_cache":                 "in-memory cache redis equivalent aws elasticache redis",
        "azurerm_servicebus_queue":            "message queue async decoupled equivalent aws sqs",
        "azurerm_servicebus_topic":            "pub sub topic notification equivalent aws sns",
        "azurerm_virtual_network":             "virtual private cloud network isolated equivalent aws vpc",
        "azurerm_network_security_group":      "firewall rules inbound outbound traffic equivalent aws security group",
        "azurerm_linux_virtual_machine":       "virtual machine compute server equivalent aws ec2 instance",
        "azurerm_kubernetes_cluster":          "kubernetes container orchestration k8s equivalent aws eks",
        "azurerm_monitor_metric_alert":        "monitoring alert metrics observability equivalent aws cloudwatch alarm",
        "azurerm_log_analytics_workspace":     "log storage observability monitoring equivalent aws cloudwatch logs",
        "azurerm_key_vault_key":               "encryption key management cryptographic equivalent aws kms",
        "azurerm_key_vault_secret":            "secret credential password api key equivalent aws secretsmanager",
        "azurerm_api_management":              "api gateway rest http proxy routing equivalent aws api gateway",
        # ── Azure data/analytics → AWS ────────────────────────────────────────
        "azurerm_data_factory":                "etl data pipeline data integration orchestration equivalent aws glue job",
        "azurerm_data_factory_pipeline":       "etl data pipeline orchestration equivalent aws glue job step functions",
        "azurerm_synapse_workspace":           "data warehouse analytics spark sql pool equivalent aws redshift emr",
        "azurerm_synapse_sql_pool":            "dedicated sql pool data warehouse columnar olap equivalent aws redshift",
        "azurerm_synapse_spark_pool":          "spark big data analytics pool equivalent aws emr databricks",
        "azurerm_databricks_workspace":        "apache spark big data analytics databricks equivalent aws emr glue databricks",
        "azurerm_search_service":              "search engine full-text cognitive ai search elasticsearch equivalent aws opensearch",
        "azurerm_dns_zone":                    "dns zone domain name resolution equivalent aws route53 hosted zone",
        "azurerm_dns_a_record":                "dns record domain routing equivalent aws route53 record",
        "azurerm_cdn_profile":                 "cdn content delivery network edge caching equivalent aws cloudfront",
        "azurerm_frontdoor_profile":           "cdn waf edge routing global load balancer equivalent aws cloudfront",
        "azurerm_app_service":                 "paas platform web app hosting managed runtime equivalent aws elastic beanstalk app service",
        "azurerm_linux_web_app":               "web application linux hosting paas equivalent aws elastic beanstalk lambda",
        "azurerm_container_registry":          "container registry docker image equivalent aws ecr",
        "azurerm_container_app":               "serverless container hosting equivalent aws ecs fargate",
        "azurerm_batch_account":               "batch compute hpc job scheduling parallel equivalent aws batch",
        "azurerm_logic_app_workflow":          "workflow orchestration serverless integration equivalent aws step functions",
        "azurerm_eventgrid_topic":             "event routing pub sub topic equivalent aws eventbridge sns",
        # ── GCP → AWS ────────────────────────────────────────────────────────
        "google_sql_database_instance":        "relational database managed postgresql mysql equivalent aws rds",
        "google_storage_bucket":               "object storage blob files static assets equivalent aws s3 bucket",
        "google_cloudfunctions_function":      "serverless function compute event-driven equivalent aws lambda",
        "google_cloud_run_v2_service":         "container serverless equivalent aws lambda ecs fargate",
        "google_container_cluster":            "kubernetes container orchestration equivalent aws eks",
        "google_pubsub_topic":                 "pub sub topic message streaming equivalent aws sns kinesis",
        "google_pubsub_subscription":          "message queue async equivalent aws sqs",
        "google_redis_instance":               "in-memory cache redis equivalent aws elasticache",
        "google_bigquery_dataset":             "data warehouse analytics equivalent aws redshift athena",
        "google_firestore_database":           "nosql document database equivalent aws dynamodb",
        "google_secret_manager_secret":        "secret credential password equivalent aws secretsmanager",
        "google_kms_crypto_key":               "encryption key management equivalent aws kms",
        "google_bigquery_table":               "data warehouse table analytics equivalent aws redshift athena",
        "google_bigquery_job":                 "data query analytics sql job equivalent aws athena",
        "google_dataproc_cluster":             "big data spark hadoop cluster equivalent aws emr",
        "google_dataflow_job":                 "stream batch data pipeline equivalent aws glue kinesis analytics",
        "google_cloud_composer_environment":   "workflow orchestration airflow equivalent aws mwaa step functions",
        "google_vertex_ai_endpoint":           "ml inference serving model deployment equivalent aws sagemaker endpoint",
        "google_dns_managed_zone":             "dns zone domain name resolution equivalent aws route53",
        "google_compute_global_forwarding_rule": "load balancer cdn global equivalent aws cloudfront alb",
        "google_artifact_registry_repository": "container registry artifact storage equivalent aws ecr",
    }

    def _build_embed_text(self, resource: TerraformResource) -> str:
        required_names = [a.name for a in resource.required_args]
        base = (
            f"{resource.resource_name} {resource.resource_name} "
            f"terraform {resource.provider} resource "
            f"{resource.description} "
            f"required arguments: {' '.join(required_names)}"
        ).strip()
        extra = self._SEMANTIC_TAGS.get(resource.resource_name, "")
        return f"{base} {extra}".strip() if extra else base

    def _resource_to_row(self, resource: TerraformResource, embed_text: str, embedding: list) -> tuple:
        """Convertit un TerraformResource en tuple pour psycopg2."""
        return (
            resource.resource_name,
            resource.provider,
            resource.description[:200],
            json.dumps([{"name": a.name, "description": a.description} for a in resource.required_args]),
            json.dumps([{"name": a.name, "description": getattr(a, "description", "")} for a in resource.optional_args[:20]]),
            json.dumps([{"name": b.name, "args": b.arguments} for b in resource.blocks]),
            resource.example_hcl[:600],
            resource.file_sha,
            embed_text,
            embedding,
        )

    def _insert_batch(self, docs: list[str], resources: list[TerraformResource]) -> int:
        """
        Encode un batch et insère dans terraform_resources.
        Fallback one-by-one si le batch échoue (ex: conflit sur un seul ID).
        Retourne le nombre de lignes réellement insérées/mises à jour.
        """
        embeddings = self.embedder.encode(
            docs,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()

        rows = [
            self._resource_to_row(res, doc, emb)
            for res, doc, emb in zip(resources, docs, embeddings)
        ]

        sql = """
            INSERT INTO terraform_resources
                (id, provider, description, required_args, optional_args,
                 blocks, example, file_sha, embed_text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                provider      = EXCLUDED.provider,
                description   = EXCLUDED.description,
                required_args = EXCLUDED.required_args,
                optional_args = EXCLUDED.optional_args,
                blocks        = EXCLUDED.blocks,
                example       = EXCLUDED.example,
                file_sha      = EXCLUDED.file_sha,
                embed_text    = EXCLUDED.embed_text,
                embedding     = EXCLUDED.embedding,
                updated_at    = NOW()
        """
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
                    count = cur.rowcount
                conn.commit()
            return count
        except Exception as batch_err:
            print(f"  Batch insert failed ({batch_err}), retrying one-by-one...")
            count = 0
            for row in rows:
                try:
                    with get_pg_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, row)
                            count += cur.rowcount
                        conn.commit()
                except Exception as e:
                    print(f"  Warning: skip {row[0]}: {e}")
            return count


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Build the Terraform RAG index in PostgreSQL")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if already up to date")
    parser.add_argument("--rebuild-args", action="store_true", help="Rebuild only argument nodes from existing DB data (no GitHub download)")
    args = parser.parse_args()

    # Check PostgreSQL connectivity before loading SentenceTransformer
    print("Checking PostgreSQL connectivity...")
    try:
        with get_pg_connection():
            pass
        print("PostgreSQL: OK")
    except Exception as e:
        print(f"PostgreSQL: FAILED — {e}")
        print("\nMake sure PostgreSQL is running and .env is configured:")
        print("  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME")
        sys.exit(1)

    from rag.rag_config import GITHUB_TOKEN
    if not GITHUB_TOKEN:
        print("WARNING: GITHUB_TOKEN not set — GitHub API requests will be rate-limited (60/hr)")

    builder = RAGBuilder(github_token=GITHUB_TOKEN or None)

    if args.rebuild_args:
        total = builder.rebuild_argument_nodes_from_db()
    else:
        total = builder.build_all(force=args.force)

    print(f"\nDone. {total} resources in index.")
