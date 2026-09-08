"""
cloud_api_scanner.py - Optional cloud account scan when no IaC exists.

Used as fallback when the repo has no Terraform/Bicep/CFN and user provided
credentials. Read-only; never creates resources. Tag-based filtering is the
only supported scoping so we don't return the user's entire account.

This module is intentionally best-effort: any SDK failure returns an empty
list so the pipeline degrades gracefully.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("CloudAPIScanner")


def scan_cloud_account(
    provider: str,
    credentials: dict,
    tag_filter: dict | None = None,
    region: str | None = None,
) -> list[dict]:
    """Scan a cloud account for existing resources, read-only.

    Args:
        provider: 'aws' | 'gcp' | 'azure'
        credentials: provider-specific dict
            aws: {access_key_id, secret_access_key, [session_token]}
            gcp: {service_account_json, project_id}
            azure: {tenant_id, client_id, client_secret, subscription_id}
        tag_filter: optional {"Project": "my-app"} — resources must match ALL tags
        region: scope to a single region (AWS/Azure only — GCP is multi-regional)

    Returns:
        list of {provider, service_type, service, handle, evidence, tags}
    """
    provider = (provider or "").lower()
    if provider == "aws":
        return _scan_aws(credentials, tag_filter, region)
    if provider == "gcp":
        return _scan_gcp(credentials, tag_filter)
    if provider == "azure":
        return _scan_azure(credentials, tag_filter, region)
    logger.warning(f"scan_cloud_account: unknown provider '{provider}'")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# AWS
# ─────────────────────────────────────────────────────────────────────────────
def _scan_aws(credentials: dict, tag_filter: dict | None, region: str | None) -> list[dict]:
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed — AWS scan skipped")
        return []

    try:
        session = boto3.Session(
            aws_access_key_id=credentials.get("access_key_id"),
            aws_secret_access_key=credentials.get("secret_access_key"),
            aws_session_token=credentials.get("session_token"),
            region_name=region or credentials.get("region") or "us-east-1",
        )
        # Resource Groups Tagging API returns ARN + tags for all tagged resources.
        client = session.client("resourcegroupstaggingapi")
        kwargs: dict[str, Any] = {}
        if tag_filter:
            kwargs["TagFilters"] = [
                {"Key": k, "Values": [v] if isinstance(v, str) else list(v)}
                for k, v in tag_filter.items()
            ]

        resources: list[dict] = []
        paginator = client.get_paginator("get_resources")
        for page in paginator.paginate(**kwargs):
            for row in page.get("ResourceTagMappingList", []):
                arn = row.get("ResourceARN", "")
                tags = {t["Key"]: t["Value"] for t in row.get("Tags", [])}
                svc, svc_type = _aws_arn_to_service(arn)
                if not svc:
                    continue
                resources.append({
                    "provider": "aws",
                    "service": svc,
                    "service_type": svc_type,
                    "handle": arn,
                    "evidence": "resourcegroupstaggingapi",
                    "tags": tags,
                })
        return resources

    except Exception as exc:
        logger.warning(f"AWS scan failed: {exc}")
        return []


_AWS_ARN_SERVICE_MAP = {
    "s3":           ("s3", "storage"),
    "dynamodb":     ("dynamodb", "key-value-nosql"),
    "lambda":       ("lambda", "serverless-functions"),
    "sqs":          ("sqs", "message-queue"),
    "sns":          ("sns", "message-queue"),
    "rds":          ("rds", "managed-relational-db"),
    "ec2":          ("ec2", "virtual-machines"),
    "ecs":          ("ecs", "container-compute"),
    "eks":          ("eks", "managed-kubernetes"),
    "apigateway":   ("apigateway", "api-gateway"),
    "secretsmanager": ("secretsmanager", "secrets-management"),
    "kms":          ("kms", "secrets-management"),
    "iam":          ("iam", "identity-access-management"),
    "cloudwatch":   ("cloudwatch", "observability"),
    "logs":         ("cloudwatch-logs", "observability"),
    "bedrock":      ("bedrock", "llm-inference"),
    "sagemaker":    ("sagemaker", "ml-training"),
    "kinesis":      ("kinesis", "event-streaming"),
    "elasticache":  ("elasticache", "cache"),
    "opensearch":   ("opensearch", "search"),
}


def _aws_arn_to_service(arn: str) -> tuple[str | None, str | None]:
    # arn:aws:SERVICE:REGION:ACCOUNT:RESOURCE
    parts = arn.split(":")
    if len(parts) < 3:
        return None, None
    svc = parts[2]
    mapping = _AWS_ARN_SERVICE_MAP.get(svc)
    if mapping:
        return mapping
    return svc, "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# GCP
# ─────────────────────────────────────────────────────────────────────────────
def _scan_gcp(credentials: dict, tag_filter: dict | None) -> list[dict]:
    try:
        from google.oauth2 import service_account
        from google.cloud import asset_v1
    except ImportError:
        logger.warning("google-cloud-asset not installed — GCP scan skipped")
        return []

    try:
        sa_json = credentials.get("service_account_json")
        project_id = credentials.get("project_id")
        if not sa_json or not project_id:
            return []

        creds = service_account.Credentials.from_service_account_info(sa_json)
        client = asset_v1.AssetServiceClient(credentials=creds)

        # Cloud Asset Inventory — list all GCP assets in the project.
        parent = f"projects/{project_id}"
        resources: list[dict] = []
        for asset in client.list_assets(request={"parent": parent, "content_type": "RESOURCE"}):
            asset_type = asset.asset_type  # e.g. "storage.googleapis.com/Bucket"
            svc, svc_type = _gcp_asset_to_service(asset_type)
            if not svc:
                continue
            labels = dict(asset.resource.data.get("labels", {})) if asset.resource else {}
            # Tag filter: require ALL labels to match
            if tag_filter and not all(labels.get(k) == v for k, v in tag_filter.items()):
                continue
            resources.append({
                "provider": "gcp",
                "service": svc,
                "service_type": svc_type,
                "handle": asset.name,
                "evidence": "cloudasset",
                "tags": labels,
            })
        return resources

    except Exception as exc:
        logger.warning(f"GCP scan failed: {exc}")
        return []


_GCP_ASSET_MAP = {
    "storage.googleapis.com/Bucket":              ("cloud-storage", "storage"),
    "bigquery.googleapis.com/Dataset":            ("bigquery", "analytics-warehouse"),
    "run.googleapis.com/Service":                 ("cloud-run", "serverless-functions"),
    "cloudfunctions.googleapis.com/CloudFunction": ("cloud-functions", "serverless-functions"),
    "pubsub.googleapis.com/Topic":                ("pubsub", "message-queue"),
    "pubsub.googleapis.com/Subscription":         ("pubsub", "message-queue"),
    "sqladmin.googleapis.com/Instance":           ("cloud-sql", "managed-relational-db"),
    "container.googleapis.com/Cluster":           ("gke", "managed-kubernetes"),
    "firestore.googleapis.com/Database":          ("firestore", "key-value-nosql"),
    "aiplatform.googleapis.com/Endpoint":         ("vertex-ai", "llm-inference"),
    "secretmanager.googleapis.com/Secret":        ("secret-manager", "secrets-management"),
}


def _gcp_asset_to_service(asset_type: str) -> tuple[str | None, str | None]:
    return _GCP_ASSET_MAP.get(asset_type, (None, None))


# ─────────────────────────────────────────────────────────────────────────────
# Azure
# ─────────────────────────────────────────────────────────────────────────────
def _scan_azure(credentials: dict, tag_filter: dict | None, region: str | None) -> list[dict]:
    try:
        from azure.identity import ClientSecretCredential
        from azure.mgmt.resource import ResourceManagementClient
    except ImportError:
        logger.warning("azure-mgmt-resource not installed — Azure scan skipped")
        return []

    try:
        cred = ClientSecretCredential(
            tenant_id=credentials["tenant_id"],
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
        )
        client = ResourceManagementClient(cred, credentials["subscription_id"])

        resources: list[dict] = []
        for r in client.resources.list():
            # r.type looks like "Microsoft.Storage/storageAccounts"
            tags = dict(r.tags or {})
            if tag_filter and not all(tags.get(k) == v for k, v in tag_filter.items()):
                continue
            if region and (r.location or "").lower() != region.lower():
                continue
            svc, svc_type = _azure_type_to_service(r.type)
            if not svc:
                continue
            resources.append({
                "provider": "azure",
                "service": svc,
                "service_type": svc_type,
                "handle": r.id,
                "evidence": "resources.list",
                "tags": tags,
            })
        return resources

    except Exception as exc:
        logger.warning(f"Azure scan failed: {exc}")
        return []


_AZURE_TYPE_MAP = {
    "Microsoft.Storage/storageAccounts":          ("storage-account", "storage"),
    "Microsoft.DocumentDB/databaseAccounts":      ("cosmos", "key-value-nosql"),
    "Microsoft.Web/sites":                        ("app-service", "serverless-functions"),
    "Microsoft.ServiceBus/namespaces":            ("service-bus", "message-queue"),
    "Microsoft.EventHub/namespaces":              ("event-hubs", "event-streaming"),
    "Microsoft.KeyVault/vaults":                  ("key-vault", "secrets-management"),
    "Microsoft.DBforPostgreSQL/servers":          ("postgresql", "managed-relational-db"),
    "Microsoft.DBforPostgreSQL/flexibleServers":  ("postgresql", "managed-relational-db"),
    "Microsoft.ContainerService/managedClusters": ("aks", "managed-kubernetes"),
    "Microsoft.CognitiveServices/accounts":       ("azure-openai", "llm-inference"),
}


def _azure_type_to_service(azure_type: str) -> tuple[str | None, str | None]:
    return _AZURE_TYPE_MAP.get(azure_type, (None, None))
