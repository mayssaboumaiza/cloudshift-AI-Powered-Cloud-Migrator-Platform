import { ArrowUpDown, AlertTriangle, ShieldAlert, Info } from "lucide-react";
import { useState } from "react";
import StrategyBadge from "./StrategyBadge";
import ReasoningPanel from "./ReasoningPanel";

// Human-readable labels for common Terraform resource types
const TF_LABELS = {
  // AWS short slugs (produced by Agent 01 when no full TF type is available)
  iam:                                 "IAM / Identity",
  s3:                                  "Amazon S3",
  rds:                                 "Amazon RDS",
  lambda:                              "AWS Lambda",
  ec2:                                 "EC2 Instance",
  ecs:                                 "Amazon ECS",
  eks:                                 "Amazon EKS",
  sqs:                                 "Amazon SQS",
  sns:                                 "Amazon SNS",
  dynamodb:                            "Amazon DynamoDB",
  cloudfront:                          "CloudFront",
  route53:                             "Route 53",
  elasticache:                         "ElastiCache",
  kinesis:                             "Amazon Kinesis",
  sagemaker:                           "Amazon SageMaker",
  cognito:                             "Amazon Cognito",
  openai:                              "OpenAI",
  // AWS full resource types
  aws_s3_bucket:                       "Amazon S3",
  aws_lambda_function:                 "AWS Lambda",
  aws_db_instance:                     "Amazon RDS",
  aws_rds_cluster:                     "Amazon Aurora",
  aws_rds_cluster_instance:            "Aurora Instance",
  aws_dynamodb_table:                  "Amazon DynamoDB",
  aws_sqs_queue:                       "Amazon SQS",
  aws_sns_topic:                       "Amazon SNS",
  aws_ecs_cluster:                     "Amazon ECS",
  aws_ecs_service:                     "ECS Service",
  aws_eks_cluster:                     "Amazon EKS",
  aws_ecr_repository:                  "Amazon ECR",
  aws_cloudfront_distribution:         "CloudFront",
  aws_route53_zone:                    "Route 53",
  aws_alb:                             "Application Load Balancer",
  aws_lb:                              "Load Balancer",
  aws_elasticache_cluster:             "ElastiCache",
  aws_elasticsearch_domain:            "Amazon OpenSearch",
  aws_kinesis_stream:                  "Amazon Kinesis",
  aws_glue_job:                        "AWS Glue",
  aws_emr_cluster:                     "Amazon EMR",
  aws_sagemaker_endpoint:              "Amazon SageMaker",
  aws_iam_role:                        "IAM Role",
  aws_iam_policy:                      "IAM Policy",
  aws_security_group:                  "Security Group",
  aws_vpc:                             "Amazon VPC",
  aws_subnet:                          "Subnet",
  aws_instance:                        "EC2 Instance",
  aws_autoscaling_group:               "Auto Scaling Group",
  aws_cloudwatch_log_group:            "CloudWatch Logs",
  aws_secretsmanager_secret:           "Secrets Manager",
  aws_kms_key:                         "AWS KMS",
  aws_ssm_parameter:                   "SSM Parameter Store",
  aws_apigatewayv2_api:                "API Gateway v2",
  aws_api_gateway_rest_api:            "API Gateway REST",
  aws_cognito_user_pool:               "Amazon Cognito",
  aws_msk_cluster:                     "Amazon MSK (Kafka)",
  aws_opensearch_domain:               "Amazon OpenSearch",
  // Azure target
  azurerm_storage_account:             "Azure Blob Storage",
  azurerm_storage_container:           "Storage Container",
  azurerm_function_app:                "Azure Functions",
  azurerm_linux_function_app:          "Azure Functions (Linux)",
  azurerm_postgresql_flexible_server:  "Azure Database for PostgreSQL",
  azurerm_mysql_flexible_server:       "Azure Database for MySQL",
  azurerm_mssql_server:                "Azure SQL Server",
  azurerm_mssql_database:              "Azure SQL Database",
  azurerm_cosmosdb_account:            "Azure Cosmos DB",
  azurerm_servicebus_namespace:        "Azure Service Bus",
  azurerm_eventhub_namespace:          "Azure Event Hubs",
  azurerm_container_app:               "Azure Container Apps",
  azurerm_kubernetes_cluster:          "Azure Kubernetes Service",
  azurerm_container_registry:          "Azure Container Registry",
  azurerm_cdn_profile:                 "Azure CDN",
  azurerm_dns_zone:                    "Azure DNS",
  azurerm_application_gateway:         "Azure Application Gateway",
  azurerm_lb:                          "Azure Load Balancer",
  azurerm_redis_cache:                 "Azure Cache for Redis",
  azurerm_search_service:              "Azure AI Search",
  azurerm_stream_analytics_job:        "Azure Stream Analytics",
  azurerm_data_factory:                "Azure Data Factory",
  azurerm_hdinsight_hadoop_cluster:    "Azure HDInsight",
  azurerm_machine_learning_workspace:  "Azure Machine Learning",
  azurerm_cognitive_account:           "Azure Cognitive Services",
  azurerm_role_assignment:             "RBAC Role Assignment",
  azurerm_user_assigned_identity:      "Managed Identity",
  azurerm_network_security_group:      "Network Security Group",
  azurerm_virtual_network:             "Azure Virtual Network",
  azurerm_subnet:                      "Subnet",
  azurerm_linux_virtual_machine:       "Linux VM",
  azurerm_windows_virtual_machine:     "Windows VM",
  azurerm_monitor_diagnostic_setting:  "Azure Monitor",
  azurerm_key_vault:                   "Azure Key Vault",
  azurerm_key_vault_secret:            "Key Vault Secret",
  azurerm_api_management:              "Azure API Management",
  azurerm_active_directory_b2c_tenant: "Azure AD B2C",
  azurerm_eventhub:                    "Event Hub",
  // GCP target
  google_storage_bucket:               "Google Cloud Storage",
  google_cloudfunctions_function:      "Cloud Functions",
  google_cloudfunctions2_function:     "Cloud Functions (Gen2)",
  google_sql_database_instance:        "Cloud SQL",
  google_bigtable_instance:            "Cloud Bigtable",
  google_firestore_document:           "Firestore",
  google_pubsub_topic:                 "Pub/Sub Topic",
  google_pubsub_subscription:          "Pub/Sub Subscription",
  google_container_cluster:            "Google Kubernetes Engine",
  google_container_registry:           "Container Registry",
  google_artifact_registry_repository: "Artifact Registry",
  google_compute_instance:             "Compute Engine",
  google_compute_autoscaler:           "Autoscaler",
  google_dns_managed_zone:             "Cloud DNS",
  google_redis_instance:               "Cloud Memorystore (Redis)",
  google_bigquery_dataset:             "BigQuery",
  google_dataflow_job:                 "Cloud Dataflow",
  google_dataproc_cluster:             "Cloud Dataproc",
  google_ml_engine_model:              "Vertex AI",
  google_project_iam_binding:          "IAM Binding",
  google_service_account:              "Service Account",
  google_secret_manager_secret:        "Secret Manager",
  google_kms_key_ring:                 "Cloud KMS",
};

function tfLabel(resourceType) {
  if (!resourceType) return null;
  return TF_LABELS[resourceType] || null;
}

function CostCell({ cost, budgetOk, breakdown = [], basis }) {
  const [hover, setHover] = useState(false);
  const hasBreakdown = breakdown.length > 0;
  return (
    <span
      style={{ position: "relative", display: "inline-block" }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <span style={{
        color: budgetOk ? "#34d399" : "#f87171",
        fontWeight: 600, fontSize: 12,
        cursor: hasBreakdown || basis ? "help" : "default",
        borderBottom: hasBreakdown || basis ? "1px dashed currentColor" : "none",
      }}>
        {cost.toFixed(0)} €
        {!budgetOk && <span title="Dépasse le budget" style={{ marginLeft: 3 }}>⚠</span>}
      </span>

      {hover && (hasBreakdown || basis) && (
        <div style={{
          position: "absolute", bottom: "calc(100% + 6px)", left: "50%",
          transform: "translateX(-50%)",
          background: "#1e293b", border: "1px solid #334155",
          borderRadius: 8, padding: "10px 12px",
          fontSize: 11, color: "#e2e8f0",
          whiteSpace: "nowrap", zIndex: 999,
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          minWidth: 220,
        }}>
          {hasBreakdown && (
            <>
              <div style={{ fontWeight: 700, marginBottom: 6, color: "#94a3b8", letterSpacing: "0.05em", fontSize: 10, textTransform: "uppercase" }}>
                Détail du coût
              </div>
              {breakdown.map((line, i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 16, marginBottom: 4 }}>
                  <span style={{ color: "#cbd5e1" }}>{line.label}</span>
                  <span style={{ fontWeight: 700, color: line.amount != null ? "#34d399" : "#64748b" }}>
                    {line.amount != null ? `${line.amount.toFixed(2)} $` : "—"}
                  </span>
                </div>
              ))}
              <div style={{ borderTop: "1px solid #334155", marginTop: 6, paddingTop: 6, display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontWeight: 700 }}>Total estimé</span>
                <span style={{ fontWeight: 700, color: "#34d399" }}>{cost.toFixed(2)} €</span>
              </div>
            </>
          )}
          {basis && (
            <div style={{ marginTop: hasBreakdown ? 6 : 0, color: "#64748b", fontSize: 10, borderTop: hasBreakdown ? "none" : "none" }}>
              Base de calcul : <span style={{ color: "#94a3b8" }}>{basis}</span>
            </div>
          )}
          <div style={{ color: "#475569", fontSize: 9, marginTop: 4 }}>
            * Estimation — hors réductions et accords tarifaires
          </div>
        </div>
      )}
    </span>
  );
}

const COMPLEXITY_COLORS = {
  HIGH: { color: "#f87171", bg: "#3b111144" },
  MEDIUM: { color: "#fbbf24", bg: "#1a2e1a44" },
  LOW: { color: "#34d399", bg: "#0f2a1a" },
};

const STRATEGY_COLORS = {
  REHOST:     { color: "#60a5fa", bg: "#172554" },
  REPLATFORM: { color: "#34d399", bg: "#052e16" },
  REFACTOR:   { color: "#f59e0b", bg: "#2d1a00" },
  RETIRE:     { color: "#9ca3af", bg: "#1f2937" },
  RETAIN:     { color: "#6b7280", bg: "#111827" },
  REPURCHASE: { color: "#a78bfa", bg: "#1e1b4b" },
  RELOCATE:   { color: "#38bdf8", bg: "#082f49" },
};

export default function MigrationPriority({ plan, graph }) {
  const [sortKey, setSortKey] = useState("migrate_order");
  const [sortAsc, setSortAsc] = useState(true);

  const resources = plan?.resources || [];
  if (resources.length === 0) {
    return <p className="muted">Aucune ressource dans le plan de migration.</p>;
  }

  const toggleSort = (key) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(true); }
  };

  // Build enriched table from plan resources + graph nodes
  const graphNodes = graph?.resources?.nodes || [];
  const nodeMap = {};
  graphNodes.forEach((n) => {
    nodeMap[n.id] = n;
    const normalized = n.id.toLowerCase()
      .replace(/^(aws|gcp|azure|azurerm)-/, "")
      .replace(/-(bucket|function|table|role|policy|group|cluster|instance|queue|topic)$/, "");
    if (normalized !== n.id) nodeMap[normalized] = n;
  });

  const rows = resources.map((r, i) => {
    const svcKey = r.service || r.source_service || r.service_name || "";
    const node = nodeMap[svcKey] || nodeMap[svcKey.toLowerCase()] || {};
    const planScore = r.composite_score ?? r.score ?? null;
    const score = planScore !== null && planScore > 0 ? planScore : 0;
    const monthlyCost = r.monthly_cost_eur ?? r.monthly_cost_estimate ?? null;
    const breakdown   = r.cost_breakdown ?? r.breakdown ?? [];
    const costBasis   = r.cost_basis ?? r.basis ?? null;
    return {
      order: i + 1,
      service: svcKey || "?",
      // terraform_resource is the canonical TF type (e.g. "azurerm_user_assigned_identity")
      // resource_name is a short slug (e.g. "iam") — not useful for TF_LABELS lookup
      terraform_resource: r.terraform_resource || r.target_equivalent || r.target_service || null,
      type: node.type || r.type || r.category || "unknown",
      score,
      complexity: r.complexity || node.complexity || "LOW",
      strategy: (r.strategy_7r || r.strategy || "MIGRATE").toUpperCase(),
      target_service: r.target_service || r.target_equivalent || null,
      monthly_cost: monthlyCost,
      breakdown,
      cost_basis: costBasis,
      budget_ok: r.budget_ok !== false,
      rejected: r.rejected === true,
      rejection_reason: r.rejection_reason || null,
      effort_estimate: r.effort_estimate ?? null,
      reasoning: r.reasoning || r.modernity_note || null,
      maturity_status: r.maturity_status || "GA",
      preview_flag: r.preview_flag === true,
      llm_target_service_suspect: r.llm_target_service_suspect || null,
      alerts: Array.isArray(r.alerts) ? r.alerts : [],
      // AHP / 7R confidence fields
      confidence:       r.confidence ?? null,
      certainty:        r.certainty  ?? "high",
      tie_detected:     r.tie_detected === true,
      tie_gap:          r.tie_gap ?? null,
      trigger_ask_human: r.trigger_ask_human === true,
      uncertainty_reason: r.uncertainty_reason || null,
      reasoning_detail: typeof r.reasoning === "object" && r.reasoning !== null
        ? r.reasoning
        : null,
      reasoning_text: typeof r.reasoning === "string" ? r.reasoning : (r.modernity_note || null),
    };
  });

  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    const cmp = typeof av === "number" ? av - bv : String(av).localeCompare(String(bv));
    return sortAsc ? cmp : -cmp;
  });

  const SortHeader = ({ field, children }) => (
    <th onClick={() => toggleSort(field)} style={{ cursor: "pointer" }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        {children}
        {sortKey === field && <ArrowUpDown size={12} />}
      </span>
    </th>
  );

  // Summary stats
  const toMigrate = resources.filter((r) => {
    const s = (r.strategy_7r || r.strategy || "").toUpperCase();
    return s !== "RETAIN" && s !== "RETIRE";
  }).length;
  const retained = resources.filter((r) => {
    const s = (r.strategy_7r || r.strategy || "").toUpperCase();
    return s === "RETAIN" || s === "RETIRE";
  }).length;
  const totalCost = resources.reduce((acc, r) => {
    return acc + (r.monthly_cost_eur ?? r.monthly_cost_estimate ?? 0);
  }, 0);

  return (
    <div className="priority-wrapper">
      <div className="priority-summary">
        <div className="priority-stat">
          <span className="priority-num">{resources.length}</span>
          <span>ressources</span>
        </div>
        <div className="priority-stat">
          <span className="priority-num" style={{ color: "#60a5fa" }}>{toMigrate}</span>
          <span>à migrer</span>
        </div>
        <div className="priority-stat">
          <span className="priority-num" style={{ color: "#6b7280" }}>{retained}</span>
          <span>retain/retire</span>
        </div>
        {totalCost > 0 && (
          <div className="priority-stat">
            <span className="priority-num" style={{ color: "#34d399" }}>
              {totalCost.toFixed(0)} €
            </span>
            <span>/mois estimé</span>
          </div>
        )}
        {resources.some((r) => r.rejected) && (
          <div className="priority-stat">
            <span className="priority-num" style={{ color: "#dc2626" }}>
              {resources.filter((r) => r.rejected).length}
            </span>
            <span>rejetés</span>
          </div>
        )}
      </div>

      <div className="table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <SortHeader field="order">#</SortHeader>
              <SortHeader field="service">Service</SortHeader>
              <SortHeader field="type">Type</SortHeader>
              <SortHeader field="strategy">Stratégie</SortHeader>
              <SortHeader field="maturity_status">Maturité</SortHeader>
              <th>Service cible</th>
              <SortHeader field="score">Score</SortHeader>
              <SortHeader field="complexity">Complexité</SortHeader>
              <SortHeader field="monthly_cost">Coût/mois</SortHeader>
              <th>Statut / Alertes</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const cc = COMPLEXITY_COLORS[r.complexity] || COMPLEXITY_COLORS.LOW;
              const sc = STRATEGY_COLORS[r.strategy] || { color: "#9ca3af", bg: "#1f2937" };
              const hasHighAlert = r.alerts.some((a) => a.level === "HIGH");
              const alertTitle = r.alerts.map((a) => `[${a.level}] ${a.message}`).join("\n");
              return (
                <tr key={r.order} style={r.rejected ? { background: "rgba(220,38,38,0.04)" } : {}}>
                  <td>
                    <span className="order-badge">{r.order}</span>
                  </td>
                  <td className="cell-service">
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <span
                        title={r.reasoning_text || undefined}
                        style={{ cursor: r.reasoning_text ? "help" : "default" }}
                      >
                        {/* Label principal = service SOURCE lisible */}
                        {tfLabel(r.service) || r.service}
                        {/* Sous-titre = type Terraform source (slug court ou type complet) */}
                        <div style={{ fontSize: 10, color: "var(--muted)", fontFamily: "monospace", marginTop: 1 }}>
                          {r.service}
                        </div>
                      </span>
                      {r.llm_target_service_suspect && (
                        <ShieldAlert
                          size={12}
                          color="#f59e0b"
                          title={`Cible suspecte : ${r.llm_target_service_suspect}`}
                        />
                      )}
                      {r.trigger_ask_human && !r.tie_detected && (
                        <span title={r.uncertainty_reason || "Zone grise"} style={{ fontSize: 11, cursor: "help" }}>⚠️</span>
                      )}
                      {r.tie_detected && (
                        <span title="Égalité détectée — confirmation requise" style={{ fontSize: 11, cursor: "help" }}>🔀</span>
                      )}
                    </div>
                    {r.reasoning_detail && (
                      <ReasoningPanel
                        service={r}
                        reasoning={r.reasoning_detail}
                        score={r.score}
                        ahpCR={0.0175}
                      />
                    )}
                  </td>
                  <td><span className="tag tag-type">{r.type}</span></td>
                  <td>
                    <StrategyBadge
                      strategy={r.strategy}
                      confidence={r.confidence}
                      certainty={r.certainty}
                      tieDetected={r.tie_detected}
                      triggerAskHuman={r.trigger_ask_human}
                      size="sm"
                    />
                  </td>
                  <td>
                    {r.preview_flag ? (
                      <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px",
                        background: "#fef9c3", color: "#854d0e",
                        border: "1px solid #fbbf24", borderRadius: 8 }}>
                        PREVIEW
                      </span>
                    ) : (
                      <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px",
                        background: "#ecfdf5", color: "#047857",
                        border: "1px solid #10b981", borderRadius: 8 }}>
                        {r.maturity_status}
                      </span>
                    )}
                  </td>
                  <td className="cell-target">
                    {r.target_service ? (
                      <div>
                        <span className="tag tag-type" style={{ color: "var(--accent)" }}>
                          {tfLabel(r.target_service) || r.target_service}
                        </span>
                        {tfLabel(r.target_service) && (
                          <div style={{ fontSize: 10, color: "var(--muted)", fontFamily: "monospace", marginTop: 2 }}>
                            {r.target_service}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span style={{ color: "var(--muted)", fontSize: 12 }}>—</span>
                    )}
                  </td>
                  <td>
                    <span className="tag tag-score">
                      {r.score > 0 ? r.score.toFixed(2) : "—"}
                    </span>
                  </td>
                  <td>
                    <span className="tag" style={{ color: cc.color, background: cc.bg }}>
                      {r.complexity}
                    </span>
                  </td>
                  <td style={{ whiteSpace: "nowrap", position: "relative" }}>
                    {r.monthly_cost != null && r.monthly_cost > 0
                      ? (
                        <CostCell
                          cost={r.monthly_cost}
                          budgetOk={r.budget_ok}
                          breakdown={r.breakdown}
                          basis={r.cost_basis}
                        />
                      )
                      : <span style={{ color: "var(--muted)", fontSize: 12 }}>—</span>
                    }
                  </td>
                  <td>
                    {r.rejected ? (
                      <span title={r.rejection_reason || "Rejeté"} style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        color: "#dc2626", fontSize: 11, fontWeight: 600,
                        cursor: r.rejection_reason ? "help" : "default",
                      }}>
                        <AlertTriangle size={12} />
                        Rejeté
                        {r.rejection_reason && (
                          <span style={{
                            color: "#9ca3af", fontWeight: 400, maxWidth: 140,
                            overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap", display: "inline-block",
                          }}>
                            — {r.rejection_reason}
                          </span>
                        )}
                      </span>
                    ) : r.alerts.length > 0 ? (
                      <span title={alertTitle} style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        color: hasHighAlert ? "#f59e0b" : "#6b7280",
                        fontSize: 11, fontWeight: 600, cursor: "help",
                      }}>
                        <Info size={12} />
                        {r.alerts.length} alerte{r.alerts.length > 1 ? "s" : ""}
                      </span>
                    ) : (
                      <span style={{ color: "#16a34a", fontSize: 11, fontWeight: 600 }}>✓ OK</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
