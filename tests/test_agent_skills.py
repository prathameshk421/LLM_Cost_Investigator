"""Unit tests for hard skills, KB matching, graph summary, aggregator caps."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_cost_investigator.agent_skills import (
    score_model_routing,
    score_retry_loop,
    score_token_context,
)
from llm_cost_investigator.aggregator import select_root_cause
from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.evidence_graph import build_graph, summarize_graph
from llm_cost_investigator.knowledge_base import match_patterns
from llm_cost_investigator.schemas import AgentEvidence, AnomalySignals, AnomalyWindow, LLMCall
from llm_cost_investigator.simulate_telemetry import generate_scenario_calls


def _window(
    *,
    signals: AnomalySignals,
    samples: list[LLMCall] | None = None,
) -> AnomalyWindow:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    return AnomalyWindow(
        feature_tag="test_feature",
        start_time=now,
        end_time=now + timedelta(hours=1),
        signals=signals,
        sample_calls=samples or [],
    )


def test_demo_retry_loop_skills() -> None:
    calls = generate_scenario_calls("retry_loop")
    w = detect_anomalies(calls)[0]
    pack = score_retry_loop(w)
    assert pack["suggested_hypothesis"] == "uncapped_retry_loop"
    assert pack["suggested_confidence"] >= 0.70
    assert not pack["forced_no_strong_signal"]
    kb = match_patterns("retry_loop_agent", w, pack)
    assert any(m["pattern_id"].startswith("RL-") for m in kb)


def test_demo_context_bloat_skills() -> None:
    calls = generate_scenario_calls("context_bloat")
    w = detect_anomalies(calls)[0]
    pack = score_token_context(w)
    assert pack["suggested_hypothesis"] == "context_bloat_self_calling_agent"
    assert pack["suggested_confidence"] >= 0.70


def test_demo_model_misroute_skills() -> None:
    calls = generate_scenario_calls("model_misroute")
    w = detect_anomalies(calls)[0]
    pack = score_model_routing(w)
    assert pack["suggested_hypothesis"] == "expensive_model_misroute"
    assert pack["suggested_confidence"] >= 0.70
    assert pack["skills"]["model_changed_flag"]["verdict"] == "pass"


def test_falsifier_model_unchanged() -> None:
    w = _window(
        signals=AnomalySignals(
            model_changed=False,
            cost_growth_pct=500,
            token_growth_pct=10,
            cost_z_score=10,
        )
    )
    pack = score_model_routing(w)
    assert pack["forced_no_strong_signal"] is True
    assert pack["suggested_hypothesis"] == "no_strong_signal"


def test_retry_sample_consistency_falsifier() -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    samples = [
        LLMCall(
            timestamp=now,
            call_id=f"c{i}",
            feature_tag="f",
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=10,
            cost_usd=0.01,
            latency_ms=100,
            retry_count=0,
        )
        for i in range(5)
    ]
    w = _window(
        signals=AnomalySignals(
            retry_z_score=6,
            max_retry_count=8,
            avg_retry_count=4,
            repeated_parent_call_count=5,
            cost_z_score=5,
            cost_growth_pct=200,
        ),
        samples=samples,
    )
    pack = score_retry_loop(w)
    assert pack["skills"]["sample_call_consistency"]["verdict"] == "fail"
    assert pack["forced_no_strong_signal"] is True


def test_evidence_graph_summary() -> None:
    calls = generate_scenario_calls("model_misroute")
    w = detect_anomalies(calls)[0]
    from llm_cost_investigator.agent_skills import score_all_agents

    packs = score_all_agents(w)
    summary = summarize_graph(build_graph(w, packs))
    assert "H:expensive_model_misroute" in summary
    assert "explains:" in summary


def test_aggregator_caps_confidence() -> None:
    evidence = [
        AgentEvidence(
            agent_name="model_routing_agent",
            hypothesis="expensive_model_misroute",
            confidence=0.99,
            supporting_metrics={"skill_band_ceiling": 0.74},
            explanation="overconfident llm",
        ),
        AgentEvidence(
            agent_name="retry_loop_agent",
            hypothesis="no_strong_signal",
            confidence=0.2,
            supporting_metrics={},
            explanation="none",
        ),
    ]
    result = select_root_cause(evidence)
    assert result.hypothesis == "expensive_model_misroute"
    assert result.confidence == 0.74


def test_aggregator_epsilon_tie_break() -> None:
    evidence = [
        AgentEvidence(
            agent_name="retry_loop_agent",
            hypothesis="uncapped_retry_loop",
            confidence=0.85,
            supporting_metrics={"skill_band_ceiling": 0.85},
            explanation="retry",
        ),
        AgentEvidence(
            agent_name="model_routing_agent",
            hypothesis="expensive_model_misroute",
            confidence=0.84,
            supporting_metrics={"skill_band_ceiling": 0.84},
            explanation="route",
        ),
    ]
    result = select_root_cause(evidence)
    # Within 0.05 → prefer expensive_model_misroute
    assert result.hypothesis == "expensive_model_misroute"


def main() -> int:
    tests = [
        test_demo_retry_loop_skills,
        test_demo_context_bloat_skills,
        test_demo_model_misroute_skills,
        test_falsifier_model_unchanged,
        test_retry_sample_consistency_falsifier,
        test_evidence_graph_summary,
        test_aggregator_caps_confidence,
        test_aggregator_epsilon_tie_break,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"{fn.__name__} ... PASS")
        except Exception as exc:
            failed += 1
            print(f"{fn.__name__} ... FAIL: {exc}")
    print()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
