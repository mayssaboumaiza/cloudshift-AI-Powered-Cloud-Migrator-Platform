"""
sa_manifest_parser.py — Manifest, SDK client, and Docker Compose parsers.

Functions (cascade Level 2 — used when no IaC is found):
  _parse_dependency_manifests — Parse requirements.txt, package.json, etc.
  _parse_sdk_client_calls     — Detect cloud SDK usage via Python AST.
  _parse_docker_compose       — Extract services from docker-compose.yml.
  _scan_env_var_patterns      — Detect cloud env vars (AWS_*, AZURE_*, etc.).
  _merge_layer2_results       — Deduplicate and merge manifest detections.
"""
from __future__ import annotations
import ast
import json
import logging
import os
import re
from typing import Any

from services.stack_analyzer.constants import (
    _AWS_SERVICES_MAP, _GCP_SERVICES_MAP, _AZURE_SERVICES_MAP, _safe_str,
)
from services.stack_analyzer.service_classifier import _get_service_type

logger = logging.getLogger("StackAnalyzer")

# ─────────────────────────────────────────────────────────────────────────────
# Layer 2: Code-scan detection (manifests, AST, docker-compose, env vars)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_dependency_manifests(
    owner: str, repo: str, branch: str, github_token: str
) -> list[dict]:
    """Read dependency manifests from GitHub and map packages to cloud services.

    Reads: requirements.txt, pyproject.toml, package.json, go.mod, pom.xml, build.gradle.
    Source code is NEVER sent to any LLM — only package names are extracted locally.
    """
    MANIFEST_TO_SERVICE: dict[str, list[tuple[str, str, float]]] = {
        "boto3":                    [("aws", "sdk",               0.50)],
        "aiobotocore":              [("aws", "sdk",               0.50)],
        "aws-cdk-lib":              [("aws", "sdk",               0.60)],
        "google-cloud-storage":     [("gcp", "gcs",               0.85)],
        "google-cloud-bigquery":    [("gcp", "bigquery",          0.85)],
        "google-cloud-pubsub":      [("gcp", "pubsub",            0.85)],
        "google-cloud-firestore":   [("gcp", "firestore",         0.85)],
        "google-cloud-spanner":     [("gcp", "spanner",           0.85)],
        "google-cloud-run":         [("gcp", "cloud-run",         0.85)],
        "google-cloud-functions":   [("gcp", "cloud-functions",   0.85)],
        "google-cloud-bigtable":    [("gcp", "bigtable",          0.85)],
        "google-cloud-tasks":       [("gcp", "cloud-tasks",       0.80)],
        "google-cloud-aiplatform":  [("gcp", "aiplatform",        0.85)],
        "azure-storage-blob":       [("azure", "blob-storage",    0.85)],
        "azure-storage-queue":      [("azure", "storage-queue",   0.80)],
        "azure-cosmos":             [("azure", "cosmosdb",        0.85)],
        "azure-servicebus":         [("azure", "service-bus",     0.85)],
        "azure-eventhub":           [("azure", "event-hubs",      0.85)],
        "azure-keyvault-secrets":   [("azure", "keyvault",        0.85)],
        "azure-search-documents":   [("azure", "cognitive-search",0.85)],
        "azure-ai-ml":              [("azure", "ml-workspace",    0.80)],
        "azure-mgmt-compute":       [("azure", "compute",         0.75)],
        "azure-mgmt-storage":       [("azure", "blob-storage",    0.75)],
        "redis":                    [("aws", "elasticache",       0.55),
                                     ("azure", "cache-for-redis", 0.55),
                                     ("gcp",  "memorystore",      0.55)],
        # DB driver packages → external connection hint.
        # These packages prove the app CONNECTS to a database but do NOT prove it OWNS one.
        # The dependency may point to an external managed DB (RDS, Cloud SQL, etc.).
        # Tag is_external_connection=True so Agent 01 assigns RETAIN by default unless
        # the user explicitly confirms ownership in the UI.
        "psycopg2":                 [("aws", "rds",               0.55),
                                     ("azure", "postgresql",      0.55)],
        "psycopg2-binary":          [("aws", "rds",               0.55)],
        "asyncpg":                  [("aws", "rds",               0.55)],
        "pymongo":                  [("aws", "documentdb",        0.55),
                                     ("azure", "cosmosdb",        0.55)],
        "motor":                    [("aws", "documentdb",        0.50)],
        "celery":                   [("aws", "sqs",               0.55),
                                     ("azure", "service-bus",     0.50)],
        "confluent-kafka":          [("aws", "msk",               0.65),
                                     ("azure", "event-hubs",      0.60)],
        "kafka-python":             [("aws", "msk",               0.60)],
        "elasticsearch":            [("aws", "opensearch",        0.60)],
        "opensearch-py":            [("aws", "opensearch",        0.70)],
        "pymysql":                  [("aws", "rds",               0.55)],
        "aiomysql":                 [("aws", "rds",               0.55)],
    }

    # DB driver packages that prove CONNECTIVITY but NOT OWNERSHIP of the database.
    # When these are the ONLY signal (no IaC declaring the DB), Agent 01 must treat
    # the service as an external dependency → strategy RETAIN, no IaC generated.
    _DB_DRIVER_PACKAGES: frozenset[str] = frozenset({
        "psycopg2", "psycopg2-binary", "asyncpg", "pg8000",
        "pymysql", "aiomysql", "mysqlclient",
        "pymongo", "motor",
        "redis", "aioredis",
        "elasticsearch", "opensearch-py",
        "cassandra-driver", "aiocassandra",
        "pymssql", "pyodbc",
    })

    results: list[dict] = []

    def _check_pkg(name: str, src: str) -> None:
        clean = re.split(r'[>=<!;\[\s]', name.strip())[0].strip().lower().replace("_", "-")
        is_driver = clean in _DB_DRIVER_PACKAGES
        for cloud, svc, conf in MANIFEST_TO_SERVICE.get(clean, []):
            entry: dict = {
                "service": svc, "cloud": cloud, "confidence": conf,
                "source": "manifest", "source_file": src,
            }
            if is_driver:
                entry["contextual_hints"] = {
                    "is_external_connection": True,
                    "hint": (
                        f"Package '{clean}' is a DB driver — proves connectivity, not ownership. "
                        "If this app owns the database, confirm in the migration wizard. "
                        "Strategy defaults to RETAIN until confirmed."
                    ),
                    "source_package": clean,
                }
            results.append(entry)

    # ── requirements.txt ─────────────────────────────────────────────────────
    try:
        content = _cached_fetch(owner, repo, "requirements.txt", branch, github_token)
        if content:
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("-"):
                    _check_pkg(line, "requirements.txt")
    except Exception as e:
        logger.debug(f"_parse_dependency_manifests requirements.txt: {e}")

    # ── pyproject.toml (regex — stdlib only, no tomllib) ─────────────────────
    try:
        content = _cached_fetch(owner, repo, "pyproject.toml", branch, github_token)
        if content:
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if re.match(r'^\[(project\.dependencies|tool\.poetry\.dependencies)\]', stripped):
                    in_deps = True
                elif stripped.startswith("[") and in_deps:
                    in_deps = False
                elif in_deps and stripped and not stripped.startswith("#"):
                    m = re.match(r'^([a-zA-Z0-9][a-zA-Z0-9._-]*)', stripped)
                    if m:
                        _check_pkg(m.group(1), "pyproject.toml")
    except Exception as e:
        logger.debug(f"_parse_dependency_manifests pyproject.toml: {e}")

    # ── package.json ─────────────────────────────────────────────────────────
    try:
        content = _cached_fetch(owner, repo, "package.json", branch, github_token)
        if content:
            pkg_data = json.loads(content)
            all_deps: dict = {}
            all_deps.update(pkg_data.get("dependencies", {}))
            all_deps.update(pkg_data.get("devDependencies", {}))
            for pkg_name in all_deps:
                _check_pkg(pkg_name, "package.json")
    except Exception as e:
        logger.debug(f"_parse_dependency_manifests package.json: {e}")

    # ── go.mod (MANIFEST_TO_SERVICE covers Python/Node — use path patterns here) ─
    try:
        content = _cached_fetch(owner, repo, "go.mod", branch, github_token)
        if content:
            _GO_PATTERNS = [
                (r"github\.com/aws/aws-sdk-go-v2",       "aws",   "sdk",       0.65),
                (r"github\.com/aws/aws-sdk-go",          "aws",   "sdk",       0.60),
                (r"cloud\.google\.com/go/storage",       "gcp",   "gcs",       0.80),
                (r"cloud\.google\.com/go/bigquery",      "gcp",   "bigquery",  0.80),
                (r"cloud\.google\.com/go/pubsub",        "gcp",   "pubsub",    0.80),
                (r"cloud\.google\.com/go/firestore",     "gcp",   "firestore", 0.80),
                (r"cloud\.google\.com/go",               "gcp",   "sdk",       0.55),
                (r"github\.com/Azure/azure-sdk-for-go",  "azure", "sdk",       0.60),
            ]
            for pattern, cloud, svc, conf in _GO_PATTERNS:
                if re.search(pattern, content):
                    results.append({
                        "service": svc, "cloud": cloud, "confidence": conf,
                        "source": "manifest", "source_file": "go.mod",
                    })
    except Exception as e:
        logger.debug(f"_parse_dependency_manifests go.mod: {e}")

    # ── pom.xml ───────────────────────────────────────────────────────────────
    try:
        content = _cached_fetch(owner, repo, "pom.xml", branch, github_token)
        if content:
            _MAVEN_PATTERNS = [
                (r"com\.amazonaws",        "aws",   "sdk", 0.60),
                (r"software\.amazon",      "aws",   "sdk", 0.60),
                (r"com\.google\.cloud",    "gcp",   "sdk", 0.60),
                (r"com\.azure",            "azure", "sdk", 0.60),
                (r"com\.microsoft\.azure", "azure", "sdk", 0.65),
            ]
            for gid in re.findall(r"<groupId>([^<]+)</groupId>", content):
                for pattern, cloud, svc, conf in _MAVEN_PATTERNS:
                    if re.search(pattern, gid):
                        results.append({
                            "service": svc, "cloud": cloud, "confidence": conf,
                            "source": "manifest", "source_file": "pom.xml",
                        })
                        break
    except Exception as e:
        logger.debug(f"_parse_dependency_manifests pom.xml: {e}")

    # ── build.gradle ─────────────────────────────────────────────────────────
    try:
        content = _cached_fetch(owner, repo, "build.gradle", branch, github_token)
        if content:
            _GRADLE_PATTERNS = [
                (r"com\.amazonaws",     "aws",   "sdk", 0.60),
                (r"software\.amazon",   "aws",   "sdk", 0.60),
                (r"com\.google\.cloud", "gcp",   "sdk", 0.60),
                (r"com\.azure",         "azure", "sdk", 0.60),
            ]
            for dep in re.findall(r'implementation\s+[\'"]([^\'"]+)[\'"]', content):
                group = dep.split(":")[0] if ":" in dep else dep
                for pattern, cloud, svc, conf in _GRADLE_PATTERNS:
                    if re.search(pattern, group):
                        results.append({
                            "service": svc, "cloud": cloud, "confidence": conf,
                            "source": "manifest", "source_file": "build.gradle",
                        })
                        break
    except Exception as e:
        logger.debug(f"_parse_dependency_manifests build.gradle: {e}")

    logger.debug(f"_parse_dependency_manifests: {len(results)} signals")
    return results


def _parse_sdk_client_calls(source_code: str, filename: str) -> list[dict]:
    """Parse Python source via AST and extract cloud SDK client instantiation calls.

    Detects boto3.client/resource, GCP module.Client(), and Azure class constructors.
    Only service NAME strings are extracted — source code is never transmitted externally.
    On SyntaxError, returns empty list silently.
    """
    GCP_MODULE_TO_SERVICE: dict[str, str] = {
        "storage": "gcs", "bigquery": "bigquery", "pubsub": "pubsub",
        "pubsub_v1": "pubsub", "firestore": "firestore", "spanner": "spanner",
        "datastore": "datastore", "bigtable": "bigtable", "aiplatform": "aiplatform",
    }
    AZURE_CLASS_TO_SERVICE: dict[str, str] = {
        "BlobServiceClient": "blob-storage",
        "ContainerClient": "blob-storage",
        "CosmosClient": "cosmosdb",
        "ServiceBusClient": "service-bus",
        "EventHubProducerClient": "event-hubs",
        "EventHubConsumerClient": "event-hubs",
        "SecretClient": "keyvault",
        "CertificateClient": "keyvault",
        "SearchClient": "cognitive-search",
        "QueueServiceClient": "storage-queue",
        "TableServiceClient": "table-storage",
    }

    results: list[dict] = []
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        logger.debug(f"_parse_sdk_client_calls: SyntaxError in {filename} — {e}")
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # boto3.client('s3') / boto3.resource('s3') / session.client('sqs')
        if (isinstance(func, ast.Attribute) and
                func.attr in {"client", "resource"} and
                node.args and isinstance(node.args[0], ast.Constant) and
                isinstance(node.args[0].value, str)):
            svc_name = node.args[0].value.lower()
            is_direct_boto3 = isinstance(func.value, ast.Name) and func.value.id == "boto3"
            results.append({
                "service": svc_name,
                "cloud": "aws",
                "confidence": 0.85 if is_direct_boto3 else 0.80,
                "source": "ast_scan",
                "source_file": filename,
            })

        # GCP: storage.Client() / bigquery.Client() / pubsub_v1.PublisherClient()
        elif (isinstance(func, ast.Attribute) and
              func.attr in {"Client", "PublisherClient", "SubscriberClient"} and
              isinstance(func.value, ast.Name) and
              func.value.id in GCP_MODULE_TO_SERVICE):
            results.append({
                "service": GCP_MODULE_TO_SERVICE[func.value.id],
                "cloud": "gcp",
                "confidence": 0.75,
                "source": "ast_scan",
                "source_file": filename,
            })

        # GCP: aiplatform.init(...)
        elif (isinstance(func, ast.Attribute) and
              func.attr == "init" and
              isinstance(func.value, ast.Name) and
              func.value.id == "aiplatform"):
            results.append({
                "service": "aiplatform",
                "cloud": "gcp",
                "confidence": 0.70,
                "source": "ast_scan",
                "source_file": filename,
            })

        # Azure: BlobServiceClient(...) / CosmosClient(...) / SecretClient(...) etc.
        elif (isinstance(func, ast.Name) and func.id in AZURE_CLASS_TO_SERVICE):
            results.append({
                "service": AZURE_CLASS_TO_SERVICE[func.id],
                "cloud": "azure",
                "confidence": 0.80,
                "source": "ast_scan",
                "source_file": filename,
            })

    return results


def _parse_docker_compose(content: str) -> list[dict]:
    """Parse docker-compose.yml content and map service images to cloud managed equivalents."""
    DOCKER_IMAGE_TO_SERVICE: dict[str, tuple[str, str, float]] = {
        "postgres":      ("any", "rds",         0.65),
        "postgresql":    ("any", "rds",         0.65),
        "mysql":         ("any", "rds",         0.65),
        "mariadb":       ("any", "rds",         0.60),
        "mongo":         ("any", "documentdb",  0.60),
        "mongodb":       ("any", "documentdb",  0.60),
        "redis":         ("any", "elasticache", 0.65),
        "rabbitmq":      ("any", "sqs",         0.60),
        "kafka":         ("any", "msk",         0.70),
        "zookeeper":     ("any", "msk",         0.55),
        "elasticsearch": ("any", "opensearch",  0.65),
        "opensearch":    ("any", "opensearch",  0.75),
        "minio":         ("any", "s3",          0.60),
        "mailhog":       ("any", "ses",         0.40),
        "localstack":    ("any", "sdk",         0.50),
    }

    results: list[dict] = []
    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            return []
        services = data.get("services", {})
        if not isinstance(services, dict):
            return []
        for _svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue
            image = svc_def.get("image", "")
            if not image:
                continue
            base = image.split(":")[0].split("/")[-1].lower()
            for img_key, (cloud, svc, conf) in DOCKER_IMAGE_TO_SERVICE.items():
                if base.startswith(img_key):
                    results.append({
                        "service": svc, "cloud": cloud, "confidence": conf,
                        "source": "docker_compose", "source_file": "docker-compose.yml",
                    })
                    break
    except Exception as e:
        logger.debug(f"_parse_docker_compose: {e}")
    return results


def _scan_env_var_patterns(source_code: str) -> list[dict]:
    """Scan source code for cloud service environment variable NAME patterns.

    Only variable NAMES are matched via regex — values are never read or transmitted.
    """
    ENV_VAR_RULES: list[tuple[str, str, str, float]] = [
        (r"AWS_S3_BUCKET|S3_BUCKET_NAME|BUCKET_NAME",       "aws",   "s3",           0.65),
        (r"AWS_SQS_QUEUE|SQS_QUEUE_URL|SQS_URL",            "aws",   "sqs",          0.65),
        (r"AWS_DYNAMODB|DYNAMODB_TABLE|DYNAMO_TABLE",        "aws",   "dynamodb",     0.65),
        (r"CLOUDFRONT_DISTRIBUTION|CDN_URL",                 "aws",   "cloudfront",   0.55),
        (r"AWS_ELASTICSEARCH|OPENSEARCH_URL|ES_ENDPOINT",    "aws",   "opensearch",   0.60),
        (r"AZURE_COSMOS|COSMOS_DB_URL|COSMOSDB_URI",         "azure", "cosmosdb",     0.70),
        (r"AZURE_STORAGE|BLOB_STORAGE|AZURE_BLOB",           "azure", "blob-storage", 0.70),
        (r"AZURE_SERVICEBUS|SERVICE_BUS_CONN",               "azure", "service-bus",  0.70),
        (r"AZURE_EVENTHUB|EVENT_HUB_CONN",                   "azure", "event-hubs",   0.70),
        (r"GCP_BUCKET|GOOGLE_CLOUD_STORAGE|GCS_BUCKET",     "gcp",   "gcs",          0.70),
        (r"BIGQUERY_DATASET|BQ_DATASET|BIGQUERY_TABLE",      "gcp",   "bigquery",     0.70),
        (r"PUBSUB_TOPIC|PUBSUB_SUBSCRIPTION",                "gcp",   "pubsub",       0.70),
        (r"REDIS_URL|REDIS_HOST|REDIS_ENDPOINT",             "any",   "elasticache",  0.55),
        (r"DATABASE_URL|POSTGRES_URL|PG_HOST|PGHOST|RDS_URL|RDS_HOST|DB_URL|DB_HOST",
                                                             "any",   "rds",          0.55),
        (r"MONGODB_URI|MONGO_URL|MONGO_URI",                 "any",   "documentdb",   0.60),
        (r"KAFKA_BROKER|KAFKA_BOOTSTRAP|KAFKA_SERVERS",      "any",   "msk",          0.60),
        (r"ELASTICSEARCH_URL|ELASTIC_HOST",                  "any",   "opensearch",   0.60),
    ]

    # Patterns where the env var is a *connection string* to an EXTERNAL service
    # (the app connects to it but does not own it). These must NOT be mapped to
    # new cloud resources — they are already deployed elsewhere (e.g. AWS RDS
    # accessed via RDS_URL from an app that only reads/writes to it).
    _EXTERNAL_CONNECTION_PATTERNS: frozenset[str] = frozenset({
        r"REDIS_URL|REDIS_HOST|REDIS_ENDPOINT",
        r"DATABASE_URL|POSTGRES_URL|PG_HOST|PGHOST|RDS_URL|RDS_HOST|DB_URL|DB_HOST",
        r"MONGODB_URI|MONGO_URL|MONGO_URI",
        r"ELASTICSEARCH_URL|ELASTIC_HOST",
    })

    results: list[dict] = []
    for pattern, cloud, svc, conf in ENV_VAR_RULES:
        if re.search(pattern, source_code):
            entry: dict = {
                "service": svc, "cloud": cloud, "confidence": conf,
                "source": "env_var_pattern", "source_file": "source_code",
            }
            if pattern in _EXTERNAL_CONNECTION_PATTERNS:
                entry["contextual_hints"] = {
                    "is_external_connection": True,
                    "hint": (
                        "Env var is a connection string to an EXTERNAL service — "
                        "do NOT create this resource. Strategy must be RETAIN."
                    ),
                }
            results.append(entry)
    return results


def _scan_requirements_for_ai(content: str) -> dict:
    """Scan requirements.txt content for AI/LLM packages.

    Returns a dict with keys: llm_provider, embedding_model, vector_db, framework
    (all strings or None). Used as fallback when Python AST finds nothing.
    """
    _PKG_AI_MAP: dict[str, tuple[str, str]] = {
        # (category, provider_label)
        "openai":                       ("llm",       "openai"),
        "anthropic":                    ("llm",       "anthropic_claude"),
        "mistralai":                    ("llm",       "mistral_ai"),
        "cohere":                       ("llm",       "cohere_ai"),
        "groq":                         ("llm",       "groq_ai"),
        "together":                     ("llm",       "together_ai"),
        "replicate":                    ("llm",       "replicate_ai"),
        "ai21":                         ("llm",       "ai21"),
        "fireworks-ai":                 ("llm",       "fireworks_ai"),
        "google-cloud-aiplatform":      ("llm",       "gcp_vertex"),
        "vertexai":                     ("llm",       "gcp_vertex"),
        "boto3":                        ("llm",       "aws_bedrock"),   # enriched later by IaC
        "azure-ai-inference":           ("llm",       "azure_openai"),
        "azure-openai":                 ("llm",       "azure_openai"),
        "langchain-openai":             ("llm",       "openai"),
        "langchain-anthropic":          ("llm",       "anthropic_claude"),
        "langchain-aws":                ("llm",       "aws_bedrock"),
        "langchain-google-vertexai":    ("llm",       "gcp_vertex"),
        "langchain-mistralai":          ("llm",       "mistral_ai"),
        "langchain-cohere":             ("llm",       "cohere_ai"),
        "langchain-groq":               ("llm",       "groq_ai"),
        "langchain-huggingface":        ("llm",       "huggingface"),
        "transformers":                 ("llm",       "huggingface_transformers"),
        "diffusers":                    ("llm",       "huggingface_diffusers"),
        "llama-cpp-python":             ("llm",       "llama_cpp"),
        "ollama":                       ("llm",       "ollama"),
        "sentence-transformers":        ("embeddings","sentence_transformers"),
        "huggingface-hub":              ("embeddings","huggingface_hub"),
        "fastembed":                    ("embeddings","fastembed"),
        "chromadb":                     ("vector_db", "chroma"),
        "pinecone-client":              ("vector_db", "pinecone"),
        "pinecone":                     ("vector_db", "pinecone"),
        "weaviate-client":              ("vector_db", "weaviate"),
        "qdrant-client":                ("vector_db", "qdrant"),
        "pymilvus":                     ("vector_db", "milvus"),
        "faiss-cpu":                    ("vector_db", "faiss"),
        "faiss-gpu":                    ("vector_db", "faiss"),
        "pgvector":                     ("vector_db", "pgvector"),
        "langchain":                    ("framework", "langchain"),
        "langchain-core":               ("framework", "langchain"),
        "langgraph":                    ("framework", "langgraph"),
        "llama-index":                  ("framework", "llamaindex"),
        "llama-index-core":             ("framework", "llamaindex"),
        "crewai":                       ("framework", "crewai"),
        "autogen":                      ("framework", "autogen"),
        "haystack-ai":                  ("framework", "haystack"),
        "semantic-kernel":              ("framework", "semantic_kernel"),
    }

    result: dict[str, str | None] = {
        "llm_provider": None, "embedding_model": None,
        "vector_db": None, "framework": None,
    }

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pkg = re.split(r"[>=<!;\[\s]", line)[0].strip().lower().replace("_", "-")
        if pkg in _PKG_AI_MAP:
            cat, label = _PKG_AI_MAP[pkg]
            key = {"llm": "llm_provider", "embeddings": "embedding_model",
                   "vector_db": "vector_db", "framework": "framework"}[cat]
            if not result[key]:   # first match wins per category
                result[key] = label

    return result


def _scan_env_for_ai_keys(content: str) -> str | None:
    """Scan .env / .env.example content for LLM API key variable names.

    Returns the detected provider label, or None if nothing found.
    Only variable NAMES are matched — values are never read.
    """
    _KEY_PATTERNS: list[tuple[str, str]] = [
        (r"OPENAI_API_KEY",             "openai"),
        (r"ANTHROPIC_API_KEY",          "anthropic_claude"),
        (r"MISTRAL_API_KEY",            "mistral_ai"),
        (r"COHERE_API_KEY",             "cohere_ai"),
        (r"GROQ_API_KEY",               "groq_ai"),
        (r"TOGETHER_API_KEY",           "together_ai"),
        (r"REPLICATE_API_TOKEN",        "replicate_ai"),
        (r"FIREWORKS_API_KEY",          "fireworks_ai"),
        (r"AI21_API_KEY",               "ai21"),
        (r"AZURE_OPENAI_API_KEY",       "azure_openai"),
        (r"AZURE_AI_API_KEY",           "azure_openai"),
        (r"GOOGLE_API_KEY|GEMINI_API_KEY", "gcp_gemini"),
        (r"VERTEX_AI|GOOGLE_CLOUD_PROJECT.*AIPLATFORM", "gcp_vertex"),
        (r"AWS_BEDROCK|BEDROCK_MODEL",  "aws_bedrock"),
        (r"HUGGINGFACE_API_KEY|HF_TOKEN|HUGGING_FACE_HUB_TOKEN", "huggingface"),
        (r"OLLAMA_HOST|OLLAMA_BASE_URL","ollama"),
    ]
    for pattern, provider in _KEY_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return provider
    return None


def _merge_layer2_results(detections: list[list[dict]], dominant_cloud: str) -> list[dict]:
    """Merge and deduplicate Layer 2 detections from all sources.

    Resolution order:
    1. Resolve cloud="any" entries to dominant_cloud when known.
    2. Deduplicate by (cloud, service) — highest confidence kept, +0.10 per extra source.
    3. Cap confidence at 0.97, filter < 0.40, sort descending.
    """
    resolved: list[dict] = []
    for batch in detections:
        for item in batch:
            entry = dict(item)
            if entry.get("cloud") == "any" and dominant_cloud not in ("unknown", "any", ""):
                entry["cloud"] = dominant_cloud
            resolved.append(entry)

    merged: dict[tuple, dict] = {}
    for item in resolved:
        key = (item.get("cloud", "any"), item.get("service", ""))
        if key not in merged:
            merged[key] = {**item, "detection_count": 1}
        else:
            existing = merged[key]
            existing["confidence"] = min(0.97, max(existing["confidence"], item["confidence"]) + 0.10)
            existing["detection_count"] += 1

    result = [v for v in merged.values() if v["confidence"] >= 0.40]
    result.sort(key=lambda x: x["confidence"], reverse=True)
    return result


# ─────────────────────────────────────────────────────────────────────────────
