"""
seed_canonical_patterns.py — Populate tf_canonical_patterns with curated HCL templates.

These patterns implement the 3rd signal of graph_rag_query() (canonical architecture hints).
Based on: Nekrasov et al. 2025 — Knowledge Injection for IaC Generation.

Run once:
    python -m rag.seed_canonical_patterns

Or via API:
    POST /api/v1/rag/seed-patterns
"""
import json
import logging
import os
from typing import Any

logger = logging.getLogger("seed_canonical_patterns")

# ── Canonical patterns ────────────────────────────────────────────────────────
# Format: (provider, pattern_name, description, resource_types[], hcl_template)
# These are the most common migration targets. The LLM uses these as architecture
# skeletons to avoid hallucinating argument names.
_PATTERNS: list[dict[str, Any]] = [
    # ─── AZURE ───────────────────────────────────────────────────────────────
    {
        "provider": "azurerm",
        "pattern_name": "azure_postgresql_flexible_server",
        "description": "Managed PostgreSQL Flexible Server on Azure — equivalent of AWS RDS Aurora/PostgreSQL",
        "resource_types": ["azurerm_postgresql_flexible_server", "azurerm_resource_group"],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                   = var.db_server_name
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  version                = "16"
  administrator_login    = var.db_admin_username
  administrator_password = var.db_admin_password
  sku_name               = "GP_Standard_D2s_v3"
  storage_mb             = 32768
  backup_retention_days  = 7
  geo_redundant_backup_enabled = false

  authentication {
    active_directory_auth_enabled = false
    password_auth_enabled         = true
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_storage_account_blob",
        "description": "Azure Storage Account with Blob container — equivalent of AWS S3 bucket",
        "resource_types": ["azurerm_storage_account", "azurerm_storage_container", "azurerm_resource_group"],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_storage_account" "main" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  blob_properties {
    versioning_enabled = true
  }
}

resource "azurerm_storage_container" "main" {
  name                  = var.container_name
  storage_account_name  = azurerm_storage_account.main.name
  container_access_type = "private"
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_linux_function_app",
        "description": "Azure Linux Function App with storage — equivalent of AWS Lambda",
        "resource_types": [
            "azurerm_linux_function_app",
            "azurerm_service_plan",
            "azurerm_storage_account",
            "azurerm_user_assigned_identity",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_user_assigned_identity" "func" {
  name                = "${var.function_name}-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_storage_account" "func" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
}

resource "azurerm_service_plan" "main" {
  name                = "${var.function_name}-plan"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "Y1"
}

resource "azurerm_linux_function_app" "main" {
  name                       = var.function_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  storage_account_name       = azurerm_storage_account.func.name
  storage_account_access_key = azurerm_storage_account.func.primary_access_key
  service_plan_id            = azurerm_service_plan.main.id
  https_only                 = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  site_config {
    application_stack {
      python_version = "3.11"
    }
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_kubernetes_service",
        "description": "Azure Kubernetes Service (AKS) cluster — equivalent of AWS EKS",
        "resource_types": [
            "azurerm_kubernetes_cluster",
            "azurerm_user_assigned_identity",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_user_assigned_identity" "aks" {
  name                = "${var.cluster_name}-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_kubernetes_cluster" "main" {
  name                = var.cluster_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.cluster_name

  default_node_pool {
    name       = "default"
    node_count = 2
    vm_size    = "Standard_D2_v2"
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.aks.id]
  }

  network_profile {
    network_plugin = "azure"
    network_policy = "azure"
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_virtual_network_with_subnet",
        "description": "Azure Virtual Network with subnets and NSG — equivalent of AWS VPC",
        "resource_types": [
            "azurerm_virtual_network",
            "azurerm_subnet",
            "azurerm_network_security_group",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_virtual_network" "main" {
  name                = var.vnet_name
  address_space       = ["10.0.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_subnet" "main" {
  name                 = "main-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_network_security_group" "main" {
  name                = "${var.vnet_name}-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  security_rule {
    name                       = "deny-all-inbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_redis_cache",
        "description": "Azure Redis Cache — equivalent of AWS ElastiCache Redis",
        "resource_types": ["azurerm_redis_cache", "azurerm_resource_group"],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_redis_cache" "main" {
  name                = var.redis_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  capacity            = 1
  family              = "C"
  sku_name            = "Standard"
  enable_non_ssl_port = false
  minimum_tls_version = "1.2"

  redis_configuration {
    maxmemory_policy = "allkeys-lru"
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_key_vault",
        "description": "Azure Key Vault for secrets and keys — equivalent of AWS Secrets Manager + KMS",
        "resource_types": ["azurerm_key_vault", "azurerm_key_vault_secret", "azurerm_resource_group"],
        "hcl_template": '''data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_key_vault" "main" {
  name                       = var.key_vault_name
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  purge_protection_enabled   = true

  access_policy {
    tenant_id = data.azurerm_client_config.current.tenant_id
    object_id = data.azurerm_client_config.current.object_id
    secret_permissions = ["Get", "List", "Set", "Delete"]
    key_permissions    = ["Get", "List", "Create", "Delete"]
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_service_bus_queue",
        "description": "Azure Service Bus namespace with queue — equivalent of AWS SQS",
        "resource_types": [
            "azurerm_servicebus_namespace",
            "azurerm_servicebus_queue",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_servicebus_namespace" "main" {
  name                = var.servicebus_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Standard"
}

resource "azurerm_servicebus_queue" "main" {
  name         = var.queue_name
  namespace_id = azurerm_servicebus_namespace.main.id

  lock_duration                  = "PT1M"
  max_size_in_megabytes          = 1024
  dead_lettering_on_message_expiration = true
}''',
    },
    # ─── GCP ─────────────────────────────────────────────────────────────────
    {
        "provider": "google",
        "pattern_name": "gcp_cloud_sql_postgres",
        "description": "Google Cloud SQL PostgreSQL instance — equivalent of AWS RDS",
        "resource_types": [
            "google_sql_database_instance",
            "google_sql_database",
            "google_sql_user",
        ],
        "hcl_template": '''resource "google_sql_database_instance" "main" {
  name             = var.db_instance_name
  database_version = "POSTGRES_15"
  region           = var.region
  deletion_protection = false

  settings {
    tier = "db-f1-micro"

    backup_configuration {
      enabled = true
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_self_link
    }
  }
}

resource "google_sql_database" "main" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "main" {
  name     = var.db_user
  instance = google_sql_database_instance.main.name
  password = var.db_password
}''',
    },
    {
        "provider": "google",
        "pattern_name": "gcp_cloud_run_service",
        "description": "Google Cloud Run v2 service — equivalent of AWS Lambda/ECS Fargate",
        "resource_types": [
            "google_cloud_run_v2_service",
            "google_service_account",
        ],
        "hcl_template": '''resource "google_service_account" "main" {
  account_id   = var.service_account_id
  display_name = "${var.service_name} service account"
  project      = var.project_id
}

resource "google_cloud_run_v2_service" "main" {
  name     = var.service_name
  location = var.region
  project  = var.project_id

  template {
    service_account = google_service_account.main.email

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}''',
    },
    {
        "provider": "google",
        "pattern_name": "gcp_storage_bucket",
        "description": "Google Cloud Storage bucket — equivalent of AWS S3",
        "resource_types": ["google_storage_bucket", "google_storage_bucket_iam_binding"],
        "hcl_template": '''resource "google_storage_bucket" "main" {
  name          = var.bucket_name
  location      = var.region
  project       = var.project_id
  force_destroy = false

  versioning {
    enabled = true
  }

  uniform_bucket_level_access = true
}''',
    },
    # ─── AZURE DATA / ANALYTICS ──────────────────────────────────────────────
    {
        "provider": "azurerm",
        "pattern_name": "azure_synapse_analytics",
        "description": "Azure Synapse Analytics workspace with dedicated SQL pool — equivalent of AWS Redshift",
        "resource_types": [
            "azurerm_synapse_workspace",
            "azurerm_synapse_sql_pool",
            "azurerm_storage_account",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_storage_account" "datalake" {
  name                     = var.storage_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  is_hns_enabled           = true
  min_tls_version          = "TLS1_2"
}

resource "azurerm_synapse_workspace" "main" {
  name                                 = var.synapse_workspace_name
  resource_group_name                  = azurerm_resource_group.main.name
  location                             = azurerm_resource_group.main.location
  storage_data_lake_gen2_filesystem_id = "${azurerm_storage_account.datalake.id}/blobServices/default/containers/${var.container_name}"
  sql_administrator_login              = var.sql_admin_login
  sql_administrator_login_password     = var.sql_admin_password
}

resource "azurerm_synapse_sql_pool" "main" {
  name                 = var.sql_pool_name
  synapse_workspace_id = azurerm_synapse_workspace.main.id
  sku_name             = "DW100c"
  create_mode          = "Default"
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_databricks_workspace",
        "description": "Azure Databricks workspace for Apache Spark — equivalent of AWS EMR or Glue Spark jobs",
        "resource_types": [
            "azurerm_databricks_workspace",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_databricks_workspace" "main" {
  name                = var.databricks_workspace_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "standard"

  tags = {
    environment = var.environment
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_data_factory_pipeline",
        "description": "Azure Data Factory with linked service — equivalent of AWS Glue ETL job",
        "resource_types": [
            "azurerm_data_factory",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_data_factory" "main" {
  name                = var.data_factory_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  identity {
    type = "SystemAssigned"
  }
}''',
    },
    {
        "provider": "azurerm",
        "pattern_name": "azure_event_driven_stack",
        "description": "Azure event-driven full stack: Functions + Service Bus + Event Grid — equivalent of Lambda + SQS + SNS",
        "resource_types": [
            "azurerm_linux_function_app",
            "azurerm_servicebus_namespace",
            "azurerm_servicebus_queue",
            "azurerm_storage_account",
            "azurerm_service_plan",
            "azurerm_resource_group",
        ],
        "hcl_template": '''resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_storage_account" "func" {
  name                     = var.storage_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
}

resource "azurerm_service_plan" "main" {
  name                = var.service_plan_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "Y1"
}

resource "azurerm_linux_function_app" "main" {
  name                       = var.function_app_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  storage_account_name       = azurerm_storage_account.func.name
  storage_account_access_key = azurerm_storage_account.func.primary_access_key
  service_plan_id            = azurerm_service_plan.main.id

  site_config {
    application_stack {
      python_version = "3.11"
    }
  }
}

resource "azurerm_servicebus_namespace" "main" {
  name                = var.servicebus_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Standard"
}

resource "azurerm_servicebus_queue" "main" {
  name         = var.queue_name
  namespace_id = azurerm_servicebus_namespace.main.id
}''',
    },
    # ─── GCP DATA ────────────────────────────────────────────────────────────
    {
        "provider": "google",
        "pattern_name": "gcp_bigquery_dataset",
        "description": "Google BigQuery dataset with table — equivalent of AWS Redshift / Athena",
        "resource_types": [
            "google_bigquery_dataset",
            "google_bigquery_table",
        ],
        "hcl_template": '''resource "google_bigquery_dataset" "main" {
  dataset_id                  = var.dataset_id
  friendly_name               = var.dataset_name
  location                    = var.region
  project                     = var.project_id
  delete_contents_on_destroy  = false

  default_table_expiration_ms = null
}

resource "google_bigquery_table" "main" {
  dataset_id = google_bigquery_dataset.main.dataset_id
  table_id   = var.table_id
  project    = var.project_id

  deletion_protection = true

  schema = var.table_schema
}''',
    },
    {
        "provider": "google",
        "pattern_name": "gcp_dataproc_spark_cluster",
        "description": "Google Dataproc Spark cluster — equivalent of AWS EMR",
        "resource_types": ["google_dataproc_cluster"],
        "hcl_template": '''resource "google_dataproc_cluster" "main" {
  name    = var.cluster_name
  region  = var.region
  project = var.project_id

  cluster_config {
    master_config {
      num_instances = 1
      machine_type  = "n1-standard-4"

      disk_config {
        boot_disk_type    = "pd-standard"
        boot_disk_size_gb = 100
      }
    }

    worker_config {
      num_instances = 2
      machine_type  = "n1-standard-4"

      disk_config {
        boot_disk_type    = "pd-standard"
        boot_disk_size_gb = 100
      }
    }

    software_config {
      image_version = "2.1-debian11"
      override_properties = {
        "dataproc:dataproc.allow.zero.workers" = "true"
      }
    }
  }
}''',
    },
    # ─── AWS ─────────────────────────────────────────────────────────────────
    {
        "provider": "aws",
        "pattern_name": "aws_rds_postgresql",
        "description": "AWS RDS PostgreSQL instance with subnet group and security group",
        "resource_types": [
            "aws_db_instance",
            "aws_db_subnet_group",
            "aws_security_group",
        ],
        "hcl_template": '''resource "aws_db_subnet_group" "main" {
  name       = "${var.db_name}-subnet-group"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "db" {
  name_prefix = "${var.db_name}-sg"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "main" {
  identifier        = var.db_name
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = "db.t3.micro"
  allocated_storage = 20
  storage_encrypted = true

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  backup_retention_period = 7
  skip_final_snapshot     = false
}''',
    },
    {
        "provider": "aws",
        "pattern_name": "aws_lambda_with_iam",
        "description": "AWS Lambda function with IAM role and CloudWatch log group",
        "resource_types": [
            "aws_lambda_function",
            "aws_iam_role",
            "aws_iam_role_policy_attachment",
            "aws_cloudwatch_log_group",
        ],
        "hcl_template": '''resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 14
}

resource "aws_iam_role" "lambda" {
  name = "${var.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "main" {
  function_name = var.function_name
  role          = aws_iam_role.lambda.arn
  handler       = var.handler
  runtime       = "python3.11"
  filename      = var.zip_file

  environment {
    variables = var.environment_variables
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic,
    aws_cloudwatch_log_group.lambda,
  ]
}''',
    },
]


def seed_patterns(force: bool = False) -> int:
    """Insert canonical patterns into tf_canonical_patterns table.

    Args:
        force: if True, DELETE existing patterns before inserting (full rebuild).

    Returns:
        Number of patterns inserted.
    """
    from rag.rag_config import get_pg_connection, EMBEDDING_MODEL
    from core.embedding_model_loader import get_shared_embedder

    embedder = get_shared_embedder(EMBEDDING_MODEL)

    texts = [
        f"{p['pattern_name']} {p['provider']} {p['description']} "
        f"{' '.join(p['resource_types'])}"
        for p in _PATTERNS
    ]
    embeddings = embedder.encode(texts, batch_size=16, show_progress_bar=False, convert_to_numpy=True).tolist()

    inserted = 0
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            if force:
                cur.execute("DELETE FROM tf_canonical_patterns")
                logger.info("tf_canonical_patterns truncated (force=True)")

            for pattern, embed_text, embedding in zip(_PATTERNS, texts, embeddings):
                try:
                    cur.execute(
                        """
                        INSERT INTO tf_canonical_patterns
                            (provider, pattern_name, description, resource_types,
                             hcl_template, embed_text, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (provider, pattern_name) DO UPDATE
                            SET description    = EXCLUDED.description,
                                resource_types = EXCLUDED.resource_types,
                                hcl_template   = EXCLUDED.hcl_template,
                                embed_text     = EXCLUDED.embed_text,
                                embedding      = EXCLUDED.embedding
                        """,
                        (
                            pattern["provider"],
                            pattern["pattern_name"],
                            pattern["description"],
                            pattern["resource_types"],
                            pattern["hcl_template"],
                            embed_text,
                            embedding,
                        ),
                    )
                    inserted += cur.rowcount
                except Exception as exc:
                    logger.warning("Failed to insert pattern %s: %s", pattern["pattern_name"], exc)

    logger.info("seed_canonical_patterns: %d patterns upserted", inserted)
    return inserted


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    force = "--force" in sys.argv
    n = seed_patterns(force=force)
    print(f"Done — {n} patterns upserted.")
