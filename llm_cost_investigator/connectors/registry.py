"""Connector name → factory."""

from __future__ import annotations

from typing import Any, Callable

from llm_cost_investigator.connectors.base import ConnectorConfigError, LogConnector
from llm_cost_investigator.connectors.langfuse import LangfuseConnector
from llm_cost_investigator.connectors.secrets import langfuse_env_var_names

ConnectorFactory = Callable[..., LogConnector]

_REGISTRY: dict[str, ConnectorFactory] = {
    "langfuse": lambda **kwargs: LangfuseConnector(**kwargs),
}


def registered_connectors() -> list[dict[str, Any]]:
    return [
        {
            "name": "langfuse",
            "description": "Langfuse GENERATION observations via Public API",
            "required_env": langfuse_env_var_names()[:2],  # keys only required
            "optional_env": [langfuse_env_var_names()[2]],
        }
    ]


def build_connector(
    name: str,
    *,
    host: str | None = None,
    api_version: str = "v1",
    connection_name: str | None = None,
) -> LogConnector:
    factory = _REGISTRY.get(name)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ConnectorConfigError(
            f"Unknown connector {name!r}. Registered: {known}"
        )
    return factory(
        host=host,
        api_version=api_version,
        connection_name=connection_name,
    )
