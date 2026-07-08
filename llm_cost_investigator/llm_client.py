"""OpenAI-compatible LLM client selection for diagnostic agents."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Literal

from llm_cost_investigator.agents import default_mock_llm_client

ProviderName = Literal["groq", "cerebras", "fallback"]


PROVIDER_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
}

PROVIDER_API_KEY_ENV: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
}

PROVIDER_MODEL_ENV: dict[str, str] = {
    "groq": "GROQ_MODEL",
    "cerebras": "CEREBRAS_MODEL",
}

DEFAULT_MODELS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "cerebras": "llama3.1-8b",
}


@dataclass(frozen=True)
class LLMClientConfig:
    """User-facing configuration for diagnostic LLM calls."""

    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class ResolvedLLMClient:
    """Callable client and provenance selected for one run."""

    client: Callable[[str], str]
    provider: ProviderName
    model: str | None = None
    fallback_reason: str | None = None

    @property
    def fallback_used(self) -> bool:
        return self.provider == "fallback"


def resolve_llm_client(config: LLMClientConfig | None = None) -> ResolvedLLMClient:
    """Resolve Groq/Cerebras when configured, otherwise deterministic fallback."""
    config = config or LLMClientConfig()
    provider = _normalise_provider(config.provider or os.environ.get("LLM_PROVIDER"))

    if provider == "fallback":
        return ResolvedLLMClient(
            client=default_mock_llm_client,
            provider="fallback",
            fallback_reason="fallback provider explicitly selected",
        )

    selected_provider = provider or _first_configured_provider()
    if selected_provider is None:
        return ResolvedLLMClient(
            client=default_mock_llm_client,
            provider="fallback",
            fallback_reason="no GROQ_API_KEY or CEREBRAS_API_KEY configured",
        )

    api_key = os.environ.get(PROVIDER_API_KEY_ENV[selected_provider], "").strip()
    if not api_key:
        return ResolvedLLMClient(
            client=default_mock_llm_client,
            provider="fallback",
            fallback_reason=(
                f"{PROVIDER_API_KEY_ENV[selected_provider]} is not configured"
            ),
        )

    model = _resolve_model(selected_provider, config.model)
    return ResolvedLLMClient(
        client=_build_openai_compatible_client(
            api_key=api_key,
            base_url=PROVIDER_BASE_URLS[selected_provider],
            model=model,
        ),
        provider=selected_provider,
        model=model,
    )


def _normalise_provider(provider: str | None) -> ProviderName | None:
    if provider is None or not provider.strip():
        return None

    normalised = provider.strip().lower()
    if normalised == "cerebus":
        normalised = "cerebras"

    if normalised not in {"groq", "cerebras", "fallback"}:
        raise ValueError(
            "Unsupported LLM provider. Use groq, cerebras, or fallback."
        )
    return normalised  # type: ignore[return-value]


def _first_configured_provider() -> ProviderName | None:
    for provider in ("groq", "cerebras"):
        env_name = PROVIDER_API_KEY_ENV[provider]
        if os.environ.get(env_name, "").strip():
            return provider  # type: ignore[return-value]
    return None


def _resolve_model(provider: str, explicit_model: str | None) -> str:
    if explicit_model and explicit_model.strip():
        return explicit_model.strip()

    env_name = PROVIDER_MODEL_ENV[provider]
    env_model = os.environ.get(env_name, "").strip()
    if env_model:
        return env_model

    generic_model = os.environ.get("LLM_MODEL", "").strip()
    if generic_model:
        return generic_model

    return DEFAULT_MODELS[provider]


def _build_openai_compatible_client(
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> Callable[[str], str]:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(
            "The openai package is required for Groq/Cerebras API calls. "
            "Install project dependencies or use provider=fallback."
        ) from exc

    client = OpenAI(api_key=api_key, base_url=base_url)

    def call(prompt: str) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content
        return content or ""

    return call
