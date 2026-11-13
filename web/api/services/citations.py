"""Link values in tool results to spans in agent explanations."""

from __future__ import annotations

import re
from typing import Any

from api.models.replay import Citation, CitationSource, ExplanationSpan


def flatten_tool_values(
    result: list[dict[str, Any]] | None,
    tool_name: str,
) -> list[tuple[Any, str, str]]:
    """Return (raw_value, json_path, field_name) for citable leaves."""
    if not result:
        return []
    out: list[tuple[Any, str, str]] = []
    for i, row in enumerate(result):
        if not isinstance(row, dict):
            continue
        for key, val in row.items():
            path = f"[{i}].{key}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                out.append((val, path, key))
            elif isinstance(val, str) and 1 < len(val) <= 64:
                out.append((val, path, key))
    return out


def find_citations(
    explanation: str,
    tool_result: list[dict[str, Any]] | None,
    tool_name: str,
) -> list[Citation]:
    """Scan explanation for values present in the tool result."""
    if not explanation or not tool_result:
        return []

    candidates = flatten_tool_values(tool_result, tool_name)
    # Prefer longer string matches and more distinctive numbers first.
    candidates.sort(
        key=lambda item: (
            0 if isinstance(item[0], str) else 1,
            -len(str(item[0])),
            -_number_specificity(item[0]),
        )
    )

    occupied: list[tuple[int, int]] = []
    citations: list[Citation] = []

    for raw, path, field in candidates:
        span = _find_span(explanation, raw, occupied)
        if span is None:
            continue
        start, end, match_kind, display = span
        occupied.append((start, end))
        citations.append(
            Citation(
                value=raw,
                display=display,
                explanation_span=ExplanationSpan(start=start, end=end),
                source=CitationSource(
                    tool_name=tool_name,
                    path=path,
                    field=field,
                    raw=raw,
                ),
                match_kind=match_kind,  # type: ignore[arg-type]
            )
        )

    citations.sort(key=lambda c: c.explanation_span.start)
    return citations


def _number_specificity(val: Any) -> int:
    if isinstance(val, int):
        return abs(val)
    if isinstance(val, float):
        return int(abs(val) * 1000)
    return 0


def _overlaps(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
    for a, b in occupied:
        if start < b and end > a:
            return True
    return False


def _find_span(
    text: str,
    raw: Any,
    occupied: list[tuple[int, int]],
) -> tuple[int, int, str, str] | None:
    if isinstance(raw, str):
        return _find_string_span(text, raw, occupied)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return _find_number_span(text, raw, occupied)
    return None


def _find_string_span(
    text: str,
    value: str,
    occupied: list[tuple[int, int]],
) -> tuple[int, int, str, str] | None:
    # Case-sensitive first, then case-insensitive.
    for flags in (0, re.IGNORECASE):
        for m in re.finditer(re.escape(value), text, flags):
            start, end = m.start(), m.end()
            if not _overlaps(start, end, occupied):
                return start, end, "exact_string", text[start:end]
    return None


def _find_number_span(
    text: str,
    value: int | float,
    occupied: list[tuple[int, int]],
) -> tuple[int, int, str, str] | None:
    patterns: list[tuple[str, str]] = []

    if isinstance(value, float):
        # Exact-ish float forms: 0.002, ~0.002, 0.02
        plain = f"{value:g}"
        patterns.append((rf"(?:~|≈|~≈)?\s*{re.escape(plain)}", "exact_number"))
        # One more decimal form if useful
        if plain != f"{value:.4f}".rstrip("0").rstrip("."):
            alt = f"{value:.4f}".rstrip("0").rstrip(".")
            patterns.append((rf"(?:~|≈)?\s*{re.escape(alt)}", "approx_number"))
    else:
        patterns.append((rf"(?<![\d.]){value}(?![\d.])", "exact_number"))
        # Also allow 1,600 style (rare in our data)
        if abs(value) >= 1000:
            grouped = f"{value:,}"
            patterns.append((rf"(?<![\d.]){re.escape(grouped)}(?![\d.])", "exact_number"))

    for pattern, kind in patterns:
        for m in re.finditer(pattern, text):
            start, end = m.start(), m.end()
            # Trim leading approx symbols from display but keep span accurate to match.
            display = text[start:end].strip()
            if not _overlaps(start, end, occupied):
                return start, end, kind, display
    return None
