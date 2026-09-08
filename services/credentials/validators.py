"""
validators.py - Non-destructive credential validation for GitHub & cloud targets.

All validators:
  - NEVER create resources
  - Return a dict {valid: bool, ...} — never raise
  - Check real permissions (not just auth) when possible

Pitfalls handled:
  - GitHub fine-grained tokens don't expose X-OAuth-Scopes; we fall back to
    attribute probes on the repo object.
  - AWS IAM simulate_principal_policy may over-report `allowed` when Service
    Control Policies (SCPs) block the action; the caller should not treat
    `allowed` as a guarantee, only as a lower bound.
  - Credentials are NEVER logged, even partially.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("CredentialValidators")


# ─────────────────────────────────────────────────────────────────────────────
# GitHub
# ─────────────────────────────────────────────────────────────────────────────

def validate_github_token(token: str, expected_repo: str | None = None) -> dict:
    """Validate a GitHub PAT / fine-grained token.

    Args:
        token: GitHub token (classic PAT or fine-grained)
        expected_repo: 'owner/name' — if provided, verifies read + write access

    Returns:
        {valid, login, scopes, repo_access, can_write, default_branch, error?}
    """
    try:
        from github import Github, GithubException
    except ImportError:
        return {"valid": False, "error": "PyGithub not installed"}

    out: dict[str, Any] = {"valid": False}
    try:
        gh = Github(token)
        user = gh.get_user()
        out["login"] = user.login
    except Exception as e:
        return {"valid": False, "error": f"auth_failed: {type(e).__name__}"}

    # Scopes — only classic PATs expose them via the header.
    try:
        import httpx
        with httpx.Client(timeout=10) as client:
            r = client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"token {token}"},
            )
        scopes_raw = r.headers.get("X-OAuth-Scopes", "")
        out["scopes"] = [s.strip() for s in scopes_raw.split(",") if s.strip()]
        out["token_type"] = "classic" if scopes_raw else "fine_grained"
    except Exception:
        out["scopes"] = []
        out["token_type"] = "unknown"

    if expected_repo:
        try:
            repo = gh.get_repo(expected_repo)
            out["repo_access"] = True
            out["default_branch"] = repo.default_branch
            perms = repo.permissions
            if perms is not None:
                out["can_write"] = bool(perms.push)
                out["can_admin"] = bool(perms.admin)
            else:
                # Fine-grained tokens may not expose permissions; probe by
                # checking branch protection read access as a proxy.
                out["can_write"] = _probe_write_ability(repo)
                out["can_admin"] = False
        except Exception as e:
            out["repo_access"] = False
            out["error"] = f"repo_access_failed: {type(e).__name__}"
            return out

    out["valid"] = True
    return out


def _probe_write_ability(repo) -> bool:
    """Best-effort: try reading branch protection (requires push scope).

    Returns False if even this read fails. Does not create anything."""
    try:
        # Reading branches requires pull; creating a ref would require push.
        # This is a read-only probe that nonetheless signals auth level.
        _ = repo.default_branch
        # Try to read collaborators — often gated on push.
        collab = repo.get_collaborators()
        _ = list(collab[:1])
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# AWS
# ─────────────────────────────────────────────────────────────────────────────

_AWS_REQUIRED_ACTIONS = [
    "s3:CreateBucket",
    "iam:CreateRole",
    "lambda:CreateFunction",
    "ec2:RunInstances",
    "dynamodb:CreateTable",
]


def validate_aws_credentials(
    access_key: str,
    secret_key: str,
    target_region: str = "us-east-1",
    session_token: str | None = None,
) -> dict:
    """STS + IAM-simulator validation. No resources are created."""
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        return {"valid": False, "error": "boto3 not installed"}

    try:
        sts = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            region_name=target_region,
        )
        identity = sts.get_caller_identity()
    except Exception as e:
        return {"valid": False, "error": f"sts_failed: {type(e).__name__}"}

    out = {
        "valid": True,
        "account": identity.get("Account"),
        "arn": identity.get("Arn"),
        "user_id": identity.get("UserId"),
        "region": target_region,
        "region_accessible": False,
        "missing_permissions": [],
    }

    # Region probe — describe regions is read-only.
    try:
        ec2 = boto3.client(
            "ec2",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            region_name=target_region,
        )
        ec2.describe_regions(RegionNames=[target_region])
        out["region_accessible"] = True
    except Exception as e:
        out["region_accessible"] = False
        out["region_error"] = type(e).__name__

    # Permission simulation (read-only).
    try:
        iam = boto3.client(
            "iam",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
        )
        sim = iam.simulate_principal_policy(
            PolicySourceArn=identity["Arn"],
            ActionNames=_AWS_REQUIRED_ACTIONS,
            ResourceArns=["*"],
        )
        missing = [
            r["EvalActionName"] for r in sim.get("EvaluationResults", [])
            if r.get("EvalDecision") != "allowed"
        ]
        out["missing_permissions"] = missing
    except ClientError as e:
        # simulate_principal_policy often requires iam:SimulatePrincipalPolicy;
        # if the caller doesn't have it, skip gracefully.
        out["permission_check_error"] = e.response.get("Error", {}).get("Code")
    except Exception as e:
        out["permission_check_error"] = type(e).__name__

    return out


# ─────────────────────────────────────────────────────────────────────────────
# GCP
# ─────────────────────────────────────────────────────────────────────────────

_GCP_REQUIRED_PERMISSIONS = [
    "storage.buckets.create",
    "iam.serviceAccounts.create",
    "cloudfunctions.functions.create",
    "compute.instances.create",
]


def validate_gcp_credentials(service_account_json: dict, project_id: str) -> dict:
    """Project lookup + testIamPermissions (free, non-destructive)."""
    try:
        from google.oauth2 import service_account
        from google.cloud import resourcemanager_v3
        from googleapiclient.discovery import build
    except ImportError:
        return {"valid": False, "error": "google-cloud libraries not installed"}

    try:
        creds = service_account.Credentials.from_service_account_info(service_account_json)
    except Exception as e:
        return {"valid": False, "error": f"invalid_service_account_json: {type(e).__name__}"}

    try:
        client = resourcemanager_v3.ProjectsClient(credentials=creds)
        project = client.get_project(name=f"projects/{project_id}")
        project_number = project.name.split("/")[-1]
    except Exception as e:
        return {"valid": False, "error": f"project_not_accessible: {type(e).__name__}"}

    granted: list[str] = []
    try:
        crm = build("cloudresourcemanager", "v1", credentials=creds, cache_discovery=False)
        result = (
            crm.projects()
            .testIamPermissions(
                resource=project_id,
                body={"permissions": _GCP_REQUIRED_PERMISSIONS},
            )
            .execute()
        )
        granted = list(result.get("permissions", []))
    except Exception as e:
        logger.warning(f"GCP testIamPermissions failed: {e}")

    return {
        "valid": True,
        "project": project_id,
        "project_number": project_number,
        "service_account_email": creds.service_account_email,
        "granted_permissions": granted,
        "missing_permissions": [p for p in _GCP_REQUIRED_PERMISSIONS if p not in granted],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Azure
# ─────────────────────────────────────────────────────────────────────────────

_AZURE_CONTRIBUTOR_ROLE_ID = "b24988ac-6180-42a0-ab88-20f7382dd24c"
_AZURE_OWNER_ROLE_ID = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"


def validate_azure_credentials(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    subscription_id: str,
) -> dict:
    """Subscription probe + role assignment inspection + allowed regions detection."""
    try:
        from azure.identity import ClientSecretCredential
        from azure.mgmt.authorization import AuthorizationManagementClient
        from azure.mgmt.subscription import SubscriptionClient
        from azure.core.exceptions import ClientAuthenticationError
    except ImportError:
        return {"valid": False, "error": "azure-mgmt libraries not installed"}

    try:
        tenant_id = tenant_id.strip()
        client_id = client_id.strip()
        client_secret = client_secret.strip()
        subscription_id = subscription_id.strip()
        cred = ClientSecretCredential(tenant_id, client_id, client_secret)
        sub_client = SubscriptionClient(cred)
        sub = sub_client.subscriptions.get(subscription_id)
    except ClientAuthenticationError as e:
        detail = str(e).strip() or type(e).__name__
        return {"valid": False, "error": "auth_failed", "detail": detail}
    except Exception as e:
        detail = str(e).strip() or type(e).__name__
        return {"valid": False, "error": f"auth_failed: {type(e).__name__}", "detail": detail}

    roles: list[str] = []
    has_contributor = False
    has_owner = False
    try:
        auth_client = AuthorizationManagementClient(cred, subscription_id)
        scope = f"/subscriptions/{subscription_id}"
        assignments = auth_client.role_assignments.list_for_scope(
            scope, filter=f"principalId eq '{client_id}'"
        )
        for a in assignments:
            rd_id = (a.role_definition_id or "").split("/")[-1]
            roles.append(rd_id)
            if rd_id == _AZURE_CONTRIBUTOR_ROLE_ID:
                has_contributor = True
            if rd_id == _AZURE_OWNER_ROLE_ID:
                has_owner = True
    except Exception as e:
        logger.warning(f"Azure role listing failed: {e}")

    allowed_regions = get_azure_allowed_regions(tenant_id, client_id, client_secret, subscription_id)

    return {
        "valid": True,
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "subscription_name": getattr(sub, "display_name", None),
        "roles": roles,
        "has_contributor_role": has_contributor,
        "has_owner_role": has_owner,
        "allowed_regions": allowed_regions,
    }


# All known Azure regions with display labels — used as fallback when Policy API is unavailable
_AZURE_ALL_REGIONS = [
    {"value": "eastus",             "label": "eastus (Virginia)"},
    {"value": "eastus2",            "label": "eastus2 (Virginia)"},
    {"value": "westus2",            "label": "westus2 (Washington)"},
    {"value": "westus3",            "label": "westus3 (Arizona)"},
    {"value": "centralus",          "label": "centralus (Iowa)"},
    {"value": "westeurope",         "label": "westeurope (Netherlands)"},
    {"value": "northeurope",        "label": "northeurope (Ireland)"},
    {"value": "francecentral",      "label": "francecentral (Paris)"},
    {"value": "germanywestcentral", "label": "germanywestcentral (Frankfurt)"},
    {"value": "uksouth",            "label": "uksouth (London)"},
    {"value": "ukwest",             "label": "ukwest (Cardiff)"},
    {"value": "swedencentral",      "label": "swedencentral (Gävle)"},
    {"value": "norwayeast",         "label": "norwayeast (Oslo)"},
    {"value": "switzerlandnorth",   "label": "switzerlandnorth (Zurich)"},
    {"value": "polandcentral",      "label": "polandcentral (Warsaw)"},
    {"value": "italynorth",         "label": "italynorth (Milan)"},
    {"value": "spaincentral",       "label": "spaincentral (Madrid)"},
    {"value": "australiaeast",      "label": "australiaeast (Sydney)"},
    {"value": "southeastasia",      "label": "southeastasia (Singapore)"},
    {"value": "eastasia",           "label": "eastasia (Hong Kong)"},
    {"value": "japaneast",          "label": "japaneast (Tokyo)"},
    {"value": "koreacentral",       "label": "koreacentral (Seoul)"},
    {"value": "centralindia",       "label": "centralindia (Pune)"},
    {"value": "brazilsouth",        "label": "brazilsouth (São Paulo)"},
    {"value": "canadacentral",      "label": "canadacentral (Toronto)"},
    {"value": "southafricanorth",   "label": "southafricanorth (Johannesburg)"},
    {"value": "uaenorth",           "label": "uaenorth (Dubai)"},
]


def get_azure_allowed_regions(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    subscription_id: str,
) -> list[dict]:
    """Query Azure Policy to find the regions allowed for this subscription.

    Uses the Azure Resource Graph / Policy Insights API to find any 'deny' policy
    assignments that restrict allowed locations. If no restriction is found (or the
    API is unavailable), returns the full list of known regions.

    Returns:
        List of {value, label} dicts — the subset of regions allowed, or all regions
        as fallback.
    """
    try:
        import httpx
        from azure.identity import ClientSecretCredential

        cred = ClientSecretCredential(tenant_id.strip(), client_id.strip(), client_secret.strip())
        token = cred.get_token("https://management.azure.com/.default").token

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Query Policy assignments that restrict locations on this subscription
        url = (
            f"https://management.azure.com/subscriptions/{subscription_id.strip()}"
            f"/providers/Microsoft.Authorization/policyAssignments?api-version=2022-06-01"
        )
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers)

        if resp.status_code != 200:
            logger.warning("get_azure_allowed_regions: policy API returned %d — using full list", resp.status_code)
            return _AZURE_ALL_REGIONS

        assignments = resp.json().get("value", [])
        allowed_locations: set[str] = set()

        for assignment in assignments:
            params = assignment.get("properties", {}).get("parameters", {})
            # Standard "Allowed locations" built-in policy uses listOfAllowedLocations param
            for param_name in ("listOfAllowedLocations", "allowedLocations", "listOfAllowedValues"):
                param = params.get(param_name, {})
                values = param.get("value", [])
                if isinstance(values, list) and values:
                    for loc in values:
                        # Azure returns locations like "West Europe" or "westeurope" — normalise
                        normalized = loc.lower().replace(" ", "")
                        allowed_locations.add(normalized)

        if not allowed_locations:
            # No location-restricting policy found — all regions are available
            return _AZURE_ALL_REGIONS

        # Filter the known regions list against the allowed set
        filtered = [
            r for r in _AZURE_ALL_REGIONS
            if r["value"].lower().replace(" ", "") in allowed_locations
        ]
        # If the policy lists regions not in our known list, still return what we know
        return filtered if filtered else _AZURE_ALL_REGIONS

    except Exception as exc:
        logger.warning("get_azure_allowed_regions: failed (%s) — returning full region list", exc)
        return _AZURE_ALL_REGIONS
