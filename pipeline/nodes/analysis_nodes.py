"""
analysis_nodes.py - Nœuds LangGraph de la phase d'analyse du dépôt.

Responsabilités :
  detect_changes_node           — Path 1 vs Path 2 decision (FIRST node in graph)
  check_analysis_node          — valide le dependency_graph produit par stack_analyzer
  notify_service_selection_node — émet l'événement SSE pour la checklist frontend
  ask_user_services_node        — construit le dependency_graph depuis la sélection manuelle
"""
import logging

from agents.pipeline_state import MigrationState
from agents.node_decorator import publish_events, _run_async_from_sync
from agents.checkpoint_events import EventPublisher
from services.stack_analyzer import _get_service_type, _get_complexity, _TYPE_PRIORITY

logger = logging.getLogger("Graph.Analysis")


# ─────────────────────────────────────────────────────────────────────────────
# detect_changes_node  — Path 1 vs Path 2 decision
# ─────────────────────────────────────────────────────────────────────────────

async def detect_changes_node(state: MigrationState) -> dict:
    """First node in the pipeline. Decides Path 1 (full) or Path 2 (incremental).

    Injects into state:
      migration_mode          : "full" | "incremental" | "up_to_date"
      detected_changes        : structured diff dict
      previous_migration_id   : id of the previous successful migration (if any)
      incremental_resources   : resource types to regenerate (Path 2 only)
      incremental_files       : Python files to re-migrate (Path 2 only)

    If mode == "up_to_date" the graph router skips all downstream nodes.
    """
    repo_url     = state.get("repo_url", "")
    target_cloud = state.get("target_cloud", "")
    migration_id = state.get("migration_id", "")
    github_token = state.get("source_files", {}).get("_github_token", "") or ""

    # Retrieve GitHub token from Vault if available
    if not github_token:
        try:
            from services.credentials.broker import get_secret_broker
            broker = get_secret_broker()
            token_data = broker.get(broker.migration_path(migration_id, "github_token"))
            if token_data:
                github_token = token_data.get("token", "")
        except Exception:
            pass
    # Fallback to env var
    if not github_token:
        import os
        github_token = os.environ.get("GITHUB_TOKEN", "")

    if not repo_url or not target_cloud:
        logger.warning("detect_changes_node: missing repo_url or target_cloud — defaulting to full")
        return {"migration_mode": "full", "detected_changes": {}}

    try:
        from configuration.database import async_session
        from services.change_detector import detect_migration_mode

        async with async_session() as db:
            result = await detect_migration_mode(
                repo_url=repo_url,
                target_cloud=target_cloud,
                github_token=github_token,
                db_session=db,
            )

        logger.info(
            "detect_changes_node: migration=%s mode=%s summary=%s",
            migration_id, result.mode, result.summary,
        )

        # Determine which resources/files to scope in incremental mode
        incremental_resources: list[str] = []
        incremental_files: list[str] = []

        if result.mode == "incremental":
            incremental_resources = result.new_resources
            # Consider modified Python + added Python files as needing re-migration
            code_exts = (".py", ".ipynb", ".txt", ".env")
            incremental_files = [
                f for f in (result.added_files + result.modified_files)
                if any(f.endswith(ext) for ext in code_exts)
            ]
            logger.info(
                "detect_changes_node: incremental scope — %d resources, %d code files",
                len(incremental_resources), len(incremental_files),
            )

        return {
            "migration_mode":         result.mode,
            "detected_changes":       result.to_dict(),
            "previous_migration_id":  result.previous_migration_id,
            "incremental_resources":  incremental_resources,
            "incremental_files":      incremental_files,
        }

    except Exception as exc:
        logger.error("detect_changes_node: unexpected error — %s (defaulting to full)", exc)
        return {"migration_mode": "full", "detected_changes": {}}


# ─────────────────────────────────────────────────────────────────────────────
# check_analysis_node
# ─────────────────────────────────────────────────────────────────────────────

def check_analysis_node(state: MigrationState) -> MigrationState:
    """Valide le dependency_graph produit par stack_analyzer.

    Vérifie :
    - dependency_graph présent
    - detected_cloud est un provider connu (aws / gcp / azure / multi)
    - au moins un service cloud détecté
    """
    errors = list(state.get("errors") or [])
    dep_graph = state.get("dependency_graph")

    if not dep_graph:
        errors.append(
            "No dependency graph produced by stack analyzer. "
            "Check the repo URL or cloud service detection."
        )
        return {"errors": errors}

    detected_cloud = (dep_graph.get("detected_cloud") or "").lower()
    resources = dep_graph.get("resources", [])

    _VALID_CLOUDS = {"aws", "gcp", "azure", "multi"}
    if detected_cloud not in _VALID_CLOUDS:
        errors.append(
            f"Unknown or unsupported cloud provider detected: '{detected_cloud}'. "
            "Expected one of: aws, gcp, azure, multi."
        )

    if not resources:
        errors.append(
            "No cloud services detected in the repository. "
            "Cannot generate migration plan without source services."
        )

    if errors:
        logger.error("[Analysis] check_analysis: %d error(s) — %s", len(errors), errors)
    else:
        _r = resources.get("nodes", []) if isinstance(resources, dict) else resources
        n = len(_r) if isinstance(_r, (list, tuple)) else 1
        if detected_cloud == "multi":
            dist = dep_graph.get("cloud_distribution", {})
            logger.info(
                "[Analysis] check_analysis: ✓ multi-cloud source=%s, %d resource(s)",
                dist, n,
            )
        else:
            logger.info(
                "[Analysis] check_analysis: ✓ cloud=%s, %d resource(s)", detected_cloud, n
            )

        # Enrich dynamic tags only for detected resource IDs (not the whole catalog)
        _trigger_scoped_enrichment(dep_graph, detected_cloud)

    # Propagate cloud_distribution to the pipeline state when multi-cloud is detected
    updates: dict = {"errors": errors}
    if detected_cloud == "multi":
        updates["cloud_distribution"] = dep_graph.get("cloud_distribution", {})

    return updates


def _trigger_scoped_enrichment(dep_graph: dict, source_cloud: str) -> None:
    """Launch DynamicTagEnricher in background, scoped to detected resource IDs only."""
    import threading
    import os

    if not os.environ.get("AZURE_AI_ENDPOINT", "").strip():
        return

    resources = dep_graph.get("resources", [])
    nodes = resources.get("nodes", []) if isinstance(resources, dict) else resources
    if not nodes:
        return

    resource_ids = [
        n.get("terraform_type") or n.get("id") or n.get("type")
        for n in (nodes if isinstance(nodes, list) else [])
        if isinstance(n, dict)
    ]
    resource_ids = [r for r in resource_ids if r]

    if not resource_ids:
        return

    target_cloud = "azure"

    def _run():
        try:
            from rag.dynamic_tag_enricher import enrich_all_missing
            n = enrich_all_missing(
                source_cloud=source_cloud,
                target_cloud=target_cloud,
                resource_ids=resource_ids,
            )
            logger.info(
                "[Analysis] Scoped dynamic enrichment: %d/%d resource(s) enriched for this migration",
                n, len(resource_ids),
            )
        except Exception as exc:
            logger.warning("[Analysis] Scoped dynamic enrichment failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="DynamicEnricher-scoped").start()
    logger.info(
        "[Analysis] Scoped enrichment launched for %d resource(s): %s",
        len(resource_ids), resource_ids,
    )


# ─────────────────────────────────────────────────────────────────────────────
# notify_service_selection_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="service_selection")
def notify_service_selection_node(state: MigrationState) -> MigrationState:
    """Émet l'événement SSE pour afficher la checklist de sélection dans le frontend.

    Déclenché uniquement quand stack_analyzer n'a trouvé aucun fichier IaC.
    Le graphe est interrompu avant ask_user_services et attend que l'utilisateur
    poste POST /migrations/{id}/submit-services.
    """
    thread_id = state.get("thread_id", "unknown")
    source_cloud = state.get("source_cloud", "unknown")
    try:
        pub = EventPublisher(thread_id)
        _run_async_from_sync(
            pub.custom(
                event_type="service_selection_required",
                phase="service_selection",
                details={"source_cloud": source_cloud},
            )
        )
        logger.info("[Analysis] service_selection_required emitted (cloud=%s)", source_cloud)
    except Exception as exc:
        logger.warning("[Analysis] notify_service_selection: could not emit event: %s", exc)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# ask_user_services_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="service_selection")
def ask_user_services_node(state: MigrationState) -> MigrationState:
    """Construit le dependency_graph à partir des services sélectionnés manuellement.

    Appelé après la reprise du graphe via update_state() avec user_selected_services.
    Convertit la liste plate de services au format dependency_graph attendu par
    Agent 01 et Agent 02.
    """
    selected = state.get("user_selected_services") or []
    source_cloud = state.get("source_cloud", "unknown")

    if not selected:
        logger.error("[Analysis] ask_user_services: no services selected — aborting")
        return {"errors": ["No cloud services selected. Please select at least one service."]}

    logger.info("[Analysis] ask_user_services: building graph from %d selected service(s)", len(selected))

    # Default contextual_hints per well-known AWS service id.
    # These hints give the LLM enough context to run its ReAct loop correctly
    # even when no Terraform files are present (user_selection path).
    # Keys follow the same schema as stack_analyzer contextual_hints so the
    # Agent 01 system prompt rules apply unchanged.
    _DEFAULT_HINTS: dict[str, dict] = {
        # ── Databases ─────────────────────────────────────────────────────────
        "rds":      {"engine": "postgres", "instance_class": "db.t3.micro",
                     "hint": "Amazon RDS (PostgreSQL by default). Map to azurerm_postgresql_flexible_server on Azure."},
        "aurora":   {"engine": "aurora-postgresql", "instance_class": "db.t3.medium",
                     "hint": "Amazon Aurora PostgreSQL. Map to azurerm_postgresql_flexible_server on Azure."},
        "dynamodb": {"hint": "Amazon DynamoDB (NoSQL). Map to azurerm_cosmosdb_account on Azure."},
        "elasticache": {"hint": "Amazon ElastiCache (Redis). Map to azurerm_redis_cache on Azure."},
        "redshift": {"hint": "Amazon Redshift (data warehouse). Map to azurerm_mssql_server on Azure."},
        "documentdb": {"hint": "Amazon DocumentDB (MongoDB-compatible). Map to azurerm_cosmosdb_account on Azure."},
        # ── Storage ───────────────────────────────────────────────────────────
        "s3":       {"hint": "Amazon S3 object storage. Map to azurerm_storage_account on Azure."},
        "efs":      {"hint": "Amazon EFS shared filesystem. Map to azurerm_storage_share on Azure."},
        # ── Compute ───────────────────────────────────────────────────────────
        "lambda":   {"hint": "AWS Lambda serverless. Map to azurerm_linux_function_app on Azure."},
        "ec2":      {"hint": "AWS EC2 virtual machine. Map to azurerm_linux_web_app or azurerm_virtual_machine on Azure."},
        "ecs":      {"hint": "AWS ECS managed containers. Map to azurerm_container_app on Azure."},
        "eks":      {"hint": "AWS EKS managed Kubernetes. Map to azurerm_kubernetes_cluster on Azure."},
        "fargate":  {"hint": "AWS Fargate serverless containers. Map to azurerm_container_app on Azure."},
        "ecr":      {"hint": "AWS ECR container registry. Map to azurerm_container_registry on Azure."},
        # ── AI/ML ─────────────────────────────────────────────────────────────
        "bedrock":  {"ai_service": "bedrock",
                     "hint": "AWS Bedrock managed LLMs. Map to azurerm_machine_learning_workspace on Azure."},
        "sagemaker":{"ai_service": "sagemaker",
                     "hint": "AWS SageMaker ML platform. Map to azurerm_machine_learning_workspace on Azure."},
        # ── IAM / Security ────────────────────────────────────────────────────
        "iam":      {"iam_trust_service": {"azure_target": "azurerm_user_assigned_identity",
                                           "hint": "AWS IAM → Azure Managed Identity (free). strategy_7r=REFACTOR."},
                     "hint": "AWS IAM identities and permissions. Map to azurerm_user_assigned_identity + azurerm_role_assignment on Azure."},
        "secretsmanager": {"hint": "AWS Secrets Manager. Map to azurerm_key_vault + azurerm_key_vault_secret on Azure."},
        "cognito":  {"hint": "AWS Cognito user auth. Map to azurerm_user_assigned_identity or Entra ID on Azure."},
        # ── Messaging ─────────────────────────────────────────────────────────
        "sqs":      {"hint": "AWS SQS message queue. Map to azurerm_servicebus_queue on Azure."},
        "sns":      {"hint": "AWS SNS pub/sub. Map to azurerm_servicebus_namespace on Azure."},
        "kinesis":  {"hint": "AWS Kinesis data streaming. Map to azurerm_eventhub_namespace on Azure."},
        "eventbridge": {"hint": "AWS EventBridge event bus. Map to azurerm_eventgrid_topic on Azure."},
        "msk":      {"hint": "AWS MSK managed Kafka. Map to azurerm_eventhub_namespace on Azure."},
        # ── Network ───────────────────────────────────────────────────────────
        "vpc":         {"hint": "AWS VPC. Map to azurerm_virtual_network on Azure."},
        "api-gateway": {"hint": "AWS API Gateway. Map to azurerm_api_management on Azure."},
        "cloudfront":  {"hint": "AWS CloudFront CDN. Map to azurerm_cdn_frontdoor_profile on Azure."},
        "elb":         {"hint": "AWS Load Balancer. Map to azurerm_load_balancer on Azure."},
        # ── Monitoring ────────────────────────────────────────────────────────
        "cloudwatch":  {"hint": "AWS CloudWatch logs & metrics. Map to azurerm_log_analytics_workspace + azurerm_application_insights on Azure."},
        # ── Search ────────────────────────────────────────────────────────────
        "opensearch":  {"hint": "AWS OpenSearch. Map to azurerm_search_service on Azure."},
    }

    nodes = []
    for svc in selected:
        svc_name = svc.get("service", "")
        if not svc_name:
            continue
        cloud = svc.get("cloud") or source_cloud
        svc_type = svc.get("type") or _get_service_type(svc_name)
        score = 4  # priorité medium par défaut pour les services déclarés par l'utilisateur
        # Inject default contextual_hints so Agent 01 LLM has enough context
        # to run its ReAct loop correctly without Terraform source files.
        hints = _DEFAULT_HINTS.get(svc_name.lower(), {})
        node = {
            "id":              svc_name,
            "type":            svc_type,
            "cloud":           cloud,
            "score":           score,
            "complexity":      _get_complexity(score),
            "source":          "user_selection",
            "cloud_confirmed": True,
        }
        if hints:
            node["contextual_hints"] = hints
        nodes.append(node)

    # Build implicit edges: IAM connects to all others; AI services connect to
    # data services (storage, database). These edges are logical approximations
    # since the user has not provided a dependency graph.
    edges = []
    node_ids = [n["id"] for n in nodes]
    _iam_nodes   = [n["id"] for n in nodes if n["type"] in ("iam", "security")]
    _ai_nodes    = [n["id"] for n in nodes if n["type"] == "ai"]
    _data_nodes  = [n["id"] for n in nodes if n["type"] in ("database", "storage")]
    _other_nodes = [
        n["id"] for n in nodes
        if n["type"] not in ("iam", "security", "ai", "database", "storage")
    ]
    seen_edges: set[tuple] = set()

    def _add_edge(src: str, tgt: str, label: str) -> None:
        key = (src, tgt)
        if key not in seen_edges and src != tgt:
            seen_edges.add(key)
            edges.append({"source": src, "target": tgt, "label": label})

    for iam_id in _iam_nodes:
        for other_id in node_ids:
            if other_id != iam_id:
                _add_edge(iam_id, other_id, "manages_access")
    for ai_id in _ai_nodes:
        for data_id in _data_nodes:
            _add_edge(ai_id, data_id, "reads_data")
    for other_id in _other_nodes:
        for data_id in _data_nodes:
            _add_edge(other_id, data_id, "uses")

    dependency_graph = {
        "detected_cloud":         source_cloud,
        "detection_mode":         "user_selection",
        "cloud_detection_method": "user_input",
        "resources":              {"nodes": nodes, "edges": edges},
        "files_analyzed":         0,
        "needs_service_selection": False,
    }

    sorted_nodes = sorted(nodes, key=lambda n: _TYPE_PRIORITY.get(n["type"], 8))

    return {
        "dependency_graph": dependency_graph,
        "needs_service_selection": False,
        "source_services_inventory": [
            {
                "service":    n["id"],
                "type":       n["type"],
                "score":      n["score"],
                "complexity": n["complexity"],
                "cloud":      n["cloud"],
                "source":     "user_selection",
            }
            for n in nodes
        ],
        "migration_priority": [
            {
                "service":       n["id"],
                "score":         n["score"],
                "complexity":    n["complexity"],
                "migrate_order": i + 1,
                "type":          n["type"],
                "type_priority": _TYPE_PRIORITY.get(n["type"], 8),
            }
            for i, n in enumerate(sorted_nodes)
        ],
        "errors": [],
    }
