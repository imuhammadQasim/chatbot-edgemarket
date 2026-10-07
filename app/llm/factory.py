"""Chat model construction. The only place that knows provider names."""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config import Settings

# LLM_PROVIDER value -> init_chat_model provider id, and the env var holding its key.
_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai": ("openai", "OPENAI_API_KEY"),
    "gemini": ("google_genai", "GEMINI_API_KEY"),
    "groq": ("groq", "GROQ_API_KEY"),
}


class LLMConfigError(RuntimeError):
    pass


def get_chat_model(settings: Settings) -> BaseChatModel:
    provider_id, key_env = _PROVIDERS[settings.llm_provider]
    api_key = settings.provider_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise LLMConfigError(
            f"LLM_PROVIDER={settings.llm_provider} but {key_env} is not set. Add it to .env."
        )

    kwargs: dict = {
        "api_key": api_key.get_secret_value(),
        "timeout": settings.llm_timeout_s,
        "max_retries": settings.llm_max_retries,
    }
    if settings.llm_temperature is not None:
        kwargs["temperature"] = settings.llm_temperature
    if settings.llm_reasoning_effort:
        kwargs["reasoning_effort"] = settings.llm_reasoning_effort
    if settings.llm_provider == "openai":
        # Current OpenAI models only call tools freely through the Responses
        # API; stream_usage makes the final chunk carry token counts.
        kwargs["use_responses_api"] = True
        kwargs["stream_usage"] = True
    kwargs.update(settings.llm_model_kwargs)

    return init_chat_model(settings.llm_model, model_provider=provider_id, **kwargs)
