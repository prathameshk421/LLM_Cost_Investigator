"""Build ReplayIncident stages from IncidentReport (shared by export + live API)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.models.replay import (
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
from llm_cost_investigator.agents import (
    _model_routing_tool_required,
    _token_context_tool_required,
)
from llm_cost_investigator.router import route_agents
from llm_cost_investigator.schemas import AnomalySignals, AnomalyWindow, IncidentReport

AGENT_TOOL = {
    "retry_loop_agent": "get_call_chain",
    "token_context_agent": "get_call_chain",
    "model_routing_agent": "get_window_calls",
}


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------


def retry_loop_tool_required(
    signals: dict[str, Any],
) -> tuple[bool, str, list[DecisionComparison]]:
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


def token_context_decision(
    signals: dict[str, Any],
) -> tuple[bool, str, list[DecisionComparison]]:
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


def model_routing_decision(
    signals: dict[str, Any],
) -> tuple[bool, str, list[DecisionComparison]]:
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
# Router / signals helpers
# ---------------------------------------------------------------------------


def _signals_from_dict(d: dict[str, Any]) -> AnomalyWindow:
    known = AnomalySignals.model_fields.keys()
    filtered = {k: v for k, v in d.items() if k in known}
    return AnomalyWindow(
        feature_tag="_",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        signals=AnomalySignals(**filtered),
    )


def router_reasons(signals: Any) -> list[RouterCandidate]:
    if hasattr(signals, "model_dump"):
        window = AnomalyWindow(
            feature_tag="_",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            signals=signals,
        )
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
        window = _signals_from_dict(signals)
        model_changed = bool(signals.get("model_changed", False))
        cost_z = float(signals.get("cost_z_score", 0.0))
        token_pct = signals.get("token_growth_pct")
        retry_z = float(signals.get("retry_z_score", 0.0))
        max_retry = int(signals.get("max_retry_count", 0))
        input_z = float(signals.get("input_tokens_z_score", 0.0))
        input_growth = signals.get("input_token_growth_pct")
        chain = int(signals.get("max_call_chain_depth", 0))

    selected = set(route_agents(window))

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


def selected_agents_from_candidates(candidates: list[RouterCandidate]) -> list[str]:
    return [c.agent_name for c in candidates if c.selected]


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
        add(
            "cost_growth_pct",
            None,
            "fail" if (signals.get("cost_growth_pct") or 0) >= 100 else "neutral",
        )
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
# Stage constructors
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
    *,
    captured: bool = True,
) -> ReplayStage:
    summary = None
    if not captured:
        summary = "Tool trace not captured in one-shot investigate"
    elif result:
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
            "captured": captured,
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
    attribution_pattern: str | None = None,
    attribution_note: str | None = None,
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
            "attribution_pattern": attribution_pattern,
            "attribution_note": attribution_note,
        },
    )


# ---------------------------------------------------------------------------
# IncidentReport → ReplayIncident
# ---------------------------------------------------------------------------


def build_stages_from_report(
    report: IncidentReport,
    *,
    include_tool_trace: bool = True,
    tool_trace_captured: bool = False,
) -> list[ReplayStage]:
    """Build stage list from a completed IncidentReport."""
    signals = signals_to_dict(report.anomaly_window.signals)
    candidates = router_reasons(report.anomaly_window.signals)
    selected = selected_agents_from_candidates(candidates)

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
        if include_tool_trace:
            stages.append(
                stage_tool_trace(
                    agent_name,
                    tool_required,
                    [],
                    None if not tool_required else [],
                    tool_name,
                    captured=tool_trace_captured,
                )
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
            attribution_pattern=report.root_cause.attribution_pattern,
            attribution_note=report.root_cause.attribution_note,
        )
    )
    return stages


def incident_report_to_replay(
    report: IncidentReport,
    *,
    incident_id: str | None = None,
    title: str | None = None,
    kind: str = "main",
    source_files: list[str] | None = None,
) -> ReplayIncident:
    """Convert core IncidentReport into a ReplayIncident for the UI."""
    rid = incident_id or f"live_{report.scenario}"
    stages = build_stages_from_report(
        report, include_tool_trace=True, tool_trace_captured=False
    )
    provider = None
    model = None
    if report.agent_runs:
        provider = report.agent_runs[0].provider
        model = report.agent_runs[0].model

    def _iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    return ReplayIncident(
        id=rid,
        title=title or f"Investigate — {report.anomaly_window.feature_tag}",
        kind=kind,  # type: ignore[arg-type]
        scenario_label=report.scenario,
        feature_tag=report.anomaly_window.feature_tag,
        window=TimeWindow(
            start_time=_iso(report.anomaly_window.start_time),
            end_time=_iso(report.anomaly_window.end_time),
        ),
        stages=stages,
        root_cause=RootCauseBrief(
            hypothesis=report.root_cause.hypothesis,
            confidence=report.root_cause.confidence,
            winning_agent=report.root_cause.winning_agent,
            attribution_pattern=report.root_cause.attribution_pattern,
            attribution_note=report.root_cause.attribution_note,
        ),
        recommendations=list(report.recommendations),
        meta=ReplayMeta(
            provider=provider,
            model=model,
            generated_at=_iso(report.generated_at),
            source_files=list(source_files or []),
        ),
    )
