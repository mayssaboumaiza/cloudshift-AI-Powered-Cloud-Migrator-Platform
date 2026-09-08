import { useState, useMemo, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import {
  Server, Database, HardDrive, MessageSquare, Shield,
  Eye, Cpu, Search, Bot, Network, X, CheckCircle2, AlertCircle, Zap,
} from "lucide-react";

// ─── i18n strings ─────────────────────────────────────────────────────────────
const I18N = {
  fr: {
    title:        "Sélection des services cloud",
    subtitle:     "Aucun fichier IaC détecté dans ce dépôt",
    warning:      (cloud) => `Indiquez les services ${cloud} utilisés par votre application. Le Migration Planner les analysera pour générer le plan de migration.`,
    autoDetected: "Services auto-détectés dans le code source",
    search:       "Rechercher un service…",
    none:         (q) => `Aucun service correspondant à « ${q} »`,
    selected:     (n) => `${n} service${n !== 1 ? "s" : ""} sélectionné${n !== 1 ? "s" : ""}`,
    none_sel:     "Aucun service sélectionné — cochez au moins un service pour continuer",
    confirm:      (n) => `Confirmer (${n} service${n !== 1 ? "s" : ""})`,
    loading:      "Analyse en cours…",
    selectAll:    "Tout sélectionner",
    deselectAll:  "Tout désélectionner",
    confirmClose: "Vous avez des services sélectionnés. Fermer quand même ?",
    extraServices:(n) => `+${n} autre${n > 1 ? "s" : ""}`,
  },
  en: {
    title:        "Cloud service selection",
    subtitle:     "No IaC file detected in this repository",
    warning:      (cloud) => `Select the ${cloud} services your application uses. The Migration Planner will analyse them to generate the migration plan.`,
    autoDetected: "Auto-detected services from source code",
    search:       "Search a service…",
    none:         (q) => `No service matching "${q}"`,
    selected:     (n) => `${n} service${n !== 1 ? "s" : ""} selected`,
    none_sel:     "No service selected — check at least one to continue",
    confirm:      (n) => `Confirm (${n} service${n !== 1 ? "s" : ""})`,
    loading:      "Analysis in progress…",
    selectAll:    "Select all",
    deselectAll:  "Deselect all",
    confirmClose: "You have selected services. Close anyway?",
    extraServices:(n) => `+${n} more`,
  },
};

// ─── Service catalog (AWS / GCP / Azure) ──────────────────────────────────────
const CATALOG = {
  aws: [
    { id: "lambda",         name: "Lambda",                category: "Compute",    desc: { fr: "Fonctions serverless",             en: "Serverless functions"          }, sdkKeys: ["lambda"] },
    { id: "ec2",            name: "EC2",                   category: "Compute",    desc: { fr: "Machines virtuelles",              en: "Virtual machines"              }, sdkKeys: ["ec2"] },
    { id: "ecs",            name: "ECS",                   category: "Compute",    desc: { fr: "Containers managés",               en: "Managed containers"            }, sdkKeys: ["ecs"] },
    { id: "eks",            name: "EKS",                   category: "Compute",    desc: { fr: "Kubernetes managé",                en: "Managed Kubernetes"            }, sdkKeys: ["eks"] },
    { id: "fargate",        name: "Fargate",               category: "Compute",    desc: { fr: "Containers sans serveur",          en: "Serverless containers"         }, sdkKeys: ["fargate"] },
    { id: "ecr",            name: "ECR",                   category: "Compute",    desc: { fr: "Registry de containers",           en: "Container registry"            }, sdkKeys: ["ecr"] },
    { id: "app-runner",     name: "App Runner",            category: "Compute",    desc: { fr: "Déploiement web simplifié",        en: "Simplified web deployment"     }, sdkKeys: ["apprunner"] },
    { id: "rds",            name: "RDS",                   category: "Database",   desc: { fr: "Base de données relationnelle",    en: "Relational database"           }, sdkKeys: ["rds", "rds-data"] },
    { id: "aurora",         name: "Aurora",                category: "Database",   desc: { fr: "BDD cloud-native haute perf",      en: "High-perf cloud-native DB"     }, sdkKeys: ["rds"] },
    { id: "dynamodb",       name: "DynamoDB",              category: "Database",   desc: { fr: "NoSQL serverless",                 en: "Serverless NoSQL"              }, sdkKeys: ["dynamodb", "dynamodbstreams"] },
    { id: "documentdb",     name: "DocumentDB",            category: "Database",   desc: { fr: "Compatible MongoDB",               en: "MongoDB-compatible"            }, sdkKeys: ["docdb"] },
    { id: "elasticache",    name: "ElastiCache",           category: "Database",   desc: { fr: "Cache Redis/Memcached",            en: "Redis/Memcached cache"         }, sdkKeys: ["elasticache"] },
    { id: "redshift",       name: "Redshift",              category: "Database",   desc: { fr: "Data warehouse",                   en: "Data warehouse"                }, sdkKeys: ["redshift", "redshift-data"] },
    { id: "s3",             name: "S3",                    category: "Storage",    desc: { fr: "Stockage d'objets",                en: "Object storage"                }, sdkKeys: ["s3", "s3control"] },
    { id: "efs",            name: "EFS",                   category: "Storage",    desc: { fr: "Système de fichiers partagé",      en: "Shared file system"            }, sdkKeys: ["efs"] },
    { id: "glacier",        name: "S3 Glacier",            category: "Storage",    desc: { fr: "Archivage longue durée",           en: "Long-term archival"            }, sdkKeys: ["glacier"] },
    { id: "sqs",            name: "SQS",                   category: "Messaging",  desc: { fr: "File de messages",                 en: "Message queue"                 }, sdkKeys: ["sqs"] },
    { id: "sns",            name: "SNS",                   category: "Messaging",  desc: { fr: "Notifications pub/sub",            en: "Pub/sub notifications"         }, sdkKeys: ["sns"] },
    { id: "msk",            name: "MSK (Kafka)",           category: "Messaging",  desc: { fr: "Kafka managé",                     en: "Managed Kafka"                 }, sdkKeys: ["kafka"] },
    { id: "kinesis",        name: "Kinesis",               category: "Messaging",  desc: { fr: "Streaming de données",             en: "Data streaming"                }, sdkKeys: ["kinesis", "firehose"] },
    { id: "ses",            name: "SES",                   category: "Messaging",  desc: { fr: "Envoi d'emails",                   en: "Email sending"                 }, sdkKeys: ["ses", "sesv2"] },
    { id: "eventbridge",    name: "EventBridge",           category: "Messaging",  desc: { fr: "Bus d'événements",                 en: "Event bus"                     }, sdkKeys: ["events"] },
    { id: "vpc",            name: "VPC",                   category: "Network",    desc: { fr: "Réseau privé virtuel",             en: "Virtual private network"       }, sdkKeys: ["ec2"] },
    { id: "cloudfront",     name: "CloudFront",            category: "Network",    desc: { fr: "CDN global",                       en: "Global CDN"                    }, sdkKeys: ["cloudfront"] },
    { id: "api-gateway",    name: "API Gateway",           category: "Network",    desc: { fr: "Gestion d'APIs REST/WebSocket",    en: "REST/WebSocket API management" }, sdkKeys: ["apigateway", "apigatewayv2"] },
    { id: "route53",        name: "Route 53",              category: "Network",    desc: { fr: "DNS managé",                       en: "Managed DNS"                   }, sdkKeys: ["route53"] },
    { id: "elb",            name: "Load Balancer",         category: "Network",    desc: { fr: "Répartiteur de charge",            en: "Load balancer"                 }, sdkKeys: ["elbv2", "elb"] },
    { id: "iam",            name: "IAM",                   category: "Security",   desc: { fr: "Identités et permissions",         en: "Identities and permissions"    }, sdkKeys: ["iam"] },
    { id: "secretsmanager", name: "Secrets Manager",       category: "Security",   desc: { fr: "Gestion des secrets",              en: "Secrets management"            }, sdkKeys: ["secretsmanager"] },
    { id: "cognito",        name: "Cognito",               category: "Security",   desc: { fr: "Authentification utilisateurs",    en: "User authentication"           }, sdkKeys: ["cognito-idp", "cognito-identity"] },
    { id: "waf",            name: "WAF",                   category: "Security",   desc: { fr: "Pare-feu applicatif",              en: "Web application firewall"      }, sdkKeys: ["wafv2"] },
    { id: "cloudwatch",     name: "CloudWatch",            category: "Monitoring", desc: { fr: "Logs et métriques",                en: "Logs and metrics"              }, sdkKeys: ["cloudwatch", "logs"] },
    { id: "x-ray",          name: "X-Ray",                 category: "Monitoring", desc: { fr: "Tracing distribué",                en: "Distributed tracing"           }, sdkKeys: ["xray"] },
    { id: "opensearch",     name: "OpenSearch",            category: "Search",     desc: { fr: "Moteur de recherche",              en: "Search engine"                 }, sdkKeys: ["opensearch", "es"] },
    { id: "bedrock",        name: "Bedrock",               category: "AI/ML",      desc: { fr: "LLMs managés (Claude, Titan…)",    en: "Managed LLMs (Claude, Titan…)" }, sdkKeys: ["bedrock", "bedrock-runtime", "bedrock-agent", "bedrock-agent-runtime"] },
    { id: "sagemaker",      name: "SageMaker",             category: "AI/ML",      desc: { fr: "Entraînement et inférence ML",     en: "ML training and inference"     }, sdkKeys: ["sagemaker", "sagemaker-runtime"] },
  ],
  gcp: [
    { id: "gcs",               name: "Cloud Storage",      category: "Storage",    desc: { fr: "Stockage d'objets",              en: "Object storage"              }, sdkKeys: [] },
    { id: "cloud-run",         name: "Cloud Run",          category: "Compute",    desc: { fr: "Containers serverless",          en: "Serverless containers"       }, sdkKeys: [] },
    { id: "cloud-functions",   name: "Cloud Functions",    category: "Compute",    desc: { fr: "Fonctions serverless",           en: "Serverless functions"        }, sdkKeys: [] },
    { id: "gke",               name: "GKE",                category: "Compute",    desc: { fr: "Kubernetes managé",              en: "Managed Kubernetes"          }, sdkKeys: [] },
    { id: "compute-engine",    name: "Compute Engine",     category: "Compute",    desc: { fr: "Machines virtuelles",            en: "Virtual machines"            }, sdkKeys: [] },
    { id: "artifact-registry", name: "Artifact Registry",  category: "Compute",    desc: { fr: "Registry d'artefacts",           en: "Artifact registry"           }, sdkKeys: [] },
    { id: "bigquery",          name: "BigQuery",           category: "Database",   desc: { fr: "Data warehouse serverless",      en: "Serverless data warehouse"   }, sdkKeys: [] },
    { id: "firestore",         name: "Firestore",          category: "Database",   desc: { fr: "NoSQL documentaire",             en: "Document NoSQL"              }, sdkKeys: [] },
    { id: "cloud-sql",         name: "Cloud SQL",          category: "Database",   desc: { fr: "BDD relationnelle managée",      en: "Managed relational DB"       }, sdkKeys: [] },
    { id: "spanner",           name: "Cloud Spanner",      category: "Database",   desc: { fr: "BDD distribuée globalement",     en: "Globally distributed DB"     }, sdkKeys: [] },
    { id: "bigtable",          name: "Bigtable",           category: "Database",   desc: { fr: "NoSQL haute performance",        en: "High-performance NoSQL"      }, sdkKeys: [] },
    { id: "memorystore",       name: "Memorystore",        category: "Database",   desc: { fr: "Cache Redis managé",             en: "Managed Redis cache"         }, sdkKeys: [] },
    { id: "pubsub",            name: "Pub/Sub",            category: "Messaging",  desc: { fr: "Messagerie asynchrone",          en: "Async messaging"             }, sdkKeys: [] },
    { id: "dataflow",          name: "Dataflow",           category: "Messaging",  desc: { fr: "Traitement de flux",             en: "Stream processing"           }, sdkKeys: [] },
    { id: "cloud-dns",         name: "Cloud DNS",          category: "Network",    desc: { fr: "DNS managé",                     en: "Managed DNS"                 }, sdkKeys: [] },
    { id: "cloud-cdn",         name: "Cloud CDN",          category: "Network",    desc: { fr: "Réseau de diffusion",            en: "Content delivery network"    }, sdkKeys: [] },
    { id: "cloud-load-balancing", name: "Load Balancing",  category: "Network",    desc: { fr: "Répartiteur de charge",          en: "Load balancer"               }, sdkKeys: [] },
    { id: "iam",               name: "IAM",                category: "Security",   desc: { fr: "Identités et permissions",       en: "Identities and permissions"  }, sdkKeys: [] },
    { id: "cloud-armor",       name: "Cloud Armor",        category: "Security",   desc: { fr: "Protection DDoS/WAF",            en: "DDoS/WAF protection"         }, sdkKeys: [] },
    { id: "secret-manager",    name: "Secret Manager",     category: "Security",   desc: { fr: "Gestion des secrets",            en: "Secrets management"          }, sdkKeys: [] },
    { id: "stackdriver",       name: "Cloud Monitoring",   category: "Monitoring", desc: { fr: "Métriques et alertes",           en: "Metrics and alerts"          }, sdkKeys: [] },
    { id: "cloud-logging",     name: "Cloud Logging",      category: "Monitoring", desc: { fr: "Centralisation des logs",        en: "Log aggregation"             }, sdkKeys: [] },
    { id: "aiplatform",        name: "Vertex AI",          category: "AI/ML",      desc: { fr: "Plateforme ML unifiée",          en: "Unified ML platform"         }, sdkKeys: [] },
    { id: "cloud-vision",      name: "Cloud Vision AI",    category: "AI/ML",      desc: { fr: "Analyse d'images",               en: "Image analysis"              }, sdkKeys: [] },
  ],
  azure: [
    { id: "blob-storage",         name: "Blob Storage",        category: "Storage",    desc: { fr: "Stockage d'objets",             en: "Object storage"            }, sdkKeys: [] },
    { id: "azure-files",          name: "Azure Files",         category: "Storage",    desc: { fr: "Partage de fichiers",           en: "File sharing"              }, sdkKeys: [] },
    { id: "azure-functions",      name: "Azure Functions",     category: "Compute",    desc: { fr: "Fonctions serverless",          en: "Serverless functions"      }, sdkKeys: [] },
    { id: "app-service",          name: "App Service",         category: "Compute",    desc: { fr: "Hébergement web managé",        en: "Managed web hosting"       }, sdkKeys: [] },
    { id: "aks",                  name: "AKS",                 category: "Compute",    desc: { fr: "Kubernetes managé",             en: "Managed Kubernetes"        }, sdkKeys: [] },
    { id: "container-apps",       name: "Container Apps",      category: "Compute",    desc: { fr: "Containers managés",            en: "Managed containers"        }, sdkKeys: [] },
    { id: "virtual-machine",      name: "Virtual Machine",     category: "Compute",    desc: { fr: "Machines virtuelles",           en: "Virtual machines"          }, sdkKeys: [] },
    { id: "container-registry",   name: "Container Registry",  category: "Compute",    desc: { fr: "Registry de containers",        en: "Container registry"        }, sdkKeys: [] },
    { id: "cosmosdb",             name: "Cosmos DB",           category: "Database",   desc: { fr: "NoSQL multi-modèle",            en: "Multi-model NoSQL"         }, sdkKeys: [] },
    { id: "postgresql",           name: "PostgreSQL Flexible", category: "Database",   desc: { fr: "BDD PostgreSQL managée",        en: "Managed PostgreSQL"        }, sdkKeys: [] },
    { id: "sql-database",         name: "SQL Database",        category: "Database",   desc: { fr: "BDD SQL Server managée",        en: "Managed SQL Server"        }, sdkKeys: [] },
    { id: "cache-for-redis",      name: "Cache for Redis",     category: "Database",   desc: { fr: "Cache Redis managé",            en: "Managed Redis cache"       }, sdkKeys: [] },
    { id: "service-bus",          name: "Service Bus",         category: "Messaging",  desc: { fr: "Messagerie enterprise",         en: "Enterprise messaging"      }, sdkKeys: [] },
    { id: "event-hubs",           name: "Event Hubs",          category: "Messaging",  desc: { fr: "Ingestion de données",          en: "Data ingestion"            }, sdkKeys: [] },
    { id: "storage-queue",        name: "Storage Queue",       category: "Messaging",  desc: { fr: "File de messages simple",       en: "Simple message queue"      }, sdkKeys: [] },
    { id: "event-grid",           name: "Event Grid",          category: "Messaging",  desc: { fr: "Événements réactifs",           en: "Reactive events"           }, sdkKeys: [] },
    { id: "front-door",           name: "Front Door / CDN",    category: "Network",    desc: { fr: "CDN et load balancer global",   en: "Global CDN and LB"         }, sdkKeys: [] },
    { id: "api-management",       name: "API Management",      category: "Network",    desc: { fr: "Passerelle d'APIs",             en: "API gateway"               }, sdkKeys: [] },
    { id: "application-gateway",  name: "App Gateway",         category: "Network",    desc: { fr: "Répartiteur de charge L7",      en: "L7 load balancer"          }, sdkKeys: [] },
    { id: "virtual-network",      name: "Virtual Network",     category: "Network",    desc: { fr: "Réseau privé virtuel",          en: "Virtual private network"   }, sdkKeys: [] },
    { id: "keyvault",             name: "Key Vault",           category: "Security",   desc: { fr: "Secrets et certificats",        en: "Secrets and certificates"  }, sdkKeys: [] },
    { id: "active-directory",     name: "Entra ID / AAD",      category: "Security",   desc: { fr: "Identité et SSO",               en: "Identity and SSO"          }, sdkKeys: [] },
    { id: "managed-identity",     name: "Managed Identity",    category: "Security",   desc: { fr: "Identités sans credentials",    en: "Credential-free identities"}, sdkKeys: [] },
    { id: "monitor",              name: "Azure Monitor",       category: "Monitoring", desc: { fr: "Métriques et logs",             en: "Metrics and logs"          }, sdkKeys: [] },
    { id: "application-insights", name: "App Insights",        category: "Monitoring", desc: { fr: "APM et tracing",                en: "APM and tracing"           }, sdkKeys: [] },
    { id: "log-analytics-workspace", name: "Log Analytics",    category: "Monitoring", desc: { fr: "Analyse centralisée",           en: "Centralised log analysis"  }, sdkKeys: [] },
    { id: "cognitive-search",     name: "Cognitive Search",    category: "Search",     desc: { fr: "Recherche intelligente",        en: "Intelligent search"        }, sdkKeys: [] },
    { id: "azure-openai",         name: "Azure OpenAI",        category: "AI/ML",      desc: { fr: "GPT-4, DALL·E, Embeddings",     en: "GPT-4, DALL·E, Embeddings" }, sdkKeys: [] },
    { id: "ml-workspace",         name: "Azure ML",            category: "AI/ML",      desc: { fr: "Entraînement et déploiement ML",en: "ML training and deployment"}, sdkKeys: [] },
    { id: "cognitive-services",   name: "Cognitive Services",  category: "AI/ML",      desc: { fr: "Vision, Speech, Language",      en: "Vision, Speech, Language"  }, sdkKeys: [] },
  ],
};

// Maps boto3 client names → service IDs (for auto-detection)
const BOTO3_KEY_TO_SERVICE_ID = {};
(CATALOG.aws || []).forEach((svc) => {
  (svc.sdkKeys || []).forEach((k) => {
    if (!BOTO3_KEY_TO_SERVICE_ID[k]) BOTO3_KEY_TO_SERVICE_ID[k] = svc.id;
  });
});

const CATEGORY_META = {
  Compute:    { icon: Cpu,           color: "#6366f1", bg: "#eef2ff" },
  Database:   { icon: Database,      color: "#0891b2", bg: "#ecfeff" },
  Storage:    { icon: HardDrive,     color: "#d97706", bg: "#fffbeb" },
  Messaging:  { icon: MessageSquare, color: "#7c3aed", bg: "#f5f3ff" },
  Network:    { icon: Network,       color: "#059669", bg: "#ecfdf5" },
  Security:   { icon: Shield,        color: "#dc2626", bg: "#fef2f2" },
  Monitoring: { icon: Eye,           color: "#0284c7", bg: "#f0f9ff" },
  Search:     { icon: Search,        color: "#b45309", bg: "#fefce8" },
  "AI/ML":    { icon: Bot,           color: "#7c3aed", bg: "#faf5ff" },
};

const CATEGORY_ORDER = [
  "Compute", "Database", "Storage", "Messaging",
  "Network", "Security", "Monitoring", "Search", "AI/ML",
];

const CLOUD_LABELS = {
  aws:   "Amazon Web Services",
  gcp:   "Google Cloud Platform",
  azure: "Microsoft Azure",
};

const CLOUD_COLORS = {
  aws:   { primary: "#FF9900", light: "#fff8ee", border: "#ffd580" },
  gcp:   { primary: "#4285F4", light: "#eff6ff", border: "#bfdbfe" },
  azure: { primary: "#0078D4", light: "#eff6ff", border: "#bfdbfe" },
};

// ─────────────────────────────────────────────────────────────────────────────

export default function ServiceSelectionModal({
  sourceCloud,
  onSubmit,
  loading,
  submitError,
  detectedServices,   // from dependency_graph.python_ai_stack or sdk_clients
  lang = "fr",        // "fr" | "en" — passed from MigrationDetail via useI18n
}) {
  const cloud    = (sourceCloud || "aws").toLowerCase();
  const services = CATALOG[cloud] || CATALOG.aws;
  const colors   = CLOUD_COLORS[cloud] || CLOUD_COLORS.aws;
  const T        = I18N[lang] || I18N.fr;

  // ── Build initial checked state from auto-detected services ─────────────────
  const autoDetectedIds = useMemo(() => {
    if (!detectedServices || cloud !== "aws") return new Set();
    const ids = new Set();
    // detectedServices is an array of { service, cloud, source, confidence }
    // or raw boto3 client names from python_ai_stack.sdk_clients
    (Array.isArray(detectedServices) ? detectedServices : []).forEach((item) => {
      const svcName = (item.service || item.id || "").toLowerCase().replace(/-/g, "");
      // Direct id match
      const direct = services.find((s) => s.id === svcName || s.id === item.service);
      if (direct) { ids.add(direct.id); return; }
      // Boto3 key match
      const byKey = BOTO3_KEY_TO_SERVICE_ID[svcName] || BOTO3_KEY_TO_SERVICE_ID[item.service];
      if (byKey) ids.add(byKey);
    });
    return ids;
  }, [detectedServices, cloud, services]);

  const [checked, setChecked]             = useState(() => {
    const init = {};
    autoDetectedIds.forEach((id) => { init[id] = true; });
    return init;
  });
  const [search,  setSearch]              = useState("");
  const [activeCategory, setActiveCategory] = useState(null);

  // ── Prevent body scroll + scroll to top so fixed modal is fully visible ──────
  useEffect(() => {
    const prev = document.documentElement.scrollTop || document.body.scrollTop;
    window.scrollTo({ top: 0, behavior: "instant" });
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
      window.scrollTo({ top: prev, behavior: "instant" });
    };
  }, []);

  // ── Escape key → close with confirmation if services checked ────────────────
  const selectedList  = services.filter((s) => checked[s.id]);
  const selectedCount = selectedList.length;

  const handleClose = useCallback(() => {
    if (selectedCount > 0) {
      if (!window.confirm(T.confirmClose)) return;
    }
    // Reset checked (parent will hide modal on next render cycle via needs_service_selection)
    setChecked({});
  }, [selectedCount, T.confirmClose]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") handleClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleClose]);

  // ── Filtering + grouping ────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return services.filter(
      (s) => !q
        || s.name.toLowerCase().includes(q)
        || s.category.toLowerCase().includes(q)
        || (s.desc?.[lang] || "").toLowerCase().includes(q),
    );
  }, [services, search, lang]);

  const byCategory = useMemo(() => {
    const map = {};
    for (const svc of filtered) {
      if (!map[svc.category]) map[svc.category] = [];
      map[svc.category].push(svc);
    }
    return map;
  }, [filtered]);

  const visibleCategories = CATEGORY_ORDER.filter((c) => byCategory[c]?.length > 0);

  const toggle = (id) => setChecked((prev) => ({ ...prev, [id]: !prev[id] }));

  const toggleCategory = (cat) => {
    const catServices = byCategory[cat] || [];
    const allChecked  = catServices.every((s) => checked[s.id]);
    const next = { ...checked };
    catServices.forEach((s) => { next[s.id] = !allChecked; });
    setChecked(next);
  };

  const handleSubmit = () => {
    if (selectedCount === 0 || loading) return;
    onSubmit(selectedList.map((s) => ({ service: s.id, cloud, type: null })));
  };

  const scrollToCategory = (cat) => {
    setActiveCategory(cat);
    const el = document.getElementById(`svc-cat-${cat}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return createPortal(
    <>
      {/* ── Overlay — click to close ── */}
      <div
        onClick={handleClose}
        style={{
          position: "fixed",
          top: 0, left: 0, right: 0, bottom: 0,
          width: "100vw", height: "100vh",
          zIndex: 99999,
          background: "rgba(15, 23, 42, 0.65)",
          backdropFilter: "blur(4px)",
          display: "flex", alignItems: "center", justifyContent: "center",
          padding: "24px",
          boxSizing: "border-box",
          overflowY: "auto",
        }}
      >
        {/* ── Modal — stop propagation so inner clicks don't close ── */}
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            width: "100%", maxWidth: 880,
            maxHeight: "calc(100vh - 48px)",
            minHeight: 0,
            background: "#fff",
            borderRadius: 20,
            boxShadow: "0 25px 60px rgba(0,0,0,0.28)",
            display: "flex", flexDirection: "column",
            overflow: "hidden",
            border: `1px solid ${colors.border}`,
            flexShrink: 1,
          }}
        >

          {/* ── Header ── */}
          <div style={{
            background: `linear-gradient(135deg, ${colors.primary}15 0%, ${colors.light} 100%)`,
            borderBottom: `1px solid ${colors.border}`,
            padding: "18px 24px 14px",
            flexShrink: 0,
          }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                  <div style={{
                    width: 40, height: 40, borderRadius: 10,
                    background: colors.primary, display: "flex",
                    alignItems: "center", justifyContent: "center", flexShrink: 0,
                  }}>
                    <Server size={20} color="#fff" />
                  </div>
                  <div>
                    <h2 style={{ margin: 0, fontSize: 19, fontWeight: 700, color: "#0f172a", lineHeight: 1.2 }}>
                      {T.title}
                    </h2>
                    <p style={{ margin: 0, fontSize: 12, color: "#64748b", marginTop: 2 }}>
                      {T.subtitle}
                    </p>
                  </div>
                </div>

                <div style={{
                  display: "flex", alignItems: "flex-start", gap: 8,
                  padding: "9px 14px", borderRadius: 8,
                  background: "#fffbeb", border: "1px solid #fde68a",
                }}>
                  <AlertCircle size={14} color="#d97706" style={{ flexShrink: 0, marginTop: 1 }} />
                  <span style={{ fontSize: 13, color: "#92400e", lineHeight: 1.45 }}>
                    {T.warning(CLOUD_LABELS[cloud] || cloud)}
                  </span>
                </div>

                {/* Auto-detected badge */}
                {autoDetectedIds.size > 0 && (
                  <div style={{
                    display: "flex", alignItems: "center", gap: 7,
                    padding: "7px 12px", borderRadius: 8,
                    background: "#f0fdf4", border: "1px solid #86efac",
                    marginTop: 10,
                  }}>
                    <Zap size={13} color="#16a34a" />
                    <span style={{ fontSize: 12, color: "#15803d", fontWeight: 600 }}>
                      {T.autoDetected} — {autoDetectedIds.size} service{autoDetectedIds.size > 1 ? "s" : ""} pré-sélectionné{autoDetectedIds.size > 1 ? "s" : ""}
                    </span>
                  </div>
                )}
              </div>

              {/* Count badge + close button */}
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, flexShrink: 0 }}>
                <div style={{
                  width: 54, height: 54, borderRadius: "50%",
                  background: selectedCount > 0 ? colors.primary : "#e2e8f0",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 22, fontWeight: 800,
                  color: selectedCount > 0 ? "#fff" : "#94a3b8",
                  transition: "all 0.2s",
                }}>
                  {selectedCount}
                </div>
                <div style={{ fontSize: 11, color: "#64748b", fontWeight: 500 }}>
                  {lang === "fr"
                    ? `sélectionné${selectedCount !== 1 ? "s" : ""}`
                    : `selected`}
                </div>
                <button
                  onClick={handleClose}
                  title="Fermer (Esc)"
                  style={{
                    width: 28, height: 28, borderRadius: "50%",
                    border: "1px solid #e2e8f0", background: "#f8fafc",
                    cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
                    color: "#94a3b8", transition: "all 0.15s",
                  }}
                  onMouseOver={(e) => { e.currentTarget.style.background = "#fee2e2"; e.currentTarget.style.color = "#dc2626"; }}
                  onMouseOut={(e)  => { e.currentTarget.style.background = "#f8fafc"; e.currentTarget.style.color = "#94a3b8"; }}
                >
                  <X size={14} />
                </button>
              </div>
            </div>

            {/* Search */}
            <div style={{ marginTop: 14, position: "relative" }}>
              <Search size={15} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "#94a3b8" }} />
              <input
                type="text"
                placeholder={T.search}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  width: "100%", boxSizing: "border-box",
                  padding: "9px 36px",
                  border: "1px solid #e2e8f0", borderRadius: 10, fontSize: 13,
                  background: "#fff", outline: "none", transition: "border-color 0.15s",
                }}
                onFocus={(e) => { e.target.style.borderColor = colors.primary; }}
                onBlur={(e)  => { e.target.style.borderColor = "#e2e8f0"; }}
              />
              {search && (
                <button onClick={() => setSearch("")} style={{
                  position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
                  background: "none", border: "none", cursor: "pointer", color: "#94a3b8", padding: 2,
                }}>
                  <X size={14} />
                </button>
              )}
            </div>

            {/* Category nav pills */}
            {!search && (
              <div style={{ display: "flex", gap: 6, marginTop: 12, flexWrap: "wrap" }}>
                {CATEGORY_ORDER.filter((c) => byCategory[c]?.length > 0).map((cat) => {
                  const meta = CATEGORY_META[cat] || {};
                  const Icon = meta.icon || Server;
                  const catCheckedCount = (byCategory[cat] || []).filter((s) => checked[s.id]).length;
                  const isActive = activeCategory === cat;
                  return (
                    <button
                      key={cat}
                      onClick={() => scrollToCategory(cat)}
                      style={{
                        display: "flex", alignItems: "center", gap: 5,
                        padding: "5px 11px", borderRadius: 20,
                        border: `1px solid ${isActive ? meta.color : "#e2e8f0"}`,
                        background: isActive ? meta.bg : "#f8fafc",
                        color: isActive ? meta.color : "#64748b",
                        fontSize: 12, fontWeight: 500, cursor: "pointer", transition: "all 0.15s",
                      }}
                    >
                      <Icon size={11} />
                      {cat}
                      {catCheckedCount > 0 && (
                        <span style={{
                          background: meta.color, color: "#fff",
                          borderRadius: 10, padding: "1px 6px", fontSize: 10, fontWeight: 700,
                        }}>
                          {catCheckedCount}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* ── Body ── */}
          <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px" }}>
            {visibleCategories.length === 0 && (
              <div style={{ textAlign: "center", padding: "40px 0", color: "#94a3b8" }}>
                <Search size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
                <p style={{ margin: 0, fontSize: 14 }}>{T.none(search)}</p>
              </div>
            )}

            {visibleCategories.map((cat) => {
              const meta        = CATEGORY_META[cat] || {};
              const Icon        = meta.icon || Server;
              const catSvcs     = byCategory[cat] || [];
              const allChecked  = catSvcs.every((s) => checked[s.id]);
              const someChecked = catSvcs.some((s) => checked[s.id]);
              const catCount    = catSvcs.filter((s) => checked[s.id]).length;

              return (
                <div key={cat} id={`svc-cat-${cat}`} style={{ marginBottom: 28 }}>
                  {/* Category header */}
                  <div style={{
                    display: "flex", alignItems: "center", gap: 10,
                    marginBottom: 12, paddingBottom: 10,
                    borderBottom: `2px solid ${meta.bg || "#f1f5f9"}`,
                  }}>
                    <div style={{
                      width: 30, height: 30, borderRadius: 8,
                      background: meta.bg, display: "flex", alignItems: "center", justifyContent: "center",
                    }}>
                      <Icon size={15} color={meta.color} />
                    </div>
                    <span style={{ fontWeight: 700, fontSize: 14, color: "#1e293b" }}>{cat}</span>
                    <span style={{ fontSize: 12, color: "#94a3b8" }}>
                      {catSvcs.length} service{catSvcs.length > 1 ? "s" : ""}
                    </span>
                    {catCount > 0 && (
                      <span style={{
                        background: meta.color, color: "#fff", borderRadius: 10,
                        padding: "2px 8px", fontSize: 11, fontWeight: 700,
                      }}>
                        {catCount} {lang === "fr" ? `sélectionné${catCount > 1 ? "s" : ""}` : "selected"}
                      </span>
                    )}
                    <button
                      onClick={() => toggleCategory(cat)}
                      style={{
                        marginLeft: "auto", padding: "4px 12px", borderRadius: 6,
                        border: `1px solid ${meta.color}30`,
                        background: allChecked ? meta.color : "transparent",
                        color: allChecked ? "#fff" : meta.color,
                        fontSize: 11, fontWeight: 600, cursor: "pointer", transition: "all 0.15s",
                      }}
                    >
                      {allChecked ? T.deselectAll : T.selectAll}
                    </button>
                  </div>

                  {/* Service cards grid */}
                  <div style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(195px, 1fr))",
                    gap: 10,
                  }}>
                    {catSvcs.map((svc) => {
                      const isSelected   = !!checked[svc.id];
                      const isAutoDetect = autoDetectedIds.has(svc.id);
                      return (
                        <button
                          key={svc.id}
                          onClick={() => toggle(svc.id)}
                          style={{
                            display: "flex", flexDirection: "column", alignItems: "flex-start",
                            padding: "11px 13px", borderRadius: 12, textAlign: "left",
                            border: isSelected ? `2px solid ${meta.color}` : "2px solid #e2e8f0",
                            background: isSelected ? meta.bg : "#fafafa",
                            cursor: "pointer", transition: "all 0.15s",
                            position: "relative",
                          }}
                        >
                          {isSelected && (
                            <div style={{
                              position: "absolute", top: 8, right: 8,
                              width: 18, height: 18, borderRadius: "50%",
                              background: meta.color, display: "flex", alignItems: "center", justifyContent: "center",
                            }}>
                              <CheckCircle2 size={12} color="#fff" />
                            </div>
                          )}
                          {isAutoDetect && (
                            <div style={{
                              position: "absolute", top: isSelected ? 30 : 8, right: 8,
                              display: "flex", alignItems: "center", gap: 3,
                            }}>
                              <Zap size={11} color="#16a34a" />
                            </div>
                          )}
                          <span style={{
                            fontSize: 13, fontWeight: 700, paddingRight: 20,
                            color: isSelected ? meta.color : "#1e293b", marginBottom: 3,
                          }}>
                            {svc.name}
                          </span>
                          {svc.desc && (
                            <span style={{ fontSize: 11, color: "#64748b", lineHeight: 1.3 }}>
                              {svc.desc[lang] || svc.desc.fr || ""}
                            </span>
                          )}
                          {isAutoDetect && !isSelected && (
                            <span style={{
                              fontSize: 10, color: "#16a34a", fontWeight: 600, marginTop: 4,
                              background: "#f0fdf4", borderRadius: 4, padding: "1px 5px",
                            }}>
                              {lang === "fr" ? "Détecté" : "Detected"}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          {/* ── Footer ── */}
          <div style={{
            borderTop: "1px solid #e2e8f0", padding: "14px 28px",
            background: "#f8fafc",
            display: "flex", flexDirection: "column", gap: 10,
            flexShrink: 0,
          }}>
            {/* Error banner */}
            {submitError && (
              <div style={{
                display: "flex", alignItems: "flex-start", gap: 8,
                padding: "9px 14px", borderRadius: 8,
                background: "#fef2f2", border: "1px solid #fecaca",
              }}>
                <AlertCircle size={14} color="#dc2626" style={{ flexShrink: 0, marginTop: 1 }} />
                <span style={{ fontSize: 12, color: "#991b1b", lineHeight: 1.45 }}>
                  {submitError}
                </span>
              </div>
            )}
            {/* Selected chips + confirm button */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
            {/* Selected chips preview */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, flex: 1, minWidth: 0 }}>
              {selectedCount === 0 ? (
                <span style={{ fontSize: 12, color: "#94a3b8", fontStyle: "italic" }}>
                  {T.none_sel}
                </span>
              ) : (
                <>
                  {selectedList.slice(0, 6).map((s) => {
                    const meta = CATEGORY_META[s.category] || {};
                    return (
                      <span key={s.id} style={{
                        padding: "3px 10px", borderRadius: 20,
                        fontSize: 11, fontWeight: 600,
                        background: meta.bg || "#f1f5f9",
                        color: meta.color || "#475569",
                        border: `1px solid ${meta.color || "#cbd5e1"}30`,
                      }}>
                        {s.name}
                      </span>
                    );
                  })}
                  {selectedCount > 6 && (
                    <span style={{ fontSize: 11, color: "#64748b", padding: "3px 6px" }}>
                      {T.extraServices(selectedCount - 6)}
                    </span>
                  )}
                </>
              )}
            </div>

            <button
              onClick={handleSubmit}
              disabled={selectedCount === 0 || loading}
              style={{
                flexShrink: 0, padding: "11px 24px", borderRadius: 10, border: "none",
                background: selectedCount === 0 || loading
                  ? "#e2e8f0"
                  : `linear-gradient(135deg, ${colors.primary}, ${colors.primary}cc)`,
                color: selectedCount === 0 || loading ? "#94a3b8" : "#fff",
                fontSize: 14, fontWeight: 700,
                cursor: selectedCount === 0 || loading ? "not-allowed" : "pointer",
                boxShadow: selectedCount > 0 && !loading ? "0 4px 14px rgba(0,0,0,0.15)" : "none",
                transition: "all 0.2s",
                display: "flex", alignItems: "center", gap: 8,
              }}
            >
              {loading ? (
                <>
                  <div style={{
                    width: 14, height: 14, border: "2px solid #fff",
                    borderTopColor: "transparent", borderRadius: "50%",
                    animation: "svc-spin 0.8s linear infinite",
                  }} />
                  {T.loading}
                </>
              ) : (
                <>
                  <CheckCircle2 size={16} />
                  {T.confirm(selectedCount)}
                </>
              )}
            </button>
            </div>  {/* end chips+button row */}
          </div>
        </div>
      </div>

      <style>{`@keyframes svc-spin { to { transform: rotate(360deg); } }`}</style>
    </>,
    document.getElementById("modal-root") || document.body
  );
}
