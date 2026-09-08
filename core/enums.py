"""
enums.py - All application enums.
"""
from enum import Enum


class MigrationStatus(str, Enum):
    """Lifecycle status of a Migration job."""
    CREATED = "Created"
    ANALYZING = "Analyzing"
    ANALYSIS_FAILED = "Analysis_Failed"
    PLAN_READY = "Plan_Ready"
    REVIEWING = "Reviewing"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    GENERATING_IAC = "Generating_IaC"
    VALIDATING_INTENT = "Validating_Intent"
    IAC_READY = "IaC_Ready"
    DEPLOYING = "Deploying"
    HEALTH_CHECKING = "Health_Checking"
    CORRECTING = "Correcting"       # partial rejection — Agent 01 re-running
    COMPLETED = "Completed"
    EXPORTED = "Exported"
    FAILED = "Failed"


class CloudProvider(str, Enum):
    """Supported cloud providers."""
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"


class Timeline(str, Enum):
    """Migration timeline options."""
    FLEXIBLE = "Flexible"
    ASAP = "ASAP"
    THREE_MONTHS = "3 mois"
    SIX_MONTHS = "6 mois"
    TWELVE_MONTHS = "12 mois"


class DataResidency(str, Enum):
    """Data residency requirements."""
    NONE = "Aucune"
    EU = "Union Europeenne"
    FRANCE = "Strictement France"
    US = "US"


class AIFramework(str, Enum):
    """Agentic AI frameworks."""
    CREWAI = "CrewAI"
    LANGGRAPH = "LangGraph"
    AUTOGEN = "AutoGen"
    LLAMAINDEX = "LlamaIndex"
    HAYSTACK = "Haystack"
    OTHER = "Other"


class LLMProvider(str, Enum):
    """LLM providers / inference backends."""
    OPENAI = "OpenAI"
    BEDROCK = "Bedrock"
    AZURE_OPENAI = "Azure OpenAI"
    VERTEX_AI = "Vertex AI"
    OLLAMA = "Ollama"
    OTHER = "Other"


class VectorDB(str, Enum):
    """Vector database backends."""
    CHROMADB = "ChromaDB"
    PINECONE = "Pinecone"
    WEAVIATE = "Weaviate"
    QDRANT = "Qdrant"
    PGVECTOR = "pgvector"
    FAISS = "FAISS"
    NONE = "None"
    OTHER = "Other"
