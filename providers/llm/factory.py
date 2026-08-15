from __future__ import annotations

import os

from .anthropic_compatible import AnthropicLLMProvider
from .base import LLMProvider
from .openai_compatible import OpenAICompatibleLLMProvider


def get_llm_provider(
    provider: str | None = None,
) -> LLMProvider:
    provider = (
        provider
        or os.environ.get("LLM_PROVIDER")
        or "anthropic"
    ).lower()

    if provider == "anthropic":
        return AnthropicLLMProvider(
            default_model="claude-haiku-4-5-20251001",
        )

    # if provider == "groq":
    #     return OpenAICompatibleLLMProvider(
    #         provider_name="groq",
    #         default_model="openai/gpt-oss-20b",
    #         base_url="https://api.groq.com/openai/v1",
    #         api_key_env="GROQ_API_KEY",
    #     )
    #
    # if provider == "ollama":
    #     return OpenAICompatibleLLMProvider(
    #         provider_name="ollama",
    #         default_model="llama3.2",
    #         base_url=os.environ.get("LLM_OLLAMA_ADDR", "http://localhost:11434/v1"),
    #         api_key_env=None,
    #     )

    if provider == "openai":
        return OpenAICompatibleLLMProvider(
            provider_name=os.environ.get("OPENAI_PROVIDER_NAME","openai"),
            default_model=os.environ.get("OPENAI_LLM", "gpt-5.4-nano"),
            base_url=os.environ.get("OPENAI_BASEURL", None),
            api_key_env="OPENAI_API_KEY",
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER {provider!r}. "
        "Choose one of: anthropic, groq, ollama, openai."
    )