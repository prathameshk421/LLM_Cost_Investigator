"""Deterministic fallback mode for demos and tests.

Usage
-----
Import ``FALLBACK_LLM_CLIENT`` for a guaranteed-safe mock, or call
``make_llm_client()`` to keep older demos on deterministic fallback:

    from llm_cost_investigator.fallback import FALLBACK_LLM_CLIENT
    client = FALLBACK_LLM_CLIENT
    evidence = run_agents(routed, anomaly, llm_client=client)

Production provider auto-selection lives in ``llm_cost_investigator.llm_client``.
This module remains as a compatibility shim for tests and local demos that must
never call a real LLM.
"""

from __future__ import annotations

import warnings
from typing import Callable

from llm_cost_investigator.agents import default_mock_llm_client

def make_llm_client(
    real_client: Callable[[str], str] | None = None,
) -> Callable[[str], str]:
    """Return a supplied client, otherwise the deterministic fallback client.

    New code should use ``llm_client.resolve_llm_client`` so Groq/Cerebras keys
    are honored. This helper is intentionally fallback-biased.
    """
    if real_client is not None:
        return real_client

    warnings.warn(
        "make_llm_client now returns deterministic fallback by default. "
        "Use llm_client.resolve_llm_client for Groq/Cerebras auto-selection.",
        DeprecationWarning,
        stacklevel=2,
    )

    return default_mock_llm_client


# Module-level constant — import this directly for zero-boilerplate demos/tests.
FALLBACK_LLM_CLIENT: Callable[[str], str] = default_mock_llm_client
