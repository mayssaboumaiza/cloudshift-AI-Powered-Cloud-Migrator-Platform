"""
core/region_constraints.py — Azure service regional availability constraints.

Single source of truth used by:
  - agents/iac_generator/iac_fixers.py  (post-generation fix pass)
  - agents/migration_planner/service_lookup_tools.py  (pre-planning check, Agent 01)

When a new "LocationNotAvailableForResourceType" error is observed, add the
terraform resource type and its supported regions here.
"""
from __future__ import annotations

# ── Per-resource supported regions ────────────────────────────────────────────
# Maps Terraform resource type → frozenset of Azure regions where it is available.
# Absence from this dict means "available everywhere" (no known constraint).
AZURE_RESOURCE_REGION_CONSTRAINTS: dict[str, frozenset[str]] = {

    # ── AI / Cognitive Services ───────────────────────────────────────────────
    "azurerm_cognitive_account": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "canadaeast",
        "centralindia", "centralus", "eastasia", "eastus", "eastus2",
        "francecentral", "germanywestcentral", "italynorth", "japaneast",
        "japanwest", "jioindiacentral", "jioindiawest", "koreacentral",
        "northcentralus", "northeurope", "norwayeast", "polandcentral",
        "qatarcentral", "southafricanorth", "southcentralus", "southeastasia",
        "southindia", "spaincentral", "swedencentral", "switzerlandnorth",
        "switzerlandwest", "uaenorth", "uksouth", "ukwest", "westcentralus",
        "westeurope", "westus", "westus2", "westus3",
    }),
    "azurerm_cognitive_deployment": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "canadaeast",
        "centralindia", "centralus", "eastasia", "eastus", "eastus2",
        "francecentral", "germanywestcentral", "italynorth", "japaneast",
        "japanwest", "jioindiacentral", "jioindiawest", "koreacentral",
        "northcentralus", "northeurope", "norwayeast", "polandcentral",
        "qatarcentral", "southafricanorth", "southcentralus", "southeastasia",
        "southindia", "spaincentral", "swedencentral", "switzerlandnorth",
        "switzerlandwest", "uaenorth", "uksouth", "ukwest", "westcentralus",
        "westeurope", "westus", "westus2", "westus3",
    }),

    # ── Machine Learning ──────────────────────────────────────────────────────
    "azurerm_machine_learning_workspace": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralindia",
        "centralus", "eastasia", "eastus", "eastus2", "francecentral",
        "japaneast", "koreacentral", "northcentralus", "northeurope",
        "norwayeast", "southcentralus", "southeastasia", "southindia",
        "swedencentral", "switzerlandnorth", "uksouth", "westcentralus",
        "westeurope", "westus", "westus2", "westus3",
    }),
    "azurerm_machine_learning_compute_cluster": frozenset({
        "australiaeast", "canadacentral", "centralus", "eastus", "eastus2",
        "francecentral", "japaneast", "northeurope", "southcentralus",
        "southeastasia", "swedencentral", "uksouth", "westeurope", "westus2",
    }),

    # ── AI Search ─────────────────────────────────────────────────────────────
    "azurerm_search_service": frozenset({
        "australiaeast", "australiasoutheast", "brazilsouth", "canadacentral",
        "canadaeast", "centralindia", "centralus", "eastasia", "eastus",
        "eastus2", "francecentral", "japaneast", "japanwest", "koreacentral",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "southindia", "swedencentral", "switzerlandnorth", "uksouth", "ukwest",
        "westcentralus", "westeurope", "westus", "westus2",
    }),

    # ── Bot Services ──────────────────────────────────────────────────────────
    "azurerm_bot_service_azure_bot": frozenset({
        "westus", "eastus", "westeurope", "northeurope", "southeastasia",
        "uksouth", "japaneast", "australiaeast",
    }),
    "azurerm_bot_channels_registration": frozenset({
        "westus", "eastus", "westeurope", "northeurope", "southeastasia",
        "uksouth", "japaneast", "australiaeast",
    }),

    # ── Logic Apps ISE ────────────────────────────────────────────────────────
    "azurerm_logic_app_integration_service_environment": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralus",
        "eastasia", "eastus", "eastus2", "francecentral", "japaneast",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "swedencentral", "uksouth", "westeurope", "westus", "westus2",
    }),

    # ── Healthcare APIs ───────────────────────────────────────────────────────
    "azurerm_healthcare_service": frozenset({
        "australiaeast", "canadacentral", "centralindia", "eastus", "eastus2",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "swedencentral", "uksouth", "westeurope", "westus2",
    }),
    "azurerm_healthcare_workspace": frozenset({
        "australiaeast", "canadacentral", "centralindia", "eastus", "eastus2",
        "northeurope", "southcentralus", "swedencentral", "uksouth",
        "westeurope", "westus2",
    }),

    # ── Spring Apps ───────────────────────────────────────────────────────────
    "azurerm_spring_cloud_service": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralus",
        "eastasia", "eastus", "eastus2", "japaneast", "northeurope",
        "southcentralus", "southeastasia", "swedencentral", "uksouth",
        "westeurope", "westus2",
    }),

    # ── Service Fabric ────────────────────────────────────────────────────────
    "azurerm_service_fabric_cluster": frozenset({
        "australiaeast", "brazilsouth", "canadacentral", "centralindia",
        "centralus", "eastasia", "eastus", "eastus2", "japaneast",
        "northcentralus", "northeurope", "southcentralus", "southeastasia",
        "swedencentral", "uksouth", "westeurope", "westus", "westus2",
    }),
}

# ── Service-name aliases → Terraform resource type ────────────────────────────
# Agent 01 calls check_region_availability with human-readable service names.
# This map resolves them to the canonical Terraform resource type so the
# constraint table above can be consulted.
SERVICE_NAME_TO_RESOURCE_TYPE: dict[str, str] = {
    # Cognitive / OpenAI
    "azure_openai":            "azurerm_cognitive_account",
    "openai":                  "azurerm_cognitive_account",
    "cognitive_services":      "azurerm_cognitive_account",
    "cognitive":               "azurerm_cognitive_account",
    "azurerm_cognitive_account": "azurerm_cognitive_account",
    "azurerm_cognitive_deployment": "azurerm_cognitive_deployment",
    # Machine Learning
    "azure_ml":                "azurerm_machine_learning_workspace",
    "machine_learning":        "azurerm_machine_learning_workspace",
    "ml_workspace":            "azurerm_machine_learning_workspace",
    "azurerm_machine_learning_workspace": "azurerm_machine_learning_workspace",
    # Search
    "azure_search":            "azurerm_search_service",
    "cognitive_search":        "azurerm_search_service",
    "search":                  "azurerm_search_service",
    "azurerm_search_service":  "azurerm_search_service",
    # Bot
    "bot_service":             "azurerm_bot_service_azure_bot",
    "azure_bot":               "azurerm_bot_service_azure_bot",
    # Healthcare
    "healthcare":              "azurerm_healthcare_workspace",
    "health_data_services":    "azurerm_healthcare_workspace",
    # Spring
    "spring_apps":             "azurerm_spring_cloud_service",
    "azure_spring_apps":       "azurerm_spring_cloud_service",
    # Service Fabric
    "service_fabric":          "azurerm_service_fabric_cluster",
}

# ── Geographic fallback priority ───────────────────────────────────────────────
# Ordered list of fallback regions per user region (same geography first).
REGION_FALLBACK_PRIORITY: dict[str, list[str]] = {
    # Europe
    "austriaeast":        ["swedencentral", "germanywestcentral", "westeurope", "northeurope", "uksouth"],
    "polandcentral":      ["swedencentral", "germanywestcentral", "westeurope", "northeurope"],
    "italynorth":         ["swedencentral", "westeurope", "francecentral", "northeurope"],
    "spaincentral":       ["swedencentral", "westeurope", "francecentral", "northeurope"],
    "germanywestcentral": ["swedencentral", "westeurope", "northeurope", "uksouth"],
    "germanynorth":       ["swedencentral", "germanywestcentral", "westeurope", "northeurope"],
    "switzerlandnorth":   ["swedencentral", "westeurope", "germanywestcentral", "northeurope"],
    "switzerlandwest":    ["swedencentral", "westeurope", "germanywestcentral", "northeurope"],
    "norwayeast":         ["swedencentral", "northeurope", "westeurope", "uksouth"],
    "norwaywest":         ["swedencentral", "northeurope", "westeurope"],
    "swedencentral":      ["westeurope", "northeurope", "uksouth", "francecentral"],
    "westeurope":         ["swedencentral", "northeurope", "uksouth", "francecentral"],
    "northeurope":        ["swedencentral", "westeurope", "uksouth", "francecentral"],
    "uksouth":            ["swedencentral", "westeurope", "northeurope", "ukwest"],
    "ukwest":             ["uksouth", "swedencentral", "westeurope", "northeurope"],
    "francecentral":      ["swedencentral", "westeurope", "northeurope", "uksouth"],
    "francesouth":        ["francecentral", "swedencentral", "westeurope"],
    "israelcentral":      ["swedencentral", "westeurope", "northeurope"],
    # Americas
    "eastus":             ["eastus2", "centralus", "northcentralus", "westus2"],
    "eastus2":            ["eastus", "centralus", "northcentralus", "westus2"],
    "northcentralus":     ["eastus", "eastus2", "centralus", "westus2"],
    "southcentralus":     ["eastus", "eastus2", "centralus", "westus2"],
    "centralus":          ["eastus", "eastus2", "northcentralus", "westus2"],
    "westcentralus":      ["westus2", "westus", "eastus", "centralus"],
    "westus":             ["westus2", "eastus", "centralus", "westus3"],
    "westus2":            ["westus", "eastus", "centralus", "westus3"],
    "westus3":            ["westus2", "westus", "eastus", "centralus"],
    "canadacentral":      ["eastus", "eastus2", "canadaeast", "centralus"],
    "canadaeast":         ["canadacentral", "eastus", "eastus2"],
    "brazilsouth":        ["eastus", "eastus2", "southcentralus", "canadacentral"],
    "brazilsoutheast":    ["brazilsouth", "eastus", "eastus2"],
    "mexicocentral":      ["southcentralus", "eastus", "eastus2"],
    # Asia Pacific
    "southeastasia":      ["eastasia", "japaneast", "centralindia", "australiaeast"],
    "eastasia":           ["southeastasia", "japaneast", "koreacentral", "centralindia"],
    "japaneast":          ["japanwest", "southeastasia", "eastasia", "koreacentral"],
    "japanwest":          ["japaneast", "southeastasia", "eastasia"],
    "koreacentral":       ["koreasouth", "japaneast", "southeastasia", "eastasia"],
    "koreasouth":         ["koreacentral", "japaneast", "southeastasia"],
    "centralindia":       ["southindia", "westindia", "southeastasia", "eastasia"],
    "southindia":         ["centralindia", "westindia", "southeastasia"],
    "westindia":          ["centralindia", "southindia", "southeastasia"],
    "jioindiacentral":    ["centralindia", "southindia", "southeastasia"],
    "jioindiawest":       ["centralindia", "southindia", "southeastasia"],
    "australiaeast":      ["australiasoutheast", "southeastasia", "japaneast"],
    "australiasoutheast": ["australiaeast", "southeastasia", "japaneast"],
    "australiacentral":   ["australiaeast", "australiasoutheast", "southeastasia"],
    "australiacentral2":  ["australiaeast", "australiasoutheast", "southeastasia"],
    # Middle East & Africa
    "uaenorth":           ["swedencentral", "westeurope", "northeurope", "uksouth"],
    "uaecentral":         ["uaenorth", "westeurope", "northeurope"],
    "qatarcentral":       ["uaenorth", "swedencentral", "westeurope", "uksouth"],
    "southafricanorth":   ["westeurope", "northeurope", "swedencentral", "uksouth"],
    "southafricawest":    ["southafricanorth", "westeurope", "northeurope"],
}


# ── Data residency → allowed Azure region sets ────────────────────────────────
# When the user specifies a data residency requirement, fallback regions must
# stay within the allowed geography.  Used by find_best_fallback().
DATA_RESIDENCY_ALLOWED_REGIONS: dict[str, frozenset[str]] = {
    "European Union": frozenset({
        "westeurope", "northeurope", "swedencentral", "uksouth", "ukwest",
        "francecentral", "francesouth", "germanywestcentral", "germanynorth",
        "switzerlandnorth", "switzerlandwest", "norwayeast", "norwaywest",
        "polandcentral", "italynorth", "spaincentral", "austriaeast",
        "israelcentral",
    }),
    "Strictement France": frozenset({"francecentral", "francesouth"}),
    "US": frozenset({
        "eastus", "eastus2", "westus", "westus2", "westus3",
        "centralus", "northcentralus", "southcentralus", "westcentralus",
        "canadacentral", "canadaeast",
    }),
    "Asia Pacific": frozenset({
        "southeastasia", "eastasia", "japaneast", "japanwest",
        "koreacentral", "koreasouth", "centralindia", "southindia", "westindia",
        "australiaeast", "australiasoutheast",
    }),
    "Middle East": frozenset({"uaenorth", "uaecentral", "qatarcentral"}),
    "Africa":       frozenset({"southafricanorth", "southafricawest"}),
}


def find_best_fallback(
    user_region: str,
    supported: frozenset[str],
    data_residency: str = "",
) -> str | None:
    """Return the nearest supported region for *user_region* respecting data residency.

    Priority:
      1. Walk the geographic fallback list for user_region.
      2. If data_residency is set, only consider regions in the allowed set.
      3. If no priority match, pick the first alphabetically within allowed regions.

    Returns None only when no supported region satisfies the data residency constraint
    (caller should surface this as an error — not silently deploy to wrong geography).
    """
    allowed = DATA_RESIDENCY_ALLOWED_REGIONS.get(data_residency, frozenset())
    candidates = supported & allowed if allowed else supported

    for candidate in REGION_FALLBACK_PRIORITY.get(user_region, []):
        if candidate in candidates:
            return candidate

    # Fallback: alphabetically first within the constrained set
    return next(iter(sorted(candidates)), None)


def check_service_region(service_name: str, region: str, data_residency: str = "") -> dict:
    """Check whether *service_name* is available in *region* and return a recommendation.

    Args:
        service_name: Terraform resource type or human-readable alias
                      (e.g. "azurerm_cognitive_account", "azure_openai", "search").
        region:       Normalised Azure region string (e.g. "austriaeast").

    Returns:
        {
          "available":          bool,
          "resource_type":      str,   # resolved Terraform type
          "recommended_region": str | None,  # nearest supported region if unavailable
          "supported_regions":  list[str],   # full supported set (sorted)
          "reason":             str,
        }
    """
    region_norm = region.lower().replace(" ", "")
    svc_norm = service_name.lower().replace("-", "_").replace(" ", "_")

    # Resolve alias → canonical resource type
    resource_type = SERVICE_NAME_TO_RESOURCE_TYPE.get(svc_norm, svc_norm)
    constraints = AZURE_RESOURCE_REGION_CONSTRAINTS.get(resource_type)

    if constraints is None:
        # No known constraint — assume available
        return {
            "available": True,
            "resource_type": resource_type,
            "recommended_region": None,
            "supported_regions": [],
            "reason": "no-known-constraint",
        }

    available = region_norm in constraints
    recommended = None if available else find_best_fallback(
        region_norm, constraints, data_residency=data_residency
    )

    # Detect data residency violation: recommended region outside allowed geography
    residency_violation = False
    if recommended and data_residency:
        allowed = DATA_RESIDENCY_ALLOWED_REGIONS.get(data_residency, frozenset())
        if allowed and recommended not in allowed:
            residency_violation = True
            recommended = None  # no safe fallback within the required geography

    return {
        "available": available,
        "resource_type": resource_type,
        "recommended_region": recommended,
        "supported_regions": sorted(constraints),
        "residency_violation": residency_violation,
        "reason": (
            "region-supported" if available
            else (
                f"No region for {resource_type} satisfies both availability and "
                f"data_residency='{data_residency}' — manual override required"
                if residency_violation
                else f"'{region_norm}' not in supported regions for {resource_type}"
            )
        ),
    }
