import { memo } from "react";
import { Handle, Position } from "@xyflow/react";

/* Inject node-appear keyframe once at module load */
if (typeof document !== "undefined" && !document.getElementById("svc-node-kf")) {
  const s = document.createElement("style");
  s.id = "svc-node-kf";
  s.textContent = `
    @keyframes svc-appear {
      from { opacity: 0; transform: scale(0.88); }
      to   { opacity: 1; transform: scale(1); }
    }
  `;
  document.head.appendChild(s);
}

const TYPE_ICONS = {
  compute:    "⚙",
  storage:    "🗄",
  database:   "🗃",
  network:    "🌐",
  iam:        "🔒",
  messaging:  "💬",
  monitoring: "📊",
  ai:         "🤖",
  other:      "☁",
};

// Human-readable names for AWS, Azure, and GCP resource types
const SERVICE_LABELS = {
  // AWS source
  aws_s3_bucket:                      "Amazon S3",
  aws_lambda_function:                "AWS Lambda",
  aws_db_instance:                    "Amazon RDS",
  aws_rds_cluster:                    "Amazon Aurora",
  aws_dynamodb_table:                 "Amazon DynamoDB",
  aws_sqs_queue:                      "Amazon SQS",
  aws_sns_topic:                      "Amazon SNS",
  aws_ecs_cluster:                    "Amazon ECS",
  aws_eks_cluster:                    "Amazon EKS",
  aws_ecr_repository:                 "Amazon ECR",
  aws_iam_role:                       "IAM Role",
  aws_iam_policy:                     "IAM Policy",
  aws_vpc:                            "Amazon VPC",
  aws_instance:                       "EC2 Instance",
  aws_cloudfront_distribution:        "CloudFront",
  aws_route53_zone:                   "Route 53",
  aws_alb:                            "Load Balancer",
  aws_lb:                             "Load Balancer",
  aws_elasticache_cluster:            "ElastiCache",
  aws_elasticsearch_domain:           "Amazon OpenSearch",
  aws_opensearch_domain:              "Amazon OpenSearch",
  aws_kinesis_stream:                 "Amazon Kinesis",
  aws_sagemaker_endpoint:             "Amazon SageMaker",
  aws_secretsmanager_secret:          "Secrets Manager",
  aws_kms_key:                        "AWS KMS",
  aws_apigatewayv2_api:               "API Gateway",
  aws_cognito_user_pool:              "Amazon Cognito",
  aws_msk_cluster:                    "Amazon MSK (Kafka)",
  // Short slugs used by Agent 01
  iam:                                "IAM / Identity",
  s3:                                 "Amazon S3",
  rds:                                "Amazon RDS",
  lambda:                             "AWS Lambda",
  ec2:                                "EC2 Instance",
  ecs:                                "Amazon ECS",
  eks:                                "Amazon EKS",
  sqs:                                "Amazon SQS",
  sns:                                "Amazon SNS",
  dynamodb:                           "Amazon DynamoDB",
  openai:                             "OpenAI",
  // Azure target
  azurerm_storage_account:            "Azure Blob Storage",
  azurerm_postgresql_flexible_server: "Azure PostgreSQL",
  azurerm_mysql_flexible_server:      "Azure MySQL",
  azurerm_cosmosdb_account:           "Azure Cosmos DB",
  azurerm_user_assigned_identity:     "Managed Identity",
  azurerm_cognitive_account:          "Azure Cognitive Services",
  azurerm_kubernetes_cluster:         "Azure Kubernetes (AKS)",
  azurerm_container_app:              "Azure Container Apps",
  azurerm_function_app:               "Azure Functions",
  azurerm_servicebus_namespace:       "Azure Service Bus",
  azurerm_eventhub_namespace:         "Azure Event Hubs",
  azurerm_search_service:             "Azure AI Search",
  azurerm_key_vault:                  "Azure Key Vault",
  azurerm_virtual_machine:            "Azure VM",
  // GCP target
  google_storage_bucket:              "Cloud Storage",
  google_sql_database_instance:       "Cloud SQL",
  google_container_cluster:           "Google Kubernetes (GKE)",
  google_cloudfunctions_function:     "Cloud Functions",
  google_pubsub_topic:                "Cloud Pub/Sub",
};

const CLOUD_SHORT = { aws: "AWS", gcp: "GCP", azure: "Azure" };

const COMPLEXITY_STYLE = {
  HIGH:   { bg: "#fef2f2", color: "#ef4444" },
  MEDIUM: { bg: "#fffbeb", color: "#f59e0b" },
  LOW:    { bg: "#f0fdf4", color: "#22c55e" },
};

function getMigrationStatus(strategy) {
  const s = (strategy ?? "").toUpperCase();
  if (["REHOST", "RELOCATE"].includes(s))    return { label: "REPLACED", color: "#22c55e" };
  if (["REPLATFORM", "REFACTOR"].includes(s)) return { label: "MODIFIED", color: "#f59e0b" };
  if (s === "REPURCHASE")                      return { label: "NEW",      color: "#3b82f6" };
  if (s === "RETIRE")                          return { label: "RETIRED",  color: "#94a3b8" };
  if (s === "RETAIN")                          return { label: "RETAINED", color: "#38bdf8" };
  return { label: "NEW", color: "#3b82f6" };
}

function ServiceNode({ data, selected }) {
  const {
    label, type, cloud, score, complexity, color,
    cloudColor, role, retired, strategy,
    detectionSource, cloudConfirmed,
  } = data;

  const isSource = role === "source";
  const migStatus = !isSource ? getMigrationStatus(strategy) : null;
  const cxStyle = COMPLEXITY_STYLE[complexity] ?? {};

  // Lookup human-readable name; fall back to stripping prefix + replacing underscores
  const displayName = SERVICE_LABELS[label]
    || SERVICE_LABELS[label?.toLowerCase()]
    || label
      .replace(/^(aws_|azurerm_|google_|microsoft\.|aws\.|google\.)/i, "")
      .replace(/_/g, " ");
  const shortName = displayName.length > 24 ? displayName.slice(0, 22) + "…" : displayName;

  const borderColor = selected ? color : "var(--surface-4, #e4e8f0)";
  const boxShadow = selected
    ? `0 0 0 2px ${color}40, 0 4px 16px rgba(0,0,0,0.14)`
    : "0 2px 8px rgba(0,0,0,0.07)";

  return (
    <div
      style={{
        background: "var(--surface-0, #fff)",
        border: `1.5px solid ${borderColor}`,
        borderLeft: `4px solid ${retired ? "#94a3b8" : color}`,
        borderRadius: 12,
        padding: "9px 13px 10px",
        minWidth: 178,
        maxWidth: 206,
        boxShadow,
        cursor: "pointer",
        opacity: retired ? 0.45 : 1,
        transition: "box-shadow 150ms ease, border-color 150ms ease, opacity 150ms ease",
        fontFamily: "var(--font-sans, system-ui)",
        userSelect: "none",
        animation: "svc-appear 0.25s cubic-bezier(0.16,1,0.3,1) both",
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: color, width: 8, height: 8, border: "2px solid var(--surface-0,#fff)" }}
      />

      {/* Row 1: cloud badge + type icon + status/strategy */}
      <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 6 }}>
        <span style={{ fontSize: 13, lineHeight: 1, flexShrink: 0 }}>
          {TYPE_ICONS[type] ?? "☁"}
        </span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.3,
            padding: "1px 6px",
            borderRadius: 10,
            background: `${cloudColor}20`,
            color: cloudColor,
            flexShrink: 0,
          }}
        >
          {CLOUD_SHORT[cloud] ?? cloud?.toUpperCase() ?? "?"}
        </span>

        {/* Source: detection confidence chip */}
        {isSource && (() => {
          const isManual = detectionSource === "user_selection";
          const bg    = isManual ? "#3b82f618" : cloudConfirmed ? "#22c55e18" : "#f59e0b18";
          const color = isManual ? "#1d4ed8"   : cloudConfirmed ? "#15803d"   : "#b45309";
          const label = isManual ? "manuel"     : cloudConfirmed ? "✓ IaC"    : "inféré";
          const tip   = isManual
            ? "Service sélectionné manuellement par l'utilisateur"
            : detectionSource
              ? `Détecté depuis ${detectionSource}`
              : undefined;
          return (
            <span
              style={{
                fontSize: 9,
                fontWeight: 600,
                padding: "1px 5px",
                borderRadius: 8,
                background: bg,
                color,
                marginLeft: "auto",
                letterSpacing: 0.1,
                flexShrink: 0,
              }}
              title={tip}
            >
              {label}
            </span>
          );
        })()}

        {/* Target: migration status badge */}
        {!isSource && migStatus && (
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              padding: "1px 6px",
              borderRadius: 8,
              background: `${migStatus.color}18`,
              color: migStatus.color,
              marginLeft: "auto",
              letterSpacing: 0.2,
              flexShrink: 0,
            }}
          >
            {migStatus.label}
          </span>
        )}
      </div>

      {/* Service name */}
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: "var(--text-primary, #0a0a0b)",
          lineHeight: 1.35,
          wordBreak: "break-word",
          textTransform: "capitalize",
        }}
        title={label}
      >
        {shortName}
      </div>

      {/* Row 3: complexity + score */}
      {(complexity || score !== undefined) && (
        <div style={{ display: "flex", gap: 5, marginTop: 6, alignItems: "center" }}>
          {complexity && (
            <span
              style={{
                fontSize: 9,
                fontWeight: 700,
                padding: "2px 6px",
                borderRadius: 8,
                background: cxStyle.bg ?? "var(--surface-2, #f4f6fa)",
                color: cxStyle.color ?? "var(--text-tertiary, #71717a)",
              }}
            >
              {complexity}
            </span>
          )}
          {typeof score === "number" && (
            <span
              style={{
                fontSize: 10,
                color: "var(--text-tertiary, #71717a)",
                marginLeft: "auto",
              }}
            >
              {score % 1 === 0 ? score : score.toFixed(2)}
            </span>
          )}
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        style={{ background: color, width: 8, height: 8, border: "2px solid var(--surface-0,#fff)" }}
      />
    </div>
  );
}

export default memo(ServiceNode);
