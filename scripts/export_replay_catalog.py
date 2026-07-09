#!/usr/bin/env python3
"""Normalize reports + live transcripts into data/replay/*.json for the replay UI.

Usage:
  python3 scripts/export_replay_catalog.py
  python3 scripts/export_replay_catalog.py --provider groq
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

from api.models.replay import (  # noqa: E402
    CallSummary,
    Citation,
    DecisionComparison,
    EvidenceSummary,
    ReplayIncident,
    ReplayMeta,
    ReplayStage,
    RootCauseBrief,
    RouterCandidate,
    SignalHighlight,
    TimeWindow,
    ToolCallRecord,
)
from api.services.citations import find_citations  # noqa: E402
from llm_cost_investigator.agents import (  # noqa: E402
    _model_routing_tool_required,
    _token_context_tool_required,
)
from llm_cost_investigator.router import route_agents  # noqa: E402
from llm_cost_investigator.schemas import AnomalyWindow, IncidentReport  # noqa: E402

INCIDENTS_DIR = ROOT / "data" / "reports" / "incidents"
TRANSCRIPTS_DIR = ROOT / "data" / "reports" / "transcripts"
OUT_DIR = ROOT / "data" / "replay"

MAIN_SCENARIOS = ("retry_loop", "context_bloat", "model_misroute")

TITLES = {
    "retry_loop": "Retry Loop — Uncapped Retries",
    "context_bloat": "Context Bloat — Self-Calling Chain",
    "model_misroute": "Model Misroute — Expensive Model",
    "token_context_thin": "Token Context Thin — MUST CALL Tool",
    "model_routing_thin": "Model Routing Thin — MUST CALL Tool",
    "retry_loop_thin": "Retry Loop Thin — MUST CALL Tool",
}

AGENT_TOOL = {
    "retry_loop_agent": "get_call_chain",
    "token_context_agent": "get_call_chain",
    "model_routing_agent": "get_window_calls",
}


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------

def retry_loop_tool_required(signals: dict[str, Any]) -> tuple[bool, str, list[DecisionComparison]]:
    retry_z = float(signals.get("retry_z_score", 0.0))
    rpc = int(signals.get("repeated_parent_call_count", 0))
    tool_required = retry_z < 2.5 and rpc >= 1
    c1 = DecisionComparison(
        metric="retry_z_score",
        value=retry_z,
        operator="<",
        threshold=2.5,
        passed=retry_z < 2.5,
    )
    c2 = DecisionComparison(
        metric="repeated_parent_call_count",
        value=rpc,
        operator=">=",
        threshold=1,
        passed=rpc >= 1,
    )
    if tool_required:
        line = (
            f"DECISION: MUST CALL get_call_chain — "
            f"retry_z_score={retry_z} (below 2.5) AND "
            f"repeated_parent_call_count={rpc} (>= 1). "
            f"Call get_call_chain on at least one available_call_id before answering."
        )
    else:
        line = (
            f"DECISION: DO NOT call get_call_chain — "
            f"retry_z_score={retry_z} (>= 2.5) OR "
            f"repeated_parent_call_count={rpc} (= 0). "
            f"Answer directly from the aggregate signals. Skip the tool entirely."
        )
    return tool_required, line, [c1, c2]


def token_context_decision(signals: dict[str, Any]) -> tuple[bool, str, list[DecisionComparison]]:
    tool_required, line = _token_context_tool_required(signals)
    input_z = float(signals.get("input_tokens_z_score", 0.0))
    depth = int(signals.get("max_call_chain_depth", 0))
    comparisons = [
        DecisionComparison(
            metric="input_tokens_z_score",
            value=input_z,
            operator="<",
            threshold=3.0,
            passed=input_z < 3.0,
        ),
        DecisionComparison(
            metric="max_call_chain_depth",
            value=depth,
            operator=">=",
            threshold=2,
            passed=depth >= 2,
        ),
    ]
    return tool_required, line, comparisons


def model_routing_decision(signals: dict[str, Any]) -> tuple[bool, str, list[DecisionComparison]]:
    tool_required, line = _model_routing_tool_required(signals)
    model_changed = bool(signals.get("model_changed", False))
    cost_z = float(signals.get("cost_z_score", 0.0))
    comparisons = [
        DecisionComparison(
            metric="model_changed",
            value=model_changed,
            operator="==",
            threshold=True,
            passed=model_changed is True,
        ),
        DecisionComparison(
            metric="cost_z_score",
            value=cost_z,
            operator="<",
            threshold=3.0,
            passed=cost_z < 3.0,
        ),
    ]
    return tool_required, line, comparisons


def agent_decision(
    agent_name: str, signals: dict[str, Any]
) -> tuple[bool, str, list[DecisionComparison], str]:
    tool = AGENT_TOOL[agent_name]
    if agent_name == "retry_loop_agent":
        req, line, comps = retry_loop_tool_required(signals)
    elif agent_name == "token_context_agent":
        req, line, comps = token_context_decision(signals)
    elif agent_name == "model_routing_agent":
        req, line, comps = model_routing_decision(signals)
    else:
        raise ValueError(f"Unknown agent: {agent_name}")
    return req, line, comps, tool


# ---------------------------------------------------------------------------
# Router reasons
# ---------------------------------------------------------------------------

def router_reasons(signals: Any) -> list[RouterCandidate]:
    selected = set(route_agents(
        AnomalyWindow(
            feature_tag="_",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            signals=signals if hasattr(signals, "model_dump") else signals,
        )
        if hasattr(signals, "cost_z_score")
        else _signals_from_dict(signals)
    ))

    # Build reasons from raw signal values
    if hasattr(signals, "model_dump"):
        s = signals
        model_changed = s.model_changed
        cost_z = s.cost_z_score
        token_pct = s.token_growth_pct
        retry_z = s.retry_z_score
        max_retry = s.max_retry_count
        input_z = s.input_tokens_z_score
        input_growth = s.input_token_growth_pct
        chain = s.max_call_chain_depth
    else:
        model_changed = bool(signals.get("model_changed", False))
        cost_z = float(signals.get("cost_z_score", 0.0))
        token_pct = signals.get("token_growth_pct")
        retry_z = float(signals.get("retry_z_score", 0.0))
        max_retry = int(signals.get("max_retry_count", 0))
        input_z = float(signals.get("input_tokens_z_score", 0.0))
        input_growth = signals.get("input_token_growth_pct")
        chain = int(signals.get("max_call_chain_depth", 0))

    def reasons_for(name: str) -> list[str]:
        r: list[str] = []
        if name == "model_routing_agent":
            if model_changed:
                r.append("model_changed == True")
            if cost_z >= 3.0 and token_pct is not None and abs(token_pct) < 50:
                r.append(f"cost_z_score={cost_z:.2f} >= 3 and |token_growth| < 50")
            if not r:
                r.append("no model-routing trigger")
        elif name == "retry_loop_agent":
            if retry_z >= 3.0:
                r.append(f"retry_z_score={retry_z:.2f} >= 3")
            if max_retry >= 3:
                r.append(f"max_retry_count={max_retry} >= 3")
            if not r:
                r.append("no retry-loop trigger")
        elif name == "token_context_agent":
            if input_z >= 3.0:
                r.append(f"input_tokens_z_score={input_z:.2f} >= 3")
            if input_growth is not None and input_growth >= 100:
                r.append(f"input_token_growth_pct={input_growth:.1f} >= 100")
            if chain >= 4:
                r.append(f"max_call_chain_depth={chain} >= 4")
            if not r:
                r.append("no token-context trigger")
        return r

    all_agents = ["model_routing_agent", "retry_loop_agent", "token_context_agent"]
    return [
        RouterCandidate(
            agent_name=name,
            selected=name in selected,
            reasons=reasons_for(name),
        )
        for name in all_agents
    ]


def _signals_from_dict(d: dict[str, Any]) -> AnomalyWindow:
    from llm_cost_investigator.schemas import AnomalySignals

    known = AnomalySignals.model_fields.keys()
    filtered = {k: v for k, v in d.items() if k in known}
    return AnomalyWindow(
        feature_tag="_",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        signals=AnomalySignals(**filtered),
    )


def selected_agents_from_candidates(candidates: list[RouterCandidate]) -> list[str]:
    return [c.agent_name for c in candidates if c.selected]


# ---------------------------------------------------------------------------
# Signal highlights
# ---------------------------------------------------------------------------

def build_highlights(signals: dict[str, Any]) -> list[SignalHighlight]:
    highlights: list[SignalHighlight] = []

    def add(key: str, threshold: str | None = None, status: str = "neutral") -> None:
        if key not in signals:
            return
        highlights.append(
            SignalHighlight(
                key=key,
                value=signals[key],
                threshold=threshold,
                status=status,  # type: ignore[arg-type]
            )
        )

    cost_z = float(signals.get("cost_z_score", 0) or 0)
    add("cost_z_score", ">= 3 anomaly", "fail" if cost_z >= 3 else "neutral")
    retry_z = float(signals.get("retry_z_score", 0) or 0)
    add("retry_z_score", ">= 3 route retry agent", "fail" if retry_z >= 3 else "neutral")
    input_z = float(signals.get("input_tokens_z_score", 0) or 0)
    add(
        "input_tokens_z_score",
        ">= 3 route token agent / < 3 may force tool",
        "fail" if input_z >= 3 else ("ambiguous" if input_z > 0 else "neutral"),
    )
    if "cost_growth_pct" in signals:
        add("cost_growth_pct", None, "fail" if (signals.get("cost_growth_pct") or 0) >= 100 else "neutral")
    if "input_token_growth_pct" in signals:
        add("input_token_growth_pct")
    if "token_growth_pct" in signals:
        add("token_growth_pct", "< 50 with cost spike → model route")
    if "max_retry_count" in signals:
        add("max_retry_count", ">= 3 route retry agent")
    if "max_call_chain_depth" in signals:
        depth = int(signals.get("max_call_chain_depth") or 0)
        add(
            "max_call_chain_depth",
            ">= 4 route / >= 2 tool gate",
            "fail" if depth >= 4 else ("ambiguous" if depth >= 2 else "neutral"),
        )
    if "repeated_parent_call_count" in signals:
        add("repeated_parent_call_count", ">= 1 with low retry_z → tool")
    if "model_changed" in signals:
        add(
            "model_changed",
            "True → route model agent",
            "fail" if signals.get("model_changed") else "neutral",
        )
    if "model_before" in signals:
        add("model_before")
    if "model_during" in signals:
        add("model_during")
    return highlights


def signals_to_dict(signals: Any) -> dict[str, Any]:
    if hasattr(signals, "model_dump"):
        return signals.model_dump(mode="json")
    return dict(signals)


def sample_calls_from_report(report: IncidentReport) -> list[CallSummary]:
    out: list[CallSummary] = []
    for c in report.anomaly_window.sample_calls:
        out.append(
            CallSummary(
                call_id=c.call_id,
                parent_call_id=c.parent_call_id,
                model=c.model,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                cost_usd=c.cost_usd,
                latency_ms=c.latency_ms,
                retry_count=c.retry_count,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Stage builders
# ---------------------------------------------------------------------------

def stage_signals(
    signals: dict[str, Any],
    sample_calls: list[CallSummary] | list[dict[str, Any]] | None = None,
) -> ReplayStage:
    calls_payload: list[dict[str, Any]] = []
    if sample_calls:
        for c in sample_calls:
            if isinstance(c, CallSummary):
                calls_payload.append(c.model_dump())
            else:
                calls_payload.append(c)
    return ReplayStage(
        id="signals",
        title="Raw signals",
        kind="deterministic",
        payload={
            "signals": signals,
            "highlights": [h.model_dump() for h in build_highlights(signals)],
            "sample_calls": calls_payload,
        },
    )


def stage_router(candidates: list[RouterCandidate]) -> ReplayStage:
    selected = selected_agents_from_candidates(candidates)
    return ReplayStage(
        id="router",
        title="Router decision",
        kind="deterministic",
        payload={
            "candidates": [c.model_dump() for c in candidates],
            "selected": selected,
            "max_agents": 2,
        },
    )


def stage_decision(
    agent_name: str,
    tool_required: bool,
    decision_line: str,
    comparisons: list[DecisionComparison],
    tool_name: str,
) -> ReplayStage:
    return ReplayStage(
        id="decision",
        title=f"DECISION — {agent_name}",
        kind="deterministic",
        agent_name=agent_name,
        payload={
            "agent_name": agent_name,
            "tool_name": tool_name,
            "tool_required": tool_required,
            "decision": "MUST_CALL" if tool_required else "DO_NOT_CALL",
            "decision_line": decision_line,
            "comparisons": [c.model_dump() for c in comparisons],
        },
    )


def stage_tool_trace(
    agent_name: str,
    tool_required: bool,
    calls: list[ToolCallRecord],
    result: list[dict[str, Any]] | None,
    tool_name: str,
) -> ReplayStage:
    summary = None
    if result:
        summary = f"{len(result)} row(s) from {tool_name}"
    elif not tool_required:
        summary = "Tool skipped — aggregate signals sufficient"
    return ReplayStage(
        id="tool_trace",
        title=f"Tool trace — {agent_name}",
        kind="deterministic",
        agent_name=agent_name,
        payload={
            "agent_name": agent_name,
            "tool_name": tool_name,
            "tool_required": tool_required,
            "calls": [c.model_dump() for c in calls],
            "result": result,
            "result_summary": summary,
        },
    )


def stage_explanation(
    agent_name: str,
    hypothesis: str,
    confidence: float,
    explanation: str,
    supporting_metrics: dict[str, Any],
    citations: list[Citation],
) -> ReplayStage:
    return ReplayStage(
        id="explanation",
        title=f"Agent explanation — {agent_name}",
        kind="agentic",
        agent_name=agent_name,
        payload={
            "agent_name": agent_name,
            "hypothesis": hypothesis,
            "confidence": confidence,
            "explanation": explanation,
            "supporting_metrics": supporting_metrics,
            "citations": [c.model_dump() for c in citations],
        },
    )


def stage_root_cause(
    hypothesis: str,
    confidence: float,
    winning_agent: str | None,
    evidence: list[EvidenceSummary],
    recommendations: list[str],
    tie_break_note: str | None = None,
) -> ReplayStage:
    return ReplayStage(
        id="root_cause",
        title="Root cause & recommendations",
        kind="agentic",
        payload={
            "hypothesis": hypothesis,
            "confidence": confidence,
            "winning_agent": winning_agent,
            "all_evidence": [e.model_dump() for e in evidence],
            "recommendations": recommendations,
            "tie_break_note": tie_break_note,
        },
    )


# ---------------------------------------------------------------------------
# Main incident export
# ---------------------------------------------------------------------------

def export_main_incident(scenario: str) -> ReplayIncident:
    path = INCIDENTS_DIR / f"{scenario}_incident.json"
    report = IncidentReport.model_validate_json(path.read_text(encoding="utf-8"))
    signals = signals_to_dict(report.anomaly_window.signals)
    candidates = router_reasons(report.anomaly_window.signals)
    selected = selected_agents_from_candidates(candidates)

    # Prefer agent_runs order; fall back to selected list.
    agent_order: list[str] = []
    for run in report.agent_runs:
        if run.evidence.agent_name not in agent_order:
            agent_order.append(run.evidence.agent_name)
    for name in selected:
        if name not in agent_order:
            agent_order.append(name)

    stages: list[ReplayStage] = [
        stage_signals(signals, sample_calls_from_report(report)),
        stage_router(candidates),
    ]

    evidence_by_agent = {
        run.evidence.agent_name: run.evidence for run in report.agent_runs
    }

    for agent_name in agent_order:
        tool_required, line, comps, tool_name = agent_decision(agent_name, signals)
        stages.append(stage_decision(agent_name, tool_required, line, comps, tool_name))
        # Main strong-signal scenarios: no live tool result — explicit skip.
        stages.append(
            stage_tool_trace(agent_name, tool_required, [], None if not tool_required else [], tool_name)
        )
        ev = evidence_by_agent.get(agent_name)
        if ev:
            stages.append(
                stage_explanation(
                    agent_name=agent_name,
                    hypothesis=ev.hypothesis,
                    confidence=ev.confidence,
                    explanation=ev.explanation,
                    supporting_metrics=ev.supporting_metrics,
                    citations=[],
                )
            )

    evidence_summaries = [
        EvidenceSummary(
            agent_name=ev.agent_name,
            hypothesis=ev.hypothesis,
            confidence=ev.confidence,
        )
        for ev in report.root_cause.evidence
    ]

    stages.append(
        stage_root_cause(
            hypothesis=report.root_cause.hypothesis,
            confidence=report.root_cause.confidence,
            winning_agent=report.root_cause.winning_agent,
            evidence=evidence_summaries,
            recommendations=list(report.recommendations),
        )
    )

    provider = None
    model = None
    if report.agent_runs:
        provider = report.agent_runs[0].provider
        model = report.agent_runs[0].model

    return ReplayIncident(
        id=scenario,
        title=TITLES[scenario],
        kind="main",
        scenario_label=scenario,
        feature_tag=report.anomaly_window.feature_tag,
        window=TimeWindow(
            start_time=report.anomaly_window.start_time.isoformat().replace("+00:00", "Z"),
            end_time=report.anomaly_window.end_time.isoformat().replace("+00:00", "Z"),
        ),
        stages=stages,
        root_cause=RootCauseBrief(
            hypothesis=report.root_cause.hypothesis,
            confidence=report.root_cause.confidence,
            winning_agent=report.root_cause.winning_agent,
        ),
        recommendations=list(report.recommendations),
        meta=ReplayMeta(
            provider=provider,
            model=model,
            generated_at=report.generated_at.isoformat().replace("+00:00", "Z"),
            source_files=[str(path.relative_to(ROOT))],
        ),
    )


# ---------------------------------------------------------------------------
# Live transcript parse
# ---------------------------------------------------------------------------

SCENARIO_HEADER = re.compile(
    r"SCENARIO:\s*(?P<id>\S+)\s*\|\s*PROVIDER:\s*(?P<provider>\S+)\s*\|\s*MODEL:\s*(?P<model>\S+)"
)


def parse_live_transcript(
    path: Path, scenario_id: str, provider: str = "groq"
) -> dict[str, Any]:
    """Parse a live transcript, preferring the given provider.

    If that provider's explanation yields no citable tool-result numbers,
    try other providers for the same scenario and pick the richest match.
    """
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?=SCENARIO:\s*\S+\s*\|\s*PROVIDER:)", text)

    parsed_blocks: list[dict[str, Any]] = []
    for block in blocks:
        m = SCENARIO_HEADER.search(block)
        if not m or m.group("id") != scenario_id:
            continue
        try:
            telemetry = _extract_json_after(block, "[Telemetry sent to agent]")
            decision_line = _extract_decision_line(block)
            tool_calls = _extract_tool_calls(block)
            tool_result = _extract_tool_result(block)
            final = _extract_final_output(block)
        except ValueError:
            continue
        parsed_blocks.append(
            {
                "provider": m.group("provider"),
                "model": m.group("model"),
                "telemetry": telemetry,
                "decision_line": decision_line,
                "tool_calls": tool_calls,
                "tool_result": tool_result,
                "final": final,
            }
        )

    if not parsed_blocks:
        raise ValueError(f"No block for scenario={scenario_id} in {path.name}")

    def score(p: dict[str, Any]) -> tuple[int, int]:
        exp = (p.get("final") or {}).get("explanation") or ""
        tool_name = "get_call_chain"
        calls = p.get("tool_calls") or []
        if calls:
            tool_name = calls[0].name
        n_cite = len(find_citations(exp, p.get("tool_result"), tool_name))
        prefer = 1 if p.get("provider") == provider else 0
        return (n_cite, prefer)

    parsed_blocks.sort(key=score, reverse=True)
    return parsed_blocks[0]


def _extract_json_after(block: str, marker: str) -> Any:
    idx = block.find(marker)
    if idx < 0:
        raise ValueError(f"Missing marker: {marker}")
    rest = block[idx + len(marker) :]
    # Find first { or [
    start_obj = rest.find("{")
    start_arr = rest.find("[")
    if start_obj < 0 and start_arr < 0:
        raise ValueError(f"No JSON after {marker}")
    if start_obj < 0:
        start = start_arr
    elif start_arr < 0:
        start = start_obj
    else:
        start = min(start_obj, start_arr)
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(rest[start:])
    return obj


def _extract_decision_line(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("DECISION:"):
            return stripped
    raise ValueError("No DECISION line in block")


def _extract_tool_calls(block: str) -> list[ToolCallRecord]:
    marker = "[Tool calls log]"
    idx = block.find(marker)
    if idx < 0:
        return []
    rest = block[idx + len(marker) :]
    # until next section header
    end = rest.find("\n[")
    section = rest if end < 0 else rest[:end]
    calls: list[ToolCallRecord] = []
    for line in section.splitlines():
        line = line.strip()
        if not line or line == "(none)":
            continue
        m = re.match(r"(\w+)\((.*)\)\s*$", line)
        if not m:
            continue
        name = m.group(1)
        args_raw = m.group(2)
        args: dict[str, Any] = {}
        for part in re.finditer(r"(\w+)=('([^']*)'|\"([^\"]*)\"|([^,\s]+))", args_raw):
            key = part.group(1)
            val = part.group(3) or part.group(4) or part.group(5)
            args[key] = val
        calls.append(ToolCallRecord(name=name, args=args))
    return calls


def _extract_tool_result(block: str) -> list[dict[str, Any]] | None:
    marker = "[Tool result returned to model]"
    if marker not in block:
        return None
    try:
        obj = _extract_json_after(block, marker)
    except ValueError:
        return None
    if isinstance(obj, list):
        return obj
    return [obj]


def _extract_final_output(block: str) -> dict[str, Any]:
    marker = "[Final agent output]"
    idx = block.find(marker)
    if idx < 0:
        raise ValueError("Missing final agent output")
    rest = block[idx + len(marker) :]
    end = rest.find("\n====")
    if end < 0:
        end = rest.find("\nSCENARIO:")
    section = rest if end < 0 else rest[:end]

    result: dict[str, Any] = {}
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*(fallback_used|hypothesis|confidence|explanation|metrics):\s*(.*)", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "metrics":
            # metrics may be inline JSON starting on this line
            blob = val
            if not blob.startswith("{"):
                # next lines
                j = i + 1
                parts = []
                while j < len(lines):
                    parts.append(lines[j])
                    j += 1
                    if "".join(parts).count("{") and "".join(parts).count("{") == "".join(parts).count("}"):
                        break
                blob = "\n".join(parts)
            try:
                result["metrics"] = json.loads(blob)
            except json.JSONDecodeError:
                # try to find raw JSON in the remainder of section
                start = section.find("{", section.find("metrics:"))
                if start >= 0:
                    decoder = json.JSONDecoder()
                    result["metrics"], _ = decoder.raw_decode(section[start:])
                else:
                    result["metrics"] = {}
        elif key == "confidence":
            result["confidence"] = float(val)
        elif key == "fallback_used":
            result["fallback_used"] = val.lower() in ("true", "1", "yes")
        else:
            result[key] = val
        i += 1
    return result


# ---------------------------------------------------------------------------
# Thin scenario export
# ---------------------------------------------------------------------------

def export_thin_from_live(
    *,
    incident_id: str,
    transcript_path: Path,
    agent_name: str,
    provider: str,
) -> ReplayIncident:
    parsed = parse_live_transcript(transcript_path, incident_id, provider=provider)
    telemetry = parsed["telemetry"]
    signals = dict(telemetry.get("signals", {}))
    feature_tag = telemetry.get("feature_tag", "unknown")
    start = telemetry.get("start_time", "2026-07-09T00:00:00Z")
    end = telemetry.get("end_time", start)

    # Router for thin demos: force-select the agent under test, still show rules.
    window = _signals_from_dict(signals)
    window.feature_tag = feature_tag
    candidates = router_reasons(window.signals)
    # Ensure the demo agent appears selected for the story
    for c in candidates:
        if c.agent_name == agent_name:
            c.selected = True
            if "thin demo: forced diagnostic path" not in c.reasons:
                c.reasons.append("thin demo: forced diagnostic path")

    tool_required, gate_line, comps, tool_name = agent_decision(agent_name, signals)
    # Prefer transcript DECISION line when present (matches real injection).
    decision_line = parsed.get("decision_line") or gate_line

    tool_calls: list[ToolCallRecord] = parsed["tool_calls"]
    tool_result = parsed["tool_result"]
    final = parsed["final"]

    explanation = final.get("explanation") or ""
    hypothesis = final.get("hypothesis") or "no_strong_signal"
    confidence = float(final.get("confidence") or 0.0)
    metrics = final.get("metrics") or {}

    citations = find_citations(explanation, tool_result, tool_name)

    sample_calls: list[dict[str, Any]] = []
    for cid in telemetry.get("available_call_ids") or []:
        sample_calls.append({"call_id": cid})

    stages = [
        stage_signals(signals, sample_calls),
        stage_router(candidates),
        stage_decision(agent_name, tool_required, decision_line, comps, tool_name),
        stage_tool_trace(agent_name, tool_required, tool_calls, tool_result, tool_name),
        stage_explanation(
            agent_name=agent_name,
            hypothesis=hypothesis,
            confidence=confidence,
            explanation=explanation,
            supporting_metrics=metrics,
            citations=citations,
        ),
        stage_root_cause(
            hypothesis=hypothesis,
            confidence=confidence,
            winning_agent=agent_name,
            evidence=[
                EvidenceSummary(
                    agent_name=agent_name,
                    hypothesis=hypothesis,
                    confidence=confidence,
                )
            ],
            recommendations=_thin_recommendations(agent_name),
            tie_break_note="Thin synthetic scenario — single agent path.",
        ),
    ]

    return ReplayIncident(
        id=incident_id,
        title=TITLES[incident_id],
        kind="thin_must_call",
        scenario_label=incident_id,
        feature_tag=feature_tag,
        window=TimeWindow(start_time=start, end_time=end),
        stages=stages,
        root_cause=RootCauseBrief(
            hypothesis=hypothesis,
            confidence=confidence,
            winning_agent=agent_name,
        ),
        recommendations=_thin_recommendations(agent_name),
        meta=ReplayMeta(
            provider=parsed.get("provider"),
            model=parsed.get("model"),
            generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            source_files=[str(transcript_path.relative_to(ROOT))],
        ),
    )


def _thin_recommendations(agent_name: str) -> list[str]:
    if agent_name == "token_context_agent":
        return [
            "Cap recursive self-calls with a max reflection depth.",
            "Summarize prior context before re-invoking the agent.",
            "Alert when call-chain depth grows with doubling input tokens.",
        ]
    if agent_name == "model_routing_agent":
        return [
            "Pin production features to an approved model allow-list.",
            "Add a cost-delta gate before accepting model upgrades.",
            "Alert when model_changed is true and per-call cost jumps >5x.",
        ]
    if agent_name == "retry_loop_agent":
        return [
            "Implement exponential backoff with jitter.",
            "Cap the maximum retry count in the client configuration.",
            "Add a circuit breaker for repeated parent failures.",
        ]
    return []


def export_retry_loop_thin() -> ReplayIncident:
    """Synthetic thin scenario (no durable live transcript checked in)."""
    signals = {
        "retry_z_score": 1.2,
        "max_retry_count": 3,
        "avg_retry_count": 1.5,
        "repeated_parent_call_count": 3,
        "latency_z_score": 2.0,
        "cost_z_score": 3.5,
        "cost_growth_pct": 180.0,
        "input_tokens_z_score": 0.3,
        "input_token_growth_pct": 5.0,
        "token_growth_pct": 5.0,
        "max_call_chain_depth": 1,
        "model_changed": False,
    }
    tool_required, line, comps, tool_name = agent_decision("retry_loop_agent", signals)
    tool_result = [
        {
            "call_id": f"chain_{i:02d}",
            "parent_call_id": None if i == 0 else "synthetic_parent_01",
            "timestamp": "2026-07-09T00:00:00Z",
            "retry_count": i,
            "cost_usd": 0.003 * (i + 1),
            "latency_ms": 900 + i * 450,
        }
        for i in range(4)
    ]
    tool_calls = [
        ToolCallRecord(name="get_call_chain", args={"call_id": "retry_loop_thin_call_00"})
    ]
    explanation = (
        "The call chain shows the same parent spawning repeated child attempts with "
        "retry_count rising 0 → 3 and per-call cost climbing from 0.003 to 0.012 USD. "
        "Although retry_z_score is only 1.2, the repeated parent pattern and latency "
        "growth (900 → 2250 ms) indicate an uncapped retry loop."
    )
    hypothesis = "uncapped_retry_loop"
    confidence = 0.72
    metrics = {
        "retry_z_score": 1.2,
        "max_retry_count": 3,
        "repeated_parent_call_count": 3,
        "observed_max_retry_in_chain": 3,
        "observed_cost_usd": [0.003, 0.006, 0.009, 0.012],
    }
    citations = find_citations(explanation, tool_result, tool_name)

    window = _signals_from_dict(signals)
    window.feature_tag = "support_reply"
    candidates = router_reasons(window.signals)
    for c in candidates:
        if c.agent_name == "retry_loop_agent":
            c.selected = True
            c.reasons.append("thin demo: forced diagnostic path")

    stages = [
        stage_signals(
            signals,
            [
                {"call_id": f"retry_loop_thin_call_{i:02d}"}
                for i in range(4)
            ],
        ),
        stage_router(candidates),
        stage_decision("retry_loop_agent", tool_required, line, comps, tool_name),
        stage_tool_trace("retry_loop_agent", tool_required, tool_calls, tool_result, tool_name),
        stage_explanation(
            "retry_loop_agent",
            hypothesis,
            confidence,
            explanation,
            metrics,
            citations,
        ),
        stage_root_cause(
            hypothesis,
            confidence,
            "retry_loop_agent",
            [
                EvidenceSummary(
                    agent_name="retry_loop_agent",
                    hypothesis=hypothesis,
                    confidence=confidence,
                )
            ],
            _thin_recommendations("retry_loop_agent"),
            tie_break_note="Thin synthetic scenario — single agent path.",
        ),
    ]

    return ReplayIncident(
        id="retry_loop_thin",
        title=TITLES["retry_loop_thin"],
        kind="thin_must_call",
        scenario_label="retry_loop_thin",
        feature_tag="support_reply",
        window=TimeWindow(
            start_time="2026-07-09T00:00:00Z",
            end_time="2026-07-09T00:00:00Z",
        ),
        stages=stages,
        root_cause=RootCauseBrief(
            hypothesis=hypothesis,
            confidence=confidence,
            winning_agent="retry_loop_agent",
        ),
        recommendations=_thin_recommendations("retry_loop_agent"),
        meta=ReplayMeta(
            provider="synthetic",
            model=None,
            generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            source_files=["scripts/run_tool_use_live.py"],
        ),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_incident(incident: ReplayIncident) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{incident.id}.json"
    path.write_text(
        incident.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export replay catalog JSON")
    parser.add_argument(
        "--provider",
        default="groq",
        choices=["groq", "cerebras"],
        help="Preferred provider block from live transcripts",
    )
    args = parser.parse_args()

    written: list[Path] = []

    for scenario in MAIN_SCENARIOS:
        incident = export_main_incident(scenario)
        written.append(write_incident(incident))
        print(f"wrote {written[-1].relative_to(ROOT)}  stages={len(incident.stages)}")

    thin_specs = [
        (
            "token_context_thin",
            TRANSCRIPTS_DIR / "live_token_context_tool_use.txt",
            "token_context_agent",
        ),
        (
            "model_routing_thin",
            TRANSCRIPTS_DIR / "live_model_routing_tool_use.txt",
            "model_routing_agent",
        ),
    ]
    for incident_id, path, agent in thin_specs:
        if not path.exists():
            print(f"SKIP {incident_id}: missing {path}")
            continue
        incident = export_thin_from_live(
            incident_id=incident_id,
            transcript_path=path,
            agent_name=agent,
            provider=args.provider,
        )
        written.append(write_incident(incident))
        n_cite = 0
        for st in incident.stages:
            if st.id == "explanation":
                n_cite = len(st.payload.get("citations") or [])
        print(
            f"wrote {written[-1].relative_to(ROOT)}  stages={len(incident.stages)}  citations={n_cite}"
        )

    thin_retry = export_retry_loop_thin()
    written.append(write_incident(thin_retry))
    n_cite = 0
    for st in thin_retry.stages:
        if st.id == "explanation":
            n_cite = len(st.payload.get("citations") or [])
    print(
        f"wrote {written[-1].relative_to(ROOT)}  stages={len(thin_retry.stages)}  citations={n_cite}"
    )

    print(f"\nExported {len(written)} incident(s) → {OUT_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
