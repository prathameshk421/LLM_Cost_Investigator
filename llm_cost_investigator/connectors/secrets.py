"""Resolve connector secrets from environment only (never from request bodies)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from llm_cost_investigator.connectors.base import ConnectorAuthError

# Load project .env if present (same pattern as llm_client)
_ENV_LOADED = False


def _ensure_dotenv() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    try:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        env_path = root / ".env"
        if not env_path.is_file():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                os.environ.setdefault(key, value)
    except Exception:
        pass


LANGFUSE_PUBLIC_KEY_ENV = "LANGFUSE_PUBLIC_KEY"
LANGFUSE_SECRET_KEY_ENV = "LANGFUSE_SECRET_KEY"
LANGFUSE_HOST_ENV = "LANGFUSE_HOST"
DEFAULT_LANGFUSE_HOST = "https://cloud.langfuse.com"


@dataclass(frozen=True)
class LangfuseCredentials:
    public_key: str
    secret_key: str
    host: str


def resolve_langfuse_credentials(
    *,
    host_override: str | None = None,
) -> LangfuseCredentials:
    """Read Langfuse keys from env. Raises ConnectorAuthError if missing."""
    _ensure_dotenv()
    public = os.environ.get(LANGFUSE_PUBLIC_KEY_ENV, "").strip()
    secret = os.environ.get(LANGFUSE_SECRET_KEY_ENV, "").strip()
    if not public or not secret:
        missing = [
            name
            for name, val in (
                (LANGFUSE_PUBLIC_KEY_ENV, public),
                (LANGFUSE_SECRET_KEY_ENV, secret),
            )
            if not val
        ]
        raise ConnectorAuthError(
            "Missing Langfuse credentials. Set "
            + ", ".join(missing)
            + " in the environment or project .env file."
        )
    host = (host_override or os.environ.get(LANGFUSE_HOST_ENV, "") or DEFAULT_LANGFUSE_HOST).strip()
    host = host.rstrip("/")
    return LangfuseCredentials(public_key=public, secret_key=secret, host=host)


def langfuse_env_var_names() -> list[str]:
    return [LANGFUSE_PUBLIC_KEY_ENV, LANGFUSE_SECRET_KEY_ENV, LANGFUSE_HOST_ENV]
