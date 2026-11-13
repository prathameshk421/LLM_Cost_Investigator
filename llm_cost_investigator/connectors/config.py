"""Named connection profiles (non-secret metadata only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_cost_investigator.connectors.base import ConnectorConfigError

ConnectorName = Literal["langfuse"]
ApiVersion = Literal["v1", "v2"]


class ConnectionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since_hours: float | None = None
    max_rows: int | None = None
    feature_default: str | None = None


class ConnectionProfile(BaseModel):
    """One named remote connection. Never stores API keys."""

    model_config = ConfigDict(extra="forbid")

    name: str
    connector: ConnectorName = "langfuse"
    host: str | None = None
    api_version: ApiVersion = "v1"
    defaults: ConnectionDefaults = Field(default_factory=ConnectionDefaults)
    filters: dict[str, Any] = Field(default_factory=dict)


class ConnectionsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    connections: dict[str, dict[str, Any]] = Field(default_factory=dict)


def default_config_search_paths() -> list[Path]:
    home = Path.home()
    return [
        Path.cwd() / ".llmci" / "connections.yaml",
        Path.cwd() / ".llmci" / "connections.yml",
        home / ".config" / "llm-cost-investigator" / "connections.yaml",
        home / ".config" / "llm-cost-investigator" / "connections.yml",
    ]


def find_config_path(explicit: Path | str | None = None) -> Path | None:
    if explicit is not None:
        path = Path(explicit)
        return path if path.is_file() else None
    for path in default_config_search_paths():
        if path.is_file():
            return path
    return None


def _load_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ConnectorConfigError(
            "PyYAML is required to load connection profiles. "
            "Install with: pip install pyyaml"
        ) from exc
    return yaml.safe_load(text)


def load_profiles(config_path: Path | str | None = None) -> dict[str, ConnectionProfile]:
    """Load named profiles from YAML. Empty dict if no file found."""
    path = find_config_path(config_path)
    if path is None:
        if config_path is not None:
            raise ConnectorConfigError(f"Connections file not found: {config_path}")
        return {}

    try:
        raw = _load_yaml(path.read_text(encoding="utf-8"))
    except ConnectorConfigError:
        raise
    except Exception as exc:
        raise ConnectorConfigError(f"Failed to parse {path}: {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConnectorConfigError(f"{path}: root must be a mapping")

    connections = raw.get("connections") or {}
    if not isinstance(connections, dict):
        raise ConnectorConfigError(f"{path}: 'connections' must be a mapping")

    profiles: dict[str, ConnectionProfile] = {}
    for name, body in connections.items():
        if not isinstance(body, dict):
            raise ConnectorConfigError(f"{path}: connection {name!r} must be a mapping")
        # Reject accidental secret fields
        for forbidden in ("secret_key", "public_key", "api_key", "password", "token"):
            if forbidden in body:
                raise ConnectorConfigError(
                    f"{path}: connection {name!r} must not contain {forbidden!r}; "
                    "use environment variables for secrets"
                )
        try:
            data = dict(body)
            data["name"] = str(name)
            profiles[str(name)] = ConnectionProfile.model_validate(data)
        except Exception as exc:
            raise ConnectorConfigError(
                f"{path}: invalid connection {name!r}: {exc}"
            ) from exc
    return profiles


def get_profile(
    name: str,
    *,
    config_path: Path | str | None = None,
) -> ConnectionProfile:
    profiles = load_profiles(config_path)
    if name not in profiles:
        available = ", ".join(sorted(profiles)) or "(none)"
        raise ConnectorConfigError(
            f"Unknown connection profile {name!r}. Available: {available}"
        )
    return profiles[name]


def list_profile_summaries(
    config_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Public summaries safe for API/CLI (no secrets)."""
    profiles = load_profiles(config_path)
    out: list[dict[str, Any]] = []
    for name, p in sorted(profiles.items()):
        out.append(
            {
                "name": name,
                "connector": p.connector,
                "host": p.host,
                "api_version": p.api_version,
                "defaults": p.defaults.model_dump(exclude_none=True),
                "filters": dict(p.filters),
            }
        )
    return out
