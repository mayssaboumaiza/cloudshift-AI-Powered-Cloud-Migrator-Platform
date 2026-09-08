import { useState, useCallback } from "react";
import { Zap, Database, BookOpen, Package, ArrowRight, Edit2, Check } from "lucide-react";

const CATEGORY_COLORS = {
  llm: { bg: "#ffffff", border: "#f59e0b", text: "#d97706", icon: Zap },
  embeddings: { bg: "#ffffff", border: "#8b5cf6", text: "#6d28d9", icon: Package },
  vector_db: { bg: "#ffffff", border: "#3b82f6", text: "#1d4ed8", icon: Database },
  framework: { bg: "#ffffff", border: "#10b981", text: "#059669", icon: BookOpen },
  managed_ml: { bg: "#ffffff", border: "#ec4899", text: "#be185d", icon: Zap },
  managed_llm: { bg: "#ffffff", border: "#f97316", text: "#c2410c", icon: Zap },
};

// IaC-declared managed ML platform mappings
const IAC_ML_CROSS_CLOUD_MAPPING = {
  "sagemaker":       { azure: "Azure Machine Learning",    gcp: "Vertex AI",            aws: "sagemaker" },
  "bedrock":         { azure: "Azure OpenAI",              gcp: "Vertex AI (Gemini)",   aws: "bedrock" },
  "vertex-ai":       { aws: "SageMaker",                   azure: "Azure Machine Learning", gcp: "vertex-ai" },
  "azure-ml":        { aws: "SageMaker",                   gcp: "Vertex AI",            azure: "azure-ml" },
  "azure-cognitive": { aws: "bedrock",                     gcp: "Vertex AI",            azure: "azure-cognitive" },
  "azure-bot":       { aws: "Amazon Lex",                  gcp: "Dialogflow CX",        azure: "azure-bot" },
};

// Service-level mapping (provider names)
const LLM_CROSS_CLOUD_MAPPING = {
  bedrock:              { gcp: "vertex-ai",   azure: "Azure OpenAI" },
  aws_bedrock:          { gcp: "vertex-ai",   azure: "Azure OpenAI" },
  aws_bedrock_claude:   { gcp: "vertex-ai",   azure: "Azure OpenAI" },
  aws_bedrock_agent:    { gcp: "vertex-ai",   azure: "Azure OpenAI" },
  aws_sagemaker:        { gcp: "vertex-ai",   azure: "Azure ML" },
  "vertex-ai":          { aws: "bedrock",     azure: "Azure OpenAI" },
  "azure-openai":       { aws: "bedrock",     gcp: "vertex-ai" },
  "sagemaker":          { gcp: "vertex-ai",   azure: "Azure ML" },
  "openai":             { aws: "bedrock",     gcp: "palm-api", azure: "Azure OpenAI" },
  "anthropic_claude":   { aws: "bedrock",     gcp: "vertex-ai", azure: "Azure OpenAI" },
  "mistral_ai":         { aws: "bedrock",     gcp: "vertex-ai", azure: "Azure OpenAI" },
  "cohere_ai":          { aws: "bedrock",     gcp: "vertex-ai", azure: "Azure OpenAI" },
};

// Class-level mapping (LangChain / SDK class names → cross-cloud equivalent)
const LLM_CLASS_CROSS_CLOUD_MAPPING = {
  ChatBedrock:       { aws: "ChatBedrock",       gcp: "ChatVertexAI",      azure: "AzureChatOpenAI" },
  ChatVertexAI:      { aws: "ChatBedrock",       gcp: "ChatVertexAI",      azure: "AzureChatOpenAI" },
  AzureChatOpenAI:   { aws: "ChatBedrock",       gcp: "ChatVertexAI",      azure: "AzureChatOpenAI" },
  ChatOpenAI:        { aws: "ChatBedrock",       gcp: "ChatVertexAI",      azure: "AzureChatOpenAI" },
  ChatAnthropic:     { aws: "ChatBedrock",       gcp: "ChatVertexAI",      azure: "AzureChatOpenAI" },
};

const EMBEDDING_CLASS_CROSS_CLOUD_MAPPING = {
  BedrockEmbeddings:      { aws: "BedrockEmbeddings",      gcp: "VertexAIEmbeddings",      azure: "AzureOpenAIEmbeddings" },
  VertexAIEmbeddings:     { aws: "BedrockEmbeddings",      gcp: "VertexAIEmbeddings",      azure: "AzureOpenAIEmbeddings" },
  AzureOpenAIEmbeddings:  { aws: "BedrockEmbeddings",      gcp: "VertexAIEmbeddings",      azure: "AzureOpenAIEmbeddings" },
  OpenAIEmbeddings:       { aws: "BedrockEmbeddings",      gcp: "VertexAIEmbeddings",      azure: "AzureOpenAIEmbeddings" },
};

const VECTOR_CLASS_CROSS_CLOUD_MAPPING = {
  OpenSearchVectorSearch: { aws: "OpenSearchVectorSearch", gcp: "VertexAIVectorSearch",  azure: "AzureSearch" },
  VertexAIVectorSearch:   { aws: "OpenSearchVectorSearch", gcp: "VertexAIVectorSearch",  azure: "AzureSearch" },
  AzureSearch:            { aws: "OpenSearchVectorSearch", gcp: "VertexAIVectorSearch",  azure: "AzureSearch" },
  // Portable (cloud-agnostic) — no change needed
  FAISS:    { aws: "FAISS",    gcp: "FAISS",    azure: "FAISS" },
  Chroma:   { aws: "Chroma",   gcp: "Chroma",   azure: "Chroma" },
  Weaviate: { aws: "Weaviate", gcp: "Weaviate", azure: "Weaviate" },
  Qdrant:   { aws: "Qdrant",   gcp: "Qdrant",   azure: "Qdrant" },
  Pinecone: { aws: "Pinecone", gcp: "Pinecone", azure: "Pinecone" },
};

const VECTOR_DB_CROSS_CLOUD_MAPPING = {
  "opensearch": { gcp: "cloud-firestore", azure: "cosmos-db" },
  "dynamodb": { gcp: "firestore", azure: "cosmos-db" },
  "pinecone": { aws: "pinecone", gcp: "pinecone", azure: "pinecone" },
  "weaviate": { aws: "weaviate", gcp: "weaviate", azure: "weaviate" },
  "chromadb": { aws: "chromadb", gcp: "chromadb", azure: "chromadb" },
};


function AIStackCard({ category, items, targetCloud, onMappingChange, isEditing }) {
  const colors = CATEGORY_COLORS[category] || CATEGORY_COLORS.framework;
  const Icon = colors.icon;

  const getTargetSuggestion = (sourceItem) => {
    if (category === "managed_ml" || category === "managed_llm") {
      return IAC_ML_CROSS_CLOUD_MAPPING[sourceItem]?.[targetCloud] || sourceItem;
    } else if (category === "llm") {
      return (
        LLM_CLASS_CROSS_CLOUD_MAPPING[sourceItem]?.[targetCloud] ||
        LLM_CROSS_CLOUD_MAPPING[sourceItem]?.[targetCloud] ||
        sourceItem
      );
    } else if (category === "embeddings") {
      return EMBEDDING_CLASS_CROSS_CLOUD_MAPPING[sourceItem]?.[targetCloud] || sourceItem;
    } else if (category === "vector_db") {
      return (
        VECTOR_CLASS_CROSS_CLOUD_MAPPING[sourceItem]?.[targetCloud] ||
        VECTOR_DB_CROSS_CLOUD_MAPPING[sourceItem]?.[targetCloud] ||
        sourceItem
      );
    }
    return sourceItem;
  };

  return (
    <div style={{ padding: "12px", border: "1px solid #e5e7eb", borderRadius: "6px", marginBottom: "12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
        <Icon size={18} style={{ color: colors.text }} />
        <h4 style={{ margin: 0, fontSize: "14px", fontWeight: "600" }}>
          {category === "managed_ml" ? "MANAGED ML PLATFORM"
            : category === "managed_llm" ? "MANAGED LLM SERVICE"
            : category.replace("_", " ").toUpperCase()}
        </h4>
      </div>

      {items.map((item, idx) => {
        const targetSuggestion = getTargetSuggestion(item.name || item);
        return (
          <div key={idx} style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <div
                style={{
                  background: colors.bg,
                  borderColor: colors.border,
                  borderWidth: "2px",
                  borderStyle: "solid",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  minWidth: "140px",
                  textAlign: "center",
                  color: colors.text,
                  fontWeight: "600",
                  fontSize: "13px",
                }}
              >
                {item.name || item}
              </div>
              {item.resource_type && (
                <span style={{ fontSize: "10px", color: "#94a3b8", textAlign: "center" }}>
                  {item.resource_type}
                </span>
              )}
            </div>
            <ArrowRight size={16} style={{ color: "#94a3b8" }} />
            {isEditing ? (
              <input
                type="text"
                defaultValue={targetSuggestion}
                onChange={(e) => onMappingChange?.(category, item.name || item, e.target.value)}
                style={{
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "2px solid #e5e7eb",
                  minWidth: "140px",
                  fontSize: "12px",
                }}
              />
            ) : (
              <div
                style={{
                  background: colors.bg,
                  borderColor: colors.border,
                  borderWidth: "2px",
                  borderStyle: "dashed",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  minWidth: "140px",
                  textAlign: "center",
                  color: colors.text,
                  fontWeight: "600",
                  fontSize: "13px",
                }}
              >
                {targetSuggestion}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function AIStackSection({
  sourceCloud = "aws",
  detectedAIStack = {},
  targetCloud = "gcp",
  onAIStackMappingChange,
  isRequired = true,
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [mappings, setMappings] = useState({});

  const handleMappingChange = useCallback(
    (_category, source, target) => {
      const newMapping = { ...mappings, [source]: target };
      setMappings(newMapping);
      onAIStackMappingChange?.(newMapping);
    },
    [mappings, onAIStackMappingChange]
  );

  // Build display data from detected AI stack (with fallback to user input)
  const displayData = {
    managed_ml: [],
    managed_llm: [],
    llm: [],
    embeddings: [],
    vector_db: [],
    framework: [],
  };

  // IaC-declared AI resources (SageMaker, Bedrock, Vertex AI, Azure ML…)
  if (detectedAIStack?.iac_ai_resources?.length > 0) {
    detectedAIStack.iac_ai_resources.forEach((res) => {
      const cat = res.type || "managed_ml";
      if (displayData[cat]) {
        displayData[cat].push({ name: res.name, detected: true, resource_type: res.resource_type });
      } else {
        displayData.managed_ml.push({ name: res.name, detected: true, resource_type: res.resource_type });
      }
    });
  }

  // Priority: detected Python analysis > user input
  // LLM — skip if already covered by an iac_ai_resource with the same provider family
  const iacProviderFamilies = new Set(
    (detectedAIStack?.iac_ai_resources || []).map((r) => {
      const n = (r.name || "").toLowerCase();
      if (n.includes("bedrock")) return "bedrock";
      if (n.includes("sagemaker")) return "sagemaker";
      if (n.includes("vertex")) return "vertex";
      if (n.includes("openai") || n.includes("cognitive")) return "openai";
      return n;
    })
  );
  const llmName = detectedAIStack?.detected_llm || detectedAIStack?.llm_provider;
  const llmFamily = llmName
    ? (llmName.toLowerCase().includes("bedrock") ? "bedrock"
      : llmName.toLowerCase().includes("sagemaker") ? "sagemaker"
      : llmName.toLowerCase().includes("vertex") ? "vertex"
      : llmName.toLowerCase().includes("openai") ? "openai"
      : llmName.toLowerCase())
    : null;
  const llmAlreadyCovered = llmFamily && iacProviderFamilies.has(llmFamily);

  if (!llmAlreadyCovered) {
    if (detectedAIStack?.detected_llm) {
      displayData.llm.push({ name: detectedAIStack.detected_llm, detected: true });
    } else if (detectedAIStack?.llm_provider) {
      displayData.llm.push({ name: detectedAIStack.llm_provider, detected: false });
    }
  }

  // Embeddings
  if (detectedAIStack?.detected_embeddings && Object.keys(detectedAIStack.detected_embeddings).length > 0) {
    Object.keys(detectedAIStack.detected_embeddings).forEach((emb) => {
      displayData.embeddings.push({ name: emb, detected: true });
    });
  } else if (detectedAIStack?.embedding_model) {
    displayData.embeddings.push({ name: detectedAIStack.embedding_model, detected: false });
  }

  // Vector DB
  if (detectedAIStack?.detected_vector_stores && detectedAIStack.detected_vector_stores.length > 0) {
    detectedAIStack.detected_vector_stores.forEach((vs) => {
      displayData.vector_db.push({ name: vs.class || vs, detected: true });
    });
  } else if (detectedAIStack?.vector_db) {
    displayData.vector_db.push({ name: detectedAIStack.vector_db, detected: false });
  }

  // Framework
  if (detectedAIStack?.framework) {
    displayData.framework.push({ name: detectedAIStack.framework, detected: false });
  }

  const hasAIStack = Object.values(displayData).some((items) => items.length > 0);

  return (
    <div style={{ padding: "20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <h3 style={{ margin: "0 0 8px 0", fontSize: "18px", fontWeight: "700" }}>
            🔧 AI Stack Configuration
            {isRequired && <span style={{ color: "#ef4444", marginLeft: "4px" }}>*</span>}
          </h3>
          <p style={{ margin: "0", fontSize: "13px", color: "#64748b" }}>
            Source: <strong>{sourceCloud.toUpperCase()}</strong> → Target: <strong>{targetCloud.toUpperCase()}</strong>
          </p>
        </div>
        <button
          onClick={() => setIsEditing(!isEditing)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            padding: "8px 12px",
            borderRadius: "6px",
            border: "1px solid #d1d5db",
            background: isEditing ? "#3b82f6" : "#f3f4f6",
            color: isEditing ? "white" : "#374151",
            cursor: "pointer",
            fontWeight: "600",
            fontSize: "13px",
          }}
        >
          {isEditing ? <Check size={16} /> : <Edit2 size={16} />}
          {isEditing ? "Save" : "Edit"}
        </button>
      </div>

      {hasAIStack ? (
        <div style={{ marginBottom: "16px" }}>
          {Object.entries(displayData).map(
            ([category, items]) =>
              items.length > 0 && (
                <AIStackCard
                  key={category}
                  category={category}
                  items={items}
                  sourceCloud={sourceCloud}
                  targetCloud={targetCloud}
                  onMappingChange={handleMappingChange}
                  isEditing={isEditing}
                />
              )
          )}
        </div>
      ) : (
        <div
          style={{
            padding: "16px",
            borderRadius: "6px",
            background: "#f0fdf4",
            border: "1px solid #bbf7d0",
            color: "#166534",
            fontSize: "13px",
            marginBottom: "16px",
          }}
        >
          ℹ️ No AI Stack detected. Your repository may not use LLM providers or vector databases.
        </div>
      )}

      {hasAIStack && (
        <div
          style={{
            padding: "12px",
            borderRadius: "6px",
            background: "#fef3c7",
            border: "1px solid #fcd34d",
            color: "#92400e",
            fontSize: "12px",
            lineHeight: "1.6",
          }}
        >
          <strong>⚠️ Migration Impact:</strong> Switching AI stack providers may require code changes (imports, model IDs, authentication). Cost and latency may differ.
        </div>
      )}
    </div>
  );
}
