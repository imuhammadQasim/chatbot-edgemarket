"""Service settings, read from the environment / `.env`.

Every secret and model name lives in `.env`; nothing here is provider-specific
beyond the key names. Switching LLM provider is an env change only.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["openai", "gemini", "groq"]

# Origins the widget is served from. ALLOWED_ORIGINS adds to these, it never
# removes them, so a typo in .env cannot lock the live site out.
REQUIRED_ORIGINS = (
    "https://edgemarket.ai",
    "https://www.edgemarket.ai",
    "https://edgemarket-staging.webevis.com",
    "http://localhost:5174",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"

    # --- LLM -----------------------------------------------------------------
    llm_provider: Provider = "groq"
    llm_model: str = "openai/gpt-oss-120b"
    # Unset means "provider default". Some reasoning models reject any value.
    llm_temperature: float | None = None
    # Accepted by all three providers (low/medium/high; gemini also "minimal",
    # openai also "none"). Unset means provider default.
    llm_reasoning_effort: str | None = None
    # Extra constructor kwargs as JSON, e.g. {"max_tokens": 800}. Env-only tuning.
    llm_model_kwargs: dict[str, Any] = Field(default_factory=dict)
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2

    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    groq_api_key: SecretStr | None = None

    # --- Storage -------------------------------------------------------------
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "edgemarket_chatbot"

    # --- EdgeMarket backend --------------------------------------------------
    edgemarket_api_base_url: str = "https://edgemarket.ai"
    # Where market links in answers point. Usually the same host as the API.
    edgemarket_site_url: str = "https://edgemarket.ai"
    http_timeout_s: float = 10.0
    stats_cache_s: float = 15.0

    # --- Chat limits ---------------------------------------------------------
    history_limit: int = 20
    max_messages_per_session: int = 50
    max_message_chars: int = 1000
    session_ttl_days: int = 30
    rate_limit: str = "20/5 minutes"
    sse_ping_s: float = 15.0
    max_tool_calls_per_turn: int = 6

    # Comma separated; merged with REQUIRED_ORIGINS.
    allowed_origins: str = "http://localhost:5173,http://localhost:5175"

    @property
    def cors_origins(self) -> list[str]:
        extra = [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]
        return list(dict.fromkeys([*REQUIRED_ORIGINS, *extra]))

    @property
    def provider_api_key(self) -> SecretStr | None:
        return {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
        }[self.llm_provider]

    @model_validator(mode="after")
    def _production_guard(self) -> "Settings":
        if self.app_env == "production" and self.llm_provider != "openai":
            raise ValueError(
                f"APP_ENV=production requires LLM_PROVIDER=openai, got LLM_PROVIDER={self.llm_provider!r}. "
                "Gemini and Groq are for development testing only."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
