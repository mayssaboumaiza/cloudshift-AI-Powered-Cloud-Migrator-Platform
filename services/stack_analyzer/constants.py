"""
sa_constants.py — All pure-data detection maps and tiny utility functions.

Every other sa_*.py module imports from here.
No project-level imports — only stdlib (json, typing).
"""
from __future__ import annotations
import json
from typing import Any

def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("files", "python_files", "result", "data", "items", "content", "paths", "file_list"):
            if isinstance(value.get(key), list):
                return value[key]
        for k, v in value.items():
            if isinstance(v, list):
                return v
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _safe_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _safe_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("content", value.get("result", ""))
    return str(value) if value else ""


_AWS_SERVICES_MAP = {
    "s3": "storage", "dynamodb": "database", "lambda": "compute",
    "sqs": "messaging", "sns": "messaging", "bedrock": "ai",
    "bedrock-runtime": "ai", "bedrock-agent": "ai", "bedrock-agent-runtime": "ai",
    "opensearch": "search", "opensearchserverless": "search", "sagemaker": "ai",
    "ec2": "compute", "rds": "database", "eks": "compute",
    "cloudwatch": "monitoring", "iam": "security", "sts": "iam",
    "secretsmanager": "security", "ecr": "container", "ecs": "compute",
    "cloudfront": "networking", "route53": "networking",
    "api_gateway": "networking", "vpc": "networking",
}

_GCP_SERVICES_MAP = {
    "storage": "storage", "bigquery": "analytics", "firestore": "database",
    "pubsub": "messaging", "run": "compute", "functions": "compute",
    "aiplatform": "ai", "vertexai": "ai", "sql": "database",
    "dns": "networking", "container": "compute",
}

_AZURE_SERVICES_MAP = {
    "blob": "storage", "cosmos": "database", "functions": "compute",
    "servicebus": "messaging", "inference": "ai", "openai": "ai",
    "keyvault": "security", "search": "search",
}


# ─────────────────────────────────────────────────────────────────────────────
# AI Stack Detection Maps (Stdlib only — no external dependencies)
# ─────────────────────────────────────────────────────────────────────────────

AI_PROVIDER_IMPORTS = {
    # LangChain cloud integrations
    "langchain_aws": "aws_bedrock",
    "langchain_google_vertexai": "gcp_vertex",
    "langchain_google_genai": "gcp_genai",
    "langchain_openai": "azure_openai",
    "langchain_anthropic": "anthropic_claude",
    "langchain_mistralai": "mistral_ai",
    "langchain_cohere": "cohere_ai",
    "langchain_huggingface": "huggingface",
    "langchain_ollama": "ollama",
    "langchain_together": "together_ai",
    "langchain_replicate": "replicate_ai",
    "langchain_groq": "groq_ai",
    "langchain_fireworks": "fireworks_ai",
    # Native cloud SDKs
    "boto3": "aws_sdk",
    "botocore": "aws_sdk",
    "vertexai": "gcp_sdk",
    "google.cloud.aiplatform": "gcp_sdk",
    "google.generativeai": "google_genai_sdk",
    "openai": "openai_sdk",
    "anthropic": "anthropic_sdk",
    # Open-source / self-hosted LLM frameworks
    "transformers": "huggingface_transformers",
    "huggingface_hub": "huggingface_hub",
    "diffusers": "huggingface_diffusers",
    "sentence_transformers": "sentence_transformers",
    "llama_cpp": "llama_cpp",
    "llama_index": "llamaindex",
    "llama_index.core": "llamaindex",
    "llama_index.llms": "llamaindex",
    "llama_index.embeddings": "llamaindex",
    "ollama": "ollama_sdk",
    # Proprietary API SDKs
    "mistralai": "mistral_sdk",
    "cohere": "cohere_sdk",
    "together": "together_sdk",
    "replicate": "replicate_sdk",
    "groq": "groq_sdk",
    "fireworks": "fireworks_sdk",
    "ai21": "ai21_sdk",
    "aleph_alpha_client": "aleph_alpha_sdk",
}

# Prefix-based matching: if an import starts with one of these prefixes,
# classify it as the given provider — catches sub-module imports like
# "from transformers.models.llama import ..." without enumerating every sub-path.
AI_PROVIDER_PREFIXES: dict[str, str] = {
    "transformers.": "huggingface_transformers",
    "huggingface_hub.": "huggingface_hub",
    "diffusers.": "huggingface_diffusers",
    "sentence_transformers.": "sentence_transformers",
    "llama_cpp.": "llama_cpp",
    "llama_index.": "llamaindex",
    "mistralai.": "mistral_sdk",
    "cohere.": "cohere_sdk",
    "together.": "together_sdk",
    "groq.": "groq_sdk",
    "fireworks.": "fireworks_sdk",
    "google.cloud.aiplatform": "gcp_sdk",
    "google.generativeai": "google_genai_sdk",
    "langchain_": "langchain",  # catch any future langchain_* integrations
}

AI_VECTOR_IMPORTS = {
    # Module paths (not class paths) — what ast.ImportFrom.module returns
    # e.g. "from langchain_community.vectorstores import OpenSearchVectorSearch"
    #       → node.module = "langchain_community.vectorstores"
    "langchain_community.vectorstores": "vector_store",
    "langchain_pinecone": "portable_pinecone",
    "langchain_qdrant": "portable_qdrant",
    "langchain_chroma": "portable_chroma",
    "langchain_weaviate": "portable_weaviate",
    "langchain_milvus": "portable_milvus",
    "langchain_elasticsearch": "portable_elasticsearch",
    "langchain_opensearch": "portable_opensearch",
    "langchain_redis": "portable_redis",
    "pinecone": "portable_pinecone",
    "weaviate": "portable_weaviate",
    "chromadb": "portable_chroma",
    "qdrant_client": "portable_qdrant",
    "pymilvus": "portable_milvus",
    "elasticsearch": "portable_elasticsearch",
    "redis": "portable_redis",
    "pgvector": "portable_pgvector",
    "psycopg2": "portable_postgres",
    "asyncpg": "portable_postgres",
    "faiss": "portable_faiss",
}

AI_CLASSES = {
    # LangChain / LangGraph LLM wrappers
    "ChatBedrock": "llm",
    "BedrockLLM": "llm",
    "BedrockEmbeddings": "embeddings",
    "ChatVertexAI": "llm",
    "VertexAI": "llm",
    "VertexAIEmbeddings": "embeddings",
    "AzureChatOpenAI": "llm",
    "AzureOpenAI": "llm",
    "AzureOpenAIEmbeddings": "embeddings",
    "ChatOpenAI": "llm",
    "OpenAI": "llm",
    "OpenAIEmbeddings": "embeddings",
    "ChatAnthropic": "llm",
    "Anthropic": "llm",
    "AnthropicEmbeddings": "embeddings",
    "ChatMistralAI": "llm",
    "MistralAIEmbeddings": "embeddings",
    "ChatCohere": "llm",
    "CohereEmbeddings": "embeddings",
    "ChatHuggingFace": "llm",
    "HuggingFaceEmbeddings": "embeddings",
    "HuggingFaceEndpointEmbeddings": "embeddings",
    "HuggingFacePipeline": "llm",
    "HuggingFaceHub": "llm",
    "ChatOllama": "llm",
    "OllamaEmbeddings": "embeddings",
    "ChatGroq": "llm",
    "ChatFireworks": "llm",
    "ChatTogether": "llm",
    "TogetherEmbeddings": "embeddings",
    "ChatLiteLLM": "llm",
    # Native SDK clients
    "Mistral": "llm",
    "MistralClient": "llm",
    "Cohere": "llm",
    "Together": "llm",
    "Groq": "llm",
    # HuggingFace native
    "AutoModelForCausalLM": "llm",
    "AutoModelForSeq2SeqLM": "llm",
    "AutoTokenizer": "llm",
    "pipeline": "llm",
    "SentenceTransformer": "embeddings",
    # LlamaIndex
    "OpenAI": "llm",
    "AzureOpenAI": "llm",
    "Gemini": "llm",
    "Bedrock": "llm",
    # Vector store classes
    "OpenSearchVectorSearch": "vector_store",
    "VertexAIVectorSearch": "vector_store",
    "Pinecone": "vector_store",
    "PineconeVectorStore": "vector_store",
    "Weaviate": "vector_store",
    "WeaviateVectorStore": "vector_store",
    "Chroma": "vector_store",
    "QdrantVectorStore": "vector_store",
    "Qdrant": "vector_store",
    "Milvus": "vector_store",
    "MilvusVectorStore": "vector_store",
    "FAISS": "vector_store",
    "PGVector": "vector_store",
    "ElasticsearchStore": "vector_store",
    "Redis": "vector_store",
}

EMBEDDING_DIMENSIONS = {
    "amazon.titan-embed-text-v1": 1536,
    "amazon.titan-embed-text-v2:0": 1024,
    "textembedding-gecko@003": 768,
    "text-embedding-004": 768,
    "text-embedding-ada-002": 1536,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "claude-3-5-sonnet-20241022": 1024,
    "multilingual-e5-large": 1024,
}


# ─────────────────────────────────────────────────────────────────────────────
# Classification des services cloud par type
# ─────────────────────────────────────────────────────────────────────────────
_SERVICE_TYPE_MAP = {
    # IAM / Identity
    "iam": "iam", "cognito": "iam", "active-directory": "iam", "sts": "iam",
    "entra": "iam", "managed-identity": "iam", "workload-identity": "iam",
    # IAM resource sub-types (so e.g. "instance-profile" doesn't fall through to unknown)
    "instance-profile": "iam", "role-policy-attachment": "iam", "policy-attachment": "iam",
    "service-account": "iam", "role-assignment": "iam", "user-assigned-identity": "iam",
    "role-binding": "iam",
    # Network
    "vpc": "network", "elb": "network", "alb": "network", "nlb": "network",
    "cloudfront": "network", "route53": "network", "api-gateway": "network",
    "apigw": "network", "waf": "network", "cdn": "network",
    "load-balancer": "network", "application-gateway": "network",
    "front-door": "network", "dns": "network",
    # Network primitives (AWS / Azure / GCP common building blocks)
    "subnet": "network", "subnet-group": "network",
    "internet-gateway": "network", "nat-gateway": "network",
    "route-table": "network", "route-table-association": "network", "route": "network",
    "security-group": "network", "network-security-group": "network",
    "network-interface": "network", "elastic-ip": "network", "eip": "network",
    "vpc-peering": "network", "transit-gateway": "network", "vpn-gateway": "network",
    "virtual-network": "network", "vnet": "network",
    "network-acl": "network", "nacl": "network",
    # Stripped-suffix fallbacks: _normalize_service_name strips trailing
    # `_group`, `_table`, etc., so e.g. aws_security_group → "security"
    # and aws_internet_gateway-like cases need the bare-stem keys here too.
    "security": "network", "internet": "network", "gateway": "network",
    # Storage
    "s3": "storage", "gcs": "storage", "blob-storage": "storage", "efs": "storage",
    "fsx": "storage", "filestore": "storage", "azure-files": "storage",
    "storage-account": "storage", "glacier": "storage",
    # Database
    "dynamodb": "database", "rds": "database", "aurora": "database",
    "firestore": "database", "bigtable": "database", "cosmosdb": "database",
    "redshift": "database", "bigquery": "database", "spanner": "database",
    "alloydb": "database", "sql": "database", "postgres": "database",
    "mysql": "database", "mongo": "database", "redis": "database",
    "elasticache": "database", "memorystore": "database", "cache": "database",
    # AWS RDS sub-resources (terraform: aws_db_*)
    "db": "database", "db-instance": "database", "db-subnet-group": "database",
    "db-parameter-group": "database", "db-option-group": "database",
    "db-cluster": "database", "db-cluster-snapshot": "database", "db-snapshot": "database",
    # Azure / GCP DB resource flavours
    "postgresql-flexible-server": "database", "mysql-flexible-server": "database",
    "mssql-server": "database", "mssql-database": "database",
    "sql-database": "database", "sql-instance": "database",
    # Compute
    "lambda": "compute", "ec2": "compute", "ecs": "compute", "eks": "compute",
    "fargate": "compute", "cloud-functions": "compute", "cloud-run": "compute",
    "app-engine": "compute", "azure-functions": "compute",
    "app-runner": "compute", "container-apps": "compute", "gke": "compute",
    "aks": "compute", "batch": "compute", "jobs": "compute",
    # Compute primitives (raw VM / instance / launch infra)
    "instance": "compute", "launch-template": "compute", "launch-configuration": "compute",
    "autoscaling": "compute", "autoscaling-group": "compute", "asg": "compute",
    "linux-virtual-machine": "compute", "windows-virtual-machine": "compute",
    "virtual-machine": "compute", "vm": "compute", "vm-scale-set": "compute",
    "compute-instance": "compute", "instance-template": "compute", "instance-group": "compute",
    "function-app": "compute", "linux-function-app": "compute",
    "kubernetes-cluster": "compute", "container-cluster": "compute",
    # Monitoring
    "cloudwatch": "monitoring", "stackdriver": "monitoring",
    "x-ray": "monitoring", "monitor": "monitoring",
    "application-insights": "monitoring", "prometheus": "monitoring",
    "grafana": "monitoring", "datadog": "monitoring",
    # Common monitoring sub-types
    "metric-alarm": "monitoring", "metric-alert": "monitoring",
    "log-analytics-workspace": "monitoring", "log-group": "monitoring",
    "monitor-metric-alert": "monitoring", "monitor-action-group": "monitoring",
    # Messaging
    "sqs": "messaging", "sns": "messaging", "eventbridge": "messaging",
    "pubsub": "messaging", "kinesis": "messaging", "kafka": "messaging",
    "service-bus": "messaging", "event-hubs": "messaging", "mq": "messaging",
    # AI / LLM
    "bedrock": "ai", "bedrock-runtime": "ai",
    "bedrock-agent": "ai", "bedrock-agent-runtime": "ai",
    "sagemaker": "ai", "vertex-ai": "ai", "azure-openai": "ai",
    "cognitive-services": "ai", "aiplatform": "ai", "ml": "ai",
    # SDK / Session
    "boto3-session": "sdk",
    # Search
    "opensearch": "search", "opensearch-serverless": "search",
    "opensearchserverless": "search", "elasticsearch": "search", "azure-search": "search",
    "algolia": "search",
    # Vector Databases
    "chromadb": "vector_db", "pinecone": "vector_db",
    "faiss": "vector_db", "weaviate": "vector_db",
    "qdrant": "vector_db", "milvus": "vector_db",
    # AI Frameworks
    "crewai": "framework", "langchain": "framework",
    "llamaindex": "framework", "autogen": "framework",
    "haystack": "framework",
}

# ─── P22: Pulumi IaC detection ────────────────────────────────────────────────
PULUMI_PYTHON_IMPORTS = {
    "pulumi_aws":     "aws",
    "pulumi_gcp":     "gcp",
    "pulumi_azure":   "azure",
    "pulumi_azuread": "azure",
    "pulumi_google":  "gcp",
}

# Pulumi resource types use colon notation: "aws:s3/bucket:Bucket"
# Segment before first colon = provider, segment after = service
_PULUMI_PROVIDER_MAP = {"aws": "aws", "gcp": "gcp", "google": "gcp", "azure": "azure", "azuread": "azure"}


_TYPE_PRIORITY = {
    "iam": 1, "network": 2, "storage": 3, "database": 4,
    "compute": 5, "messaging": 6, "monitoring": 7,
    "ai": 8, "search": 9, "vector_db": 10, "framework": 11,
    "sdk": 12, "unknown": 13,
}


def _normalize_service_name(full_resource_type: str) -> str:
    """Normalize a resource type to its canonical service name.

    Examples:
        aws_s3_bucket → s3
        aws_dynamodb_table → dynamodb
        aws_bedrock_custom_model → bedrock
        aws_bedrock_model_invocation_logging_configuration → bedrock
        azurerm_storage_account → storage
        azurerm_cognitive_account → cognitive
        google_compute_instance → compute
    """
    # ── Compound-prefix fast-path: multi-word AWS service names that share a
    # common prefix must be collapsed BEFORE the generic suffix stripping,
    # otherwise "bedrock_custom_model" → strip "_model" → "bedrock-custom"
    # instead of the canonical "bedrock".
    _AWS_COMPOUND_PREFIXES: dict[str, str] = {
        "bedrock_agent_":       "bedrock",
        "bedrock_":             "bedrock",
        "bedrockagent_":        "bedrock",
        "sagemaker_endpoint":   "sagemaker",
        "sagemaker_":           "sagemaker",
        "opensearchserverless_":"opensearch",
        "cognito_identity_":    "cognito",
        "cognito_user_":        "cognito",
        "cognito_":             "cognito",
        "route53_":             "route53",
        "cloudwatch_":          "cloudwatch",
        "secretsmanager_":      "secretsmanager",
        "dynamodb_":            "dynamodb",
        "kinesis_":             "kinesis",
        "elasticache_":         "elasticache",
        "codecommit_":          "codecommit",
        "codepipeline_":        "codepipeline",
        "codebuild_":           "codebuild",
        "eks_":                 "eks",
        "ecs_":                 "ecs",
        "ecr_":                 "ecr",
        "wafv2_":               "waf",
        "waf_":                 "waf",
    }

    svc = full_resource_type

    # Remove cloud prefix to get bare resource type
    bare = svc
    if svc.startswith("aws_"):
        bare = svc[4:]
    elif svc.startswith("azurerm_"):
        bare = svc[8:]
    elif svc.startswith("google_"):
        bare = svc[7:]

    # Apply compound-prefix mapping (AWS only — azure/google have their own logic)
    if svc.startswith("aws_"):
        for compound, canonical in _AWS_COMPOUND_PREFIXES.items():
            if bare.startswith(compound) or bare == compound.rstrip("_"):
                return canonical
        svc = bare
    else:
        svc = bare

    # Extract base service name (remove resource-specific suffix)
    # Common suffixes: _bucket, _table, _topic, _queue, _instance, _group, _policy, _role, etc.
    suffixes_to_remove = [
        "_bucket", "_table", "_topic", "_queue", "_instance", "_group",
        "_policy", "_role", "_function", "_object", "_account", "_cluster",
        "_database", "_server", "_log_group", "_handler", "_layer",
    ]

    for suffix in suffixes_to_remove:
        if svc.endswith(suffix):
            svc = svc[:-len(suffix)]
            break

    # Normalize underscores to hyphens
    svc = svc.replace("_", "-")
    return svc

# Multiplicateur de complexité par type de service.
# Reflète l'effort réel de migration : database > compute > iam > storage …
_TYPE_COMPLEXITY_WEIGHT: dict[str, float] = {
    "database":   3.0,   # migration stateful la plus complexe
    "ai":         2.5,   # dépendances propriétaires (Bedrock, Vertex, Cognitive)
    "compute":    2.0,   # logique métier, configs d'exécution
    "network":    1.8,   # topologie, CIDR, peering
    "messaging":  1.5,   # contrats de file, ordering, idempotency
    "search":     1.5,   # schéma d'index, tokeniseurs
    "vector_db":  1.5,   # embeddings, similarity configs
    "iam":        1.2,   # politiques, rôles, bindings
    "storage":    1.0,   # objet/blob — transfert souvent trivial
    "monitoring": 0.8,   # alertes reconfigurables facilement
    "framework":  0.5,   # lib locale, pas une ressource cloud
    "sdk":        0.5,   # session client, pas une ressource cloud
    "unknown":    1.0,
}



# ─────────────────────────────────────────────────────────────────────────────
# Dynamic keyword classifier — handles resource types not in _SERVICE_TYPE_MAP
# without requiring manual dict updates.
#
# Rules are ordered by specificity: first match wins.  Each frozenset contains
# keywords that are either full segments or substrings of the normalised name.
# ─────────────────────────────────────────────────────────────────────────────
_CATEGORY_KEYWORD_RULES: list[tuple[str, frozenset]] = [
    # Most specific first to avoid false matches
    ("vector_db",  frozenset({"chromadb", "pinecone", "faiss", "weaviate",
                               "qdrant", "milvus", "pgvector"})),
    ("framework",  frozenset({"crewai", "langchain", "llamaindex",
                               "autogen", "haystack"})),
    ("sdk",        frozenset({"boto3", "session-manager"})),
    ("ai",         frozenset({"sagemaker", "bedrock", "vertex-ai", "cognitive",
                               "openai", "rekognition", "comprehend", "textract",
                               "aiplatform", "mlflow", "ml-workspace"})),
    ("search",     frozenset({"opensearch", "elasticsearch", "algolia",
                               "azure-search", "solr"})),
    ("messaging",  frozenset({"sqs", "sns", "eventbridge", "pubsub", "kinesis",
                               "kafka", "service-bus", "event-hub", "mq",
                               "queue", "topic", "notification"})),
    ("monitoring", frozenset({"cloudwatch", "stackdriver", "x-ray", "xray",
                               "application-insights", "prometheus", "grafana",
                               "datadog", "newrelic", "log-analytics",
                               "monitor", "alarm", "alert", "dashboard",
                               "log-group", "log-stream", "metric"})),
    ("database",   frozenset({"dynamodb", "rds", "aurora", "firestore",
                               "bigtable", "cosmosdb", "redshift", "bigquery",
                               "spanner", "alloydb", "postgres", "mysql",
                               "mongo", "redis", "elasticache", "memorystore",
                               "memcached", "cassandra", "couchdb", "influx",
                               "timescale", "neo4j", "tigergraph", "db",
                               "sql", "database", "datastore", "cache"})),
    ("storage",    frozenset({"s3", "gcs", "blob", "efs", "fsx", "filestore",
                               "glacier", "bucket", "disk", "volume",
                               "storage-account", "archive", "backup",
                               "object-store"})),
    ("iam",        frozenset({"iam", "cognito", "active-directory", "sts",
                               "entra", "managed-identity", "workload-identity",
                               "role", "policy", "permission", "identity",
                               "profile", "service-account", "role-assignment",
                               "role-binding", "access-control", "rbac",
                               "federation", "saml", "oauth", "sso"})),
    ("compute",    frozenset({"lambda", "ec2", "ecs", "eks", "fargate",
                               "cloud-functions", "cloud-run", "app-engine",
                               "azure-functions", "kubernetes", "gke", "aks",
                               "batch", "instance", "virtual-machine", "vm",
                               "container", "function-app", "autoscaling",
                               "launch-template", "app-service", "app-runner",
                               "workload", "pod", "job", "cronjob"})),
    # Network last — broad terms like "gateway" and "security" should only
    # match if no more-specific category already claimed the resource.
    ("network",    frozenset({"vpc", "vnet", "elb", "alb", "nlb", "cloudfront",
                               "route53", "api-gateway", "waf", "cdn",
                               "load-balancer", "application-gateway",
                               "front-door", "dns", "subnet", "nat-gateway",
                               "internet-gateway", "route-table",
                               "security-group", "network-security-group",
                               "network-interface", "elastic-ip", "eip",
                               "peering", "transit-gateway", "vpn",
                               "nacl", "acl", "firewall", "gateway",
                               "internet", "security", "ingress", "egress",
                               "lb", "proxy", "reverse-proxy"})),
]

_RELATION_PATTERNS: dict[str, list[str]] = {
    "reads_from":        ["get_object", "download_file", "read", "get_item",
                          "query", "scan", "list_objects", "fetch"],
    "writes_to":         ["put_object", "upload_file", "write", "insert",
                          "put_item", "update_item", "batch_write"],
    "authenticates_via": ["assume_role", "get_credentials", "auth",
                          "get_session_token", "get_authorization_token"],
    "triggers":          ["create_function", "invoke", "trigger", "publish",
                          "send_message", "start_execution"],
}

_INFRA_SDKS: dict = {
    "aws":   ["sts", "boto3-session", "botocore", "aws-core", "boto3.session"],
    "azure": ["azure-identity", "azure-core", "msrest", "azure-mgmt-core", "azure-common"],
    "gcp":   ["google-auth", "google-api-core", "grpc", "google-cloud-core", "google-auth-oauthlib"],
}
