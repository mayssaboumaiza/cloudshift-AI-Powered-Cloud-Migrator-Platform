import logging
import os
from enum import Enum
from typing import List, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_settings_logger = logging.getLogger("Settings")


# -------------------------------
# ENV ENUM
# -------------------------------
class AppEnv(str, Enum):
    dev = "development"
    staging = "staging"
    prod = "production"


def _resolve_env_file() -> str:
    """Pick the correct .env file based on APP_ENV environment variable.

    Resolution order:
      1. .env.<APP_ENV>  (e.g. .env.production)
      2. .env            (fallback — always loaded if specific file missing)

    Usage:
      APP_ENV=production uvicorn app:app   → loads .env.production
      APP_ENV=staging    uvicorn app:app   → loads .env.staging
      (no APP_ENV)                         → loads .env (development default)
    """
    env_name = os.getenv("APP_ENV", "development")
    specific = f".env.{env_name}"
    return specific if os.path.exists(specific) else ".env"


# -------------------------------
# APP CONFIG
# -------------------------------
class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",  # <-- VERY IMPORTANT (ignore DB_*, LOG_*, etc)
        env_ignore_empty=True,  # skip empty env vars (avoids json.loads("") crash)
        env_list_delimiter=",",  # parse List[str] fields as comma-separated, not JSON
        populate_by_name=True,   # allow field name in addition to alias
    )

    # Core app runtime
    APP_HOST: str = Field(default="localhost")
    APP_PORT: int = Field(default=7777)
    APP_ENV: AppEnv = Field(default=AppEnv.dev)
    WORKERS_COUNT: int = Field(default=5)

    # API metadata — env var names differ from field names so keep aliases
    TITLE: str = Field(default="Workspace Service API", validation_alias="API_TITLE")
    DESCRIPTION: str = Field(
        default="Manages workspaces with CRUD operations.", validation_alias="API_DESCRIPTION"
    )
    VERSION: str = Field(default="1.0.0", validation_alias="API_VERSION")
    API_PREFIX: str = Field(default="/api")
    DOCS_URL: str = Field(default="/api/docs", validation_alias="API_DOCS_URL")
    REDOC_URL: str = Field(default="/api/redoc", validation_alias="API_REDOC_URL")
    OPENAPI_URL: str = Field(default="/api/openapi.json", validation_alias="API_OPENAPI_URL")

    # GitHub token (fallback when client does not supply one)
    GITHUB_TOKEN: str = Field(default="")

    # Fernet encryption key for GitHub tokens stored in DB.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # If empty, tokens are stored in plaintext (warning logged at startup).
    FERNET_KEY: str = Field(default="")

    # P34: Support key rotation via comma-separated list of Fernet keys.
    # The FIRST key is used for new encryptions; all keys are tried for decryption.
    # Example: FERNET_KEYS="new_key,old_key"
    # When set, takes precedence over FERNET_KEY.
    FERNET_KEYS: str = Field(default="")

    # ── Azure OpenAI deployment names ───────────────────────────────────────
    # All values are read from the .env file (env var of the same name).
    # The defaults below are only the fallback when the env var is absent.
    #
    #   AZURE_MODEL      → default deployment for the three ReAct agents
    #                      (Agent 01 planner, Agent 02 IaC, Agent 03 deploy).
    #                      Per-agent override: AZURE_MODEL_01 / _02 / _03.
    #   AZURE_MODEL_RAG  → deployment for the GraphRAG conversational chatbot
    #                      (cheaper/faster model is enough for chat answers).
    #
    # Agents require strong reasoning + reliable native function-calling → gpt-5.1.
    # The chatbot only summarises retrieved context → gpt-4o is sufficient.
    # ────────────────────────────────────────────────────────────────────────
    AZURE_MODEL: str = Field(default="gpt-5.1")
    AZURE_MODEL_RAG: str = Field(default="gpt-4o")
    AZURE_OPENAI_API_VERSION: str = Field(default="2025-04-01-preview")

    # Rate limiting (requests per minute per IP for LLM-heavy endpoints)
    RATE_LIMIT_ANALYZE: str = Field(default="5/minute")
    RATE_LIMIT_ACCEPT: str = Field(default="10/minute")
    RATE_LIMIT_REJECT: str = Field(default="10/minute")

    # CORS list
    CORS_ORIGINS: List[str] = Field(default=["*"], description="Allowed CORS origins")

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def normalize_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        """Always return a clean list of strings."""
        # Case 1 → Comma-separated "a,b,c" or empty string
        if isinstance(v, str):
            if not v.strip():
                return ["*"]
            return [origin.strip() for origin in v.split(",") if origin.strip()]

        # Case 2 → Already a list
        if isinstance(v, list):
            return v

        raise ValueError("Invalid CORS_ORIGINS format. Must be string or list.")


# -------------------------------
# DATABASE CONFIG
# -------------------------------
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",  # <-- IMPORTANT
        populate_by_name=True,
    )

    DB_USER: str = Field(default="")
    DB_PASSWORD: str = Field(default="")
    DB_HOST: str = Field(default="localhost")
    DB_PORT: int = Field(default=5432)
    DB_NAME: str = Field(default="cloud_migrator")

    POOL_SIZE: int = Field(default=5, validation_alias="DB_POOL_SIZE")
    MAX_OVERFLOW: int = Field(default=10, validation_alias="DB_MAX_OVERFLOW")
    POOL_TIMEOUT: int = Field(default=30, validation_alias="DB_POOL_TIMEOUT")
    POOL_RECYCLE: int = Field(default=1800, validation_alias="DB_POOL_RECYCLE")
    ECHO: bool = Field(default=False, validation_alias="DB_ECHO")

    ENABLE_MULTI_TENANT: bool = Field(default=False, validation_alias="DB_ENABLE_MULTI_TENANT")
    DEFAULT_TENANT_ID: str = Field(default="default", validation_alias="DB_DEFAULT_TENANT_ID")

    @property
    def POSTGRES_URI(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = AppConfig()
db_settings = DatabaseSettings()

# ── P36: CORS safety check in production ─────────────────────────────────────
if settings.APP_ENV == AppEnv.prod and "*" in settings.CORS_ORIGINS:
    raise SystemExit(
        "[FATAL] CORS_ORIGINS='*' is forbidden in production. "
        "Set CORS_ORIGINS to your actual frontend domain(s) in .env.production "
        "or set APP_ENV=development to bypass this check."
    )

# ── P34: Fernet key rotation helper ──────────────────────────────────────────
def get_fernet():
    """Return a MultiFernet if FERNET_KEYS is set, else a single Fernet from FERNET_KEY.

    MultiFernet tries each key in order — enables zero-downtime key rotation:
    1. Add the new key as the FIRST entry in FERNET_KEYS.
    2. Re-encrypt stored tokens in the background (optional).
    3. Remove the old key from FERNET_KEYS once all tokens are migrated.
    """
    try:
        from cryptography.fernet import Fernet, MultiFernet
    except ImportError:
        return None

    raw_keys = settings.FERNET_KEYS.strip()
    if raw_keys:
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if keys:
            try:
                fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
                return MultiFernet(fernets)
            except Exception as e:
                _settings_logger.error(f"Invalid FERNET_KEYS — falling back to FERNET_KEY: {e}")

    single = settings.FERNET_KEY.strip()
    if single:
        try:
            return Fernet(single.encode() if isinstance(single, str) else single)
        except Exception as e:
            _settings_logger.error(f"Invalid FERNET_KEY: {e}")

    _settings_logger.warning(
        "No FERNET_KEY or FERNET_KEYS configured — GitHub tokens stored in plaintext."
    )
    return None
