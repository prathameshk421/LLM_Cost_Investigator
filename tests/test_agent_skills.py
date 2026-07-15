"""Unit tests for hard skills, KB matching, graph summary, aggregator caps."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_cost_investigator.agent_skills import (
    score_all_agents,
    score_model_routing,
    score_retry_loop,
    score_token_context,
)
from llm_cost_investigator.aggregator import (
    apply_cross_agent_adjustments,
    select_root_cause,
)
from llm_cost_investigator.detector import detect_anomalies
from llm_cost_investigator.evidence_graph import build_graph, summarize_graph
from llm_cost_investigator.knowledge_base import (
    match_cross_agent,
    match_patterns,
    primary_cross_pattern,
)
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


def test_cross_demote_model_routing_when_token_growth_pass() -> None:
    """Strong token growth pass demotes expensive_model_misroute to ≤0.74."""
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    w = AnomalyWindow(
        feature_tag="f",
        start_time=now,
        end_time=now + timedelta(hours=1),
        signals=AnomalySignals(
            model_changed=True,
            model_before="gpt-4o-mini",
            model_during="gpt-4.1",
            cost_growth_pct=300,
            token_growth_pct=20,
            cost_z_score=6,
            input_token_growth_pct=400,
            input_tokens_z_score=5,
            max_call_chain_depth=6,
        ),
        sample_calls=[],
    )
    packs = score_all_agents(w)
    # Ensure token growth threshold is pass for demote rule
    assert packs["token_context_agent"]["skills"]["token_growth_threshold"]["verdict"] == "pass"

    evidence = [
        AgentEvidence(
            agent_name="model_routing_agent",
            hypothesis="expensive_model_misroute",
            confidence=0.95,
            supporting_metrics={"skill_band_ceiling": 1.0},
            explanation="routing",
        )
    ]
    result = select_root_cause(evidence, skill_packs=packs)
    assert result.hypothesis == "expensive_model_misroute"
    assert result.confidence <= 0.74
    demoted = next(e for e in result.evidence if e.agent_name == "model_routing_agent")
    assert demoted.supporting_metrics.get("cross_demoted") is True


def test_demo_scenarios_still_high_confidence() -> None:
    from llm_cost_investigator.agents import (
        fallback_model_routing,
        fallback_retry_loop,
        fallback_token_context,
    )
    from llm_cost_investigator.router import route_agents

    for scenario in ("retry_loop", "context_bloat", "model_misroute"):
        calls = generate_scenario_calls(scenario)
        w = detect_anomalies(calls)[0]
        packs = score_all_agents(w)
        agents = route_agents(w)
        evidence = []
        for a in agents:
            if a == "retry_loop_agent":
                evidence.append(fallback_retry_loop(w))
            elif a == "token_context_agent":
                evidence.append(fallback_token_context(w))
            else:
                evidence.append(fallback_model_routing(w))
        result = select_root_cause(evidence, skill_packs=packs)
        assert result.hypothesis != "no_strong_signal", scenario
        assert result.confidence >= 0.70, (scenario, result.confidence)


def test_primary_cross_prefers_x03() -> None:
    matches = match_cross_agent(
        {
            "retry_loop_agent": {
                "suggested_hypothesis": "no_strong_signal",
                "forced_no_strong_signal": True,
                "band_ceiling": 0.49,
                "skills": {
                    "sample_call_consistency": {
                        "verdict": "fail",
                        "falsifier": True,
                    }
                },
            },
            "token_context_agent": {
                "suggested_hypothesis": "no_strong_signal",
                "forced_no_strong_signal": True,
                "band_ceiling": 0.49,
                "skills": {},
            },
            "model_routing_agent": {
                "suggested_hypothesis": "no_strong_signal",
                "forced_no_strong_signal": True,
                "band_ceiling": 0.49,
                "skills": {},
            },
        }
    )
    primary = primary_cross_pattern(matches)
    assert primary is not None
    assert primary["pattern_id"] == "X-03"


def test_x02_when_all_weak() -> None:
    packs = {
        "retry_loop_agent": {
            "suggested_hypothesis": "no_strong_signal",
            "forced_no_strong_signal": False,
            "band_ceiling": 0.49,
            "skills": {},
        },
        "token_context_agent": {
            "suggested_hypothesis": "no_strong_signal",
            "forced_no_strong_signal": False,
            "band_ceiling": 0.49,
            "skills": {},
        },
        "model_routing_agent": {
            "suggested_hypothesis": "no_strong_signal",
            "forced_no_strong_signal": False,
            "band_ceiling": 0.49,
            "skills": {},
        },
    }
    evidence = [
        AgentEvidence(
            agent_name="retry_loop_agent",
            hypothesis="no_strong_signal",
            confidence=0.2,
            supporting_metrics={},
            explanation="weak",
        )
    ]
    result = select_root_cause(evidence, skill_packs=packs)
    assert result.hypothesis == "no_strong_signal"
    assert result.attribution_pattern == "X-02"
    assert result.attribution_note


def test_x03_attribution_on_select() -> None:
    packs = {
        "retry_loop_agent": {
            "suggested_hypothesis": "no_strong_signal",
            "forced_no_strong_signal": True,
            "band_ceiling": 0.49,
            "skills": {
                "sample_call_consistency": {"verdict": "fail", "falsifier": True},
            },
        },
        "token_context_agent": {
            "suggested_hypothesis": "no_strong_signal",
            "forced_no_strong_signal": True,
            "band_ceiling": 0.49,
            "skills": {},
        },
        "model_routing_agent": {
            "suggested_hypothesis": "no_strong_signal",
            "forced_no_strong_signal": True,
            "band_ceiling": 0.49,
            "skills": {},
        },
    }
    evidence = [
        AgentEvidence(
            agent_name="retry_loop_agent",
            hypothesis="no_strong_signal",
            confidence=0.1,
            supporting_metrics={},
            explanation="bad samples",
        )
    ]
    result = select_root_cause(evidence, skill_packs=packs)
    assert result.attribution_pattern == "X-03"


def test_select_without_packs_still_works() -> None:
    evidence = [
        AgentEvidence(
            agent_name="model_routing_agent",
            hypothesis="expensive_model_misroute",
            confidence=0.9,
            supporting_metrics={"skill_band_ceiling": 0.9},
            explanation="ok",
        )
    ]
    result = select_root_cause(evidence)
    assert result.hypothesis == "expensive_model_misroute"
    assert result.confidence == 0.9
    assert result.attribution_pattern is None


def test_weak_band_forces_no_strong_signal() -> None:
    """LLM inventing a cause below skill ceiling 0.50 must be forced no_strong."""
    from llm_cost_investigator.agents import _apply_skill_pack_to_evidence

    pack = {
        "band_ceiling": 0.49,
        "forced_no_strong_signal": False,
        "skills": {},
        "metrics": {},
        "suggested_hypothesis": "no_strong_signal",
        "suggested_confidence": 0.30,
    }
    ev = AgentEvidence(
        agent_name="retry_loop_agent",
        hypothesis="uncapped_retry_loop",
        confidence=0.45,
        supporting_metrics={},
        explanation="llm invents",
    )
    capped = _apply_skill_pack_to_evidence(ev, pack, [])
    assert capped.hypothesis == "no_strong_signal"
    assert capped.confidence <= 0.49
    result = select_root_cause([capped])
    assert result.hypothesis == "no_strong_signal"


def test_pattern_band_ceiling_forces_no_signal_mixed() -> None:
    from llm_cost_investigator.knowledge_base import pattern_band_ceiling

    matches = [
        {"pattern_id": "TC-01", "band_high": 1.0, "forces_no_signal": False},
        {"pattern_id": "X-03", "band_high": 0.49, "forces_no_signal": True},
    ]
    assert pattern_band_ceiling(matches, 1.0) <= 0.49


def test_pricing_longest_prefix_match() -> None:
    from llm_cost_investigator.ingest.pricing import lookup_rates

    _, _, key_mini = lookup_rates("gpt-4o-mini-2024-07-18")
    assert key_mini == "gpt-4o-mini"
    _, _, key_o1m = lookup_rates("o1-mini-2024")
    assert key_o1m == "o1-mini"
    _, _, key_4o = lookup_rates("gpt-4o-2024-08-06")
    assert key_4o == "gpt-4o"


def test_none_token_growth_not_stable() -> None:
    """Unknown token growth must not score as 'tokens stable' pass."""
    w = _window(
        signals=AnomalySignals(
            model_changed=True,
            model_before="gpt-4o-mini",
            model_during="gpt-4.1",
            cost_growth_pct=250,
            token_growth_pct=None,
            cost_z_score=5.0,
        )
    )
    pack = score_model_routing(w)
    s3 = pack["skills"]["token_stability_check"]
    assert s3["verdict"] != "pass"
    assert "unknown" in (s3.get("detail") or "").lower() or s3["verdict"] == "soft_pass"
    # Must not hit pure 0.95 band that requires token_stability pass
    if pack["suggested_hypothesis"] == "expensive_model_misroute":
        assert pack["suggested_confidence"] < 0.95


def test_naive_sample_timestamps_do_not_crash() -> None:
    """Aware window + naive samples must not TypeError in score_all_agents."""
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    samples = [
        LLMCall(
            timestamp=datetime(2026, 7, 1, 12, 30),  # naive
            call_id="n1",
            feature_tag="f",
            model="m",
            input_tokens=500,
            output_tokens=10,
            cost_usd=0.05,
            latency_ms=50,
            retry_count=0,
        )
    ]
    w = AnomalyWindow(
        feature_tag="f",
        start_time=now,
        end_time=now + timedelta(hours=1),
        signals=AnomalySignals(
            input_token_growth_pct=400,
            max_call_chain_depth=5,
            input_tokens_z_score=4,
            cost_z_score=3,
        ),
        sample_calls=samples,
    )
    packs = score_all_agents(w)
    assert "token_context_agent" in packs


def test_late_retry_storm_samples_include_retries() -> None:
    """Representative sampling must not drop late high-retry calls."""
    from llm_cost_investigator.detector import detect_anomalies

    base = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    calls: list[LLMCall] = []
    for wi in range(4):
        for j in range(3):
            t = base + timedelta(minutes=5 * wi, seconds=j * 10)
            calls.append(
                LLMCall(
                    timestamp=t,
                    call_id=f"b{wi}_{j}",
                    feature_tag="feat",
                    model="gpt-4o-mini",
                    input_tokens=100,
                    output_tokens=20,
                    cost_usd=0.001,
                    latency_ms=100,
                    retry_count=0,
                )
            )
    aw = base + timedelta(minutes=20)
    for j in range(20):
        t = aw + timedelta(seconds=j * 5)
        rc = 6 if j >= 15 else 0
        calls.append(
            LLMCall(
                timestamp=t,
                call_id=f"a{j}",
                feature_tag="feat",
                parent_call_id="p1" if j >= 15 else None,
                model="gpt-4o-mini",
                input_tokens=100,
                output_tokens=20,
                cost_usd=0.05 if rc else 0.001,
                latency_ms=800 if rc else 100,
                retry_count=rc,
            )
        )
    anoms = detect_anomalies(calls)
    assert anoms
    a = anoms[0]
    assert a.signals.max_retry_count >= 5
    assert max(c.retry_count for c in a.sample_calls) >= 1
    pack = score_retry_loop(a)
    assert pack["forced_no_strong_signal"] is False
    assert pack["suggested_hypothesis"] == "uncapped_retry_loop"


def test_retry_v2_prompt_bad_json_no_crash() -> None:
    from llm_cost_investigator.agents import build_retry_loop_prompt_v2

    out = build_retry_loop_prompt_v2("NOT JSON")
    assert "DECISION:" in out
    assert "could not parse" in out.lower() or "DO NOT call" in out


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
        test_cross_demote_model_routing_when_token_growth_pass,
        test_demo_scenarios_still_high_confidence,
        test_primary_cross_prefers_x03,
        test_x02_when_all_weak,
        test_x03_attribution_on_select,
        test_select_without_packs_still_works,
        test_weak_band_forces_no_strong_signal,
        test_pattern_band_ceiling_forces_no_signal_mixed,
        test_pricing_longest_prefix_match,
        test_none_token_growth_not_stable,
        test_naive_sample_timestamps_do_not_crash,
        test_late_retry_storm_samples_include_retries,
        test_retry_v2_prompt_bad_json_no_crash,
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
