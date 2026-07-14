"""Deterministic hard skills for diagnostic agents.

Numeric thresholds are resolved here — never left to the LLM.
"""

from __future__ import annotations

from typing import Any, Literal

from llm_cost_investigator.ingest.pricing import lookup_rates
from llm_cost_investigator.schemas import AnomalyWindow, LLMCall

SkillVerdict = Literal["pass", "soft_pass", "fail"]

__all__ = [
    "SkillVerdict",
    "score_retry_loop",
    "score_token_context",
    "score_model_routing",
    "score_all_agents",
    "forced_no_strong_signal",
    "skill_band_ceiling",
]


def _skill(
    verdict: SkillVerdict,
    *,
    value: Any = None,
    detail: str = "",
    falsifier: bool = False,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "value": value,
        "detail": detail,
        "falsifier": falsifier,
    }


def forced_no_strong_signal(skill_pack: dict[str, Any]) -> bool:
    return bool(skill_pack.get("forced_no_strong_signal"))


def skill_band_ceiling(skill_pack: dict[str, Any]) -> float:
    return float(skill_pack.get("band_ceiling", 1.0))


def _any_falsifier_fail(skills: dict[str, dict[str, Any]]) -> bool:
    for s in skills.values():
        if s.get("falsifier") and s.get("verdict") == "fail":
            return True
    return False


def _retry_cost_attribution(samples: list[LLMCall]) -> tuple[float | None, str]:
    if not samples:
        return None, "no samples"
    total = sum(c.cost_usd for c in samples)
    if total <= 0:
        return None, "zero sample cost"
    retried = sum(c.cost_usd for c in samples if c.retry_count > 0)
    return 100.0 * retried / total, f"retried_cost={retried:.6f} total={total:.6f}"


def _latency_retry_trend(samples: list[LLMCall]) -> SkillVerdict:
    retried = [c for c in samples if c.retry_count > 0]
    if len(retried) < 2:
        return "soft_pass"
    ordered = sorted(retried, key=lambda c: c.retry_count)
    increases = 0
    pairs = 0
    for a, b in zip(ordered, ordered[1:]):
        pairs += 1
        if b.latency_ms >= a.latency_ms:
            increases += 1
    if pairs == 0:
        return "soft_pass"
    if increases == pairs:
        return "pass"
    if increases >= pairs / 2:
        return "soft_pass"
    # flat/decreasing with high retries
    if max(c.retry_count for c in retried) >= 3:
        return "fail"
    return "soft_pass"


def score_retry_loop(anomaly: AnomalyWindow) -> dict[str, Any]:
    sig = anomaly.signals
    samples = list(anomaly.sample_calls)
    retry_z = float(sig.retry_z_score)
    max_retry = int(sig.max_retry_count)
    avg_retry = float(sig.avg_retry_count)
    rpc = int(sig.repeated_parent_call_count)

    # 1 retry_zscore_threshold
    if retry_z >= 5:
        s1 = _skill("pass", value=retry_z, detail="retry_z >= 5")
    elif retry_z >= 3:
        s1 = _skill("soft_pass", value=retry_z, detail="retry_z in [3,5)")
    else:
        s1 = _skill("fail", value=retry_z, detail="retry_z < 3")

    # 2 max_retry_floor
    if max_retry >= 5:
        s2 = _skill("pass", value=max_retry, detail="max_retry >= 5")
    elif max_retry >= 2:
        s2 = _skill("soft_pass", value=max_retry, detail="max_retry in [2,4]")
    else:
        s2 = _skill("fail", value=max_retry, detail="max_retry < 2")

    # 3 repeated_parent_call_check
    if rpc >= 3:
        s3 = _skill("pass", value=rpc)
    elif rpc >= 1:
        s3 = _skill("soft_pass", value=rpc)
    else:
        s3 = _skill("fail", value=rpc)

    # 4 latency_growth_confirmation
    lat_v = _latency_retry_trend(samples)
    s4 = _skill(lat_v, value=sig.latency_z_score, detail="sample latency vs retry_count")

    # 5 sample_call_consistency (falsifier) — soft when samples empty
    if not samples:
        s5 = _skill(
            "soft_pass",
            value=None,
            detail="no samples; skip hard fail",
            falsifier=True,
        )
    else:
        with_retry = sum(1 for c in samples if c.retry_count > 0)
        frac = with_retry / len(samples)
        if max_retry >= 2 and frac < 0.25:
            s5 = _skill(
                "fail",
                value=frac,
                detail="aggregate max_retry high but samples mostly retry_count=0",
                falsifier=True,
            )
        elif max_retry >= 2 and frac < 0.5:
            s5 = _skill(
                "soft_pass",
                value=frac,
                detail="partial sample/aggregate mismatch",
                falsifier=True,
            )
        else:
            s5 = _skill(
                "pass",
                value=frac,
                detail="samples consistent with retries",
                falsifier=True,
            )

    # 6 cost_attribution_check (falsifier)
    attr, attr_detail = _retry_cost_attribution(samples)
    if attr is None:
        s6 = _skill(
            "soft_pass",
            value=None,
            detail=f"insufficient data ({attr_detail})",
            falsifier=True,
        )
        attr_pct = None
    elif attr >= 50:
        s6 = _skill("pass", value=attr, detail=attr_detail, falsifier=True)
        attr_pct = attr
    elif attr >= 20:
        s6 = _skill("soft_pass", value=attr, detail=attr_detail, falsifier=True)
        attr_pct = attr
    else:
        s6 = _skill("fail", value=attr, detail=attr_detail, falsifier=True)
        attr_pct = attr

    # 7 non_retry_alternative_scan
    tok_g = float(sig.input_token_growth_pct or 0.0)
    model_ch = bool(sig.model_changed)
    if model_ch and (sig.cost_growth_pct or 0) >= 200 and abs(sig.token_growth_pct or 0) < 50:
        s7 = _skill("fail", value="model_routing_strong", detail="competing model-routing signal")
    elif tok_g >= 300 and sig.max_call_chain_depth >= 5:
        s7 = _skill("fail", value="token_context_strong", detail="competing context-bloat signal")
    elif tok_g >= 100 or model_ch:
        s7 = _skill("soft_pass", value="moderate_alt", detail="some competing signal")
    else:
        s7 = _skill("pass", value="none", detail="no strong alternative")

    skills = {
        "retry_zscore_threshold": s1,
        "max_retry_floor": s2,
        "repeated_parent_call_check": s3,
        "latency_growth_confirmation": s4,
        "sample_call_consistency": s5,
        "cost_attribution_check": s6,
        "non_retry_alternative_scan": s7,
    }
    forced = _any_falsifier_fail(skills)

    # Band ceiling + suggested confidence (aligned with Agents.md + demos)
    if forced:
        band = 0.49
        conf = 0.30
        hyp = "no_strong_signal"
    elif s1["verdict"] == "pass" and s2["verdict"] == "pass" and s6["verdict"] == "pass":
        band = 1.0
        conf = 0.94
        hyp = "uncapped_retry_loop"
    elif (
        s1["verdict"] in ("pass", "soft_pass")
        and s2["verdict"] in ("pass", "soft_pass")
        and s6["verdict"] != "fail"
        and (s1["verdict"] == "pass" or s2["verdict"] == "pass" or s3["verdict"] in ("pass", "soft_pass"))
    ):
        band = 0.89
        conf = 0.82
        hyp = "uncapped_retry_loop"
    elif max_retry >= 2 or avg_retry >= 1 or retry_z >= 2:
        band = 0.74
        conf = 0.60
        hyp = "uncapped_retry_loop"
    else:
        band = 0.49
        conf = 0.30
        hyp = "no_strong_signal"

    if s7["verdict"] == "fail" and hyp != "no_strong_signal":
        band = min(band, 0.74)
        conf = min(conf, 0.60)

    return {
        "agent_name": "retry_loop_agent",
        "skills": skills,
        "forced_no_strong_signal": forced,
        "band_ceiling": band,
        "suggested_confidence": conf,
        "suggested_hypothesis": hyp,
        "metrics": {
            "retry_z_score": retry_z,
            "max_retry_count": max_retry,
            "avg_retry_count": avg_retry,
            "repeated_parent_call_count": rpc,
            "cost_attribution_pct": attr_pct,
        },
    }


def score_token_context(anomaly: AnomalyWindow) -> dict[str, Any]:
    sig = anomaly.signals
    samples = list(anomaly.sample_calls)
    growth = float(sig.input_token_growth_pct or 0.0)
    depth = int(sig.max_call_chain_depth)
    z = float(sig.input_tokens_z_score)
    cost_g = float(sig.cost_growth_pct or 0.0)
    token_g = float(sig.token_growth_pct or growth)

    # 1 token_growth_threshold
    if growth >= 300:
        s1 = _skill("pass", value=growth)
    elif growth >= 100:
        s1 = _skill("soft_pass", value=growth)
    else:
        s1 = _skill("fail", value=growth)

    # 2 chain_depth_floor
    if depth >= 5:
        s2 = _skill("pass", value=depth)
    elif depth >= 3:
        s2 = _skill("soft_pass", value=depth)
    else:
        s2 = _skill("fail", value=depth)

    # 3 chain_depth_recency (falsifier) — soft unless samples clearly outside window
    if not samples or depth < 2:
        s3 = _skill(
            "soft_pass",
            value=None,
            detail="insufficient chain timing evidence",
            falsifier=True,
        )
    else:
        in_window = sum(
            1
            for c in samples
            if anomaly.start_time <= c.timestamp <= anomaly.end_time
        )
        frac = in_window / len(samples)
        if frac >= 0.5:
            s3 = _skill("pass", value=frac, detail="deep chain samples in window", falsifier=True)
        elif frac > 0:
            s3 = _skill("soft_pass", value=frac, detail="partial recency", falsifier=True)
        else:
            s3 = _skill(
                "fail",
                value=frac,
                detail="samples outside anomaly window",
                falsifier=True,
            )

    # 4 token_zscore_check
    if z >= 3:
        s4 = _skill("pass", value=z)
    elif z >= 2:
        s4 = _skill("soft_pass", value=z)
    else:
        s4 = _skill("fail", value=z)

    # 5 prompt_content_growth_correlation (falsifier)
    # Fail when cost growth far exceeds token growth (looks like model routing)
    if token_g <= 0 and cost_g >= 100:
        s5 = _skill(
            "fail",
            value={"cost_growth_pct": cost_g, "token_growth_pct": token_g},
            detail="cost grew without token growth",
            falsifier=True,
        )
    elif token_g > 0 and cost_g > max(token_g * 2.5, token_g + 150):
        s5 = _skill(
            "fail",
            value={"cost_growth_pct": cost_g, "token_growth_pct": token_g},
            detail="cost growth far exceeds token growth",
            falsifier=True,
        )
    elif token_g > 0 and cost_g > token_g * 1.5 + 50:
        s5 = _skill(
            "soft_pass",
            value={"cost_growth_pct": cost_g, "token_growth_pct": token_g},
            detail="mild cost/token divergence",
            falsifier=True,
        )
    else:
        s5 = _skill(
            "pass",
            value={"cost_growth_pct": cost_g, "token_growth_pct": token_g},
            detail="cost tracks tokens",
            falsifier=True,
        )

    # 6 self_call_pattern_check
    parents = [c.parent_call_id for c in samples if c.parent_call_id]
    if depth >= 3 and len(parents) >= 2:
        s6 = _skill("pass", value=len(parents), detail="parent chain present")
    elif depth >= 2 or parents:
        s6 = _skill("soft_pass", value=len(parents), detail="weak chain signature")
    else:
        s6 = _skill("fail", value=0, detail="no recursive signature")

    # 7 sample_consistency_check
    if not samples:
        s7 = _skill("soft_pass", value=None, detail="no samples")
    else:
        max_in = max(c.input_tokens for c in samples)
        min_in = min(c.input_tokens for c in samples)
        if growth >= 100 and max_in <= min_in * 1.1 and len(samples) >= 3:
            s7 = _skill("fail", value=(min_in, max_in), detail="samples lack token growth")
        else:
            s7 = _skill("pass", value=(min_in, max_in), detail="samples show token variation")

    skills = {
        "token_growth_threshold": s1,
        "chain_depth_floor": s2,
        "chain_depth_recency": s3,
        "token_zscore_check": s4,
        "prompt_content_growth_correlation": s5,
        "self_call_pattern_check": s6,
        "sample_consistency_check": s7,
    }
    forced = _any_falsifier_fail(skills)

    if forced:
        band, conf, hyp = 0.49, 0.28, "no_strong_signal"
    elif s1["verdict"] == "pass" and s2["verdict"] == "pass":
        band, conf, hyp = 1.0, 0.93, "context_bloat_self_calling_agent"
    elif s4["verdict"] == "pass" and depth >= 4:
        # demo context_bloat: growth ~294 soft, z high, depth 5
        band, conf, hyp = 0.89, 0.84, "context_bloat_self_calling_agent"
    elif s1["verdict"] in ("pass", "soft_pass") or s4["verdict"] == "pass":
        band, conf, hyp = 0.74, 0.62, "context_bloat_self_calling_agent"
    else:
        band, conf, hyp = 0.49, 0.28, "no_strong_signal"

    # TC-03 style: deep chain but little token growth
    if depth >= 5 and growth < 100 and not forced:
        band = min(band, 0.49)
        conf = min(conf, 0.40)
        hyp = "no_strong_signal"

    return {
        "agent_name": "token_context_agent",
        "skills": skills,
        "forced_no_strong_signal": forced,
        "band_ceiling": band,
        "suggested_confidence": conf,
        "suggested_hypothesis": hyp,
        "metrics": {
            "input_token_growth_pct": growth,
            "max_call_chain_depth": depth,
            "input_tokens_z_score": z,
            "cost_growth_pct": cost_g,
            "token_growth_pct": token_g,
        },
    }


def score_model_routing(anomaly: AnomalyWindow) -> dict[str, Any]:
    sig = anomaly.signals
    samples = list(anomaly.sample_calls)
    model_changed = bool(sig.model_changed)
    cost_g = float(sig.cost_growth_pct or 0.0)
    token_g = float(sig.token_growth_pct or 0.0)
    cost_z = float(sig.cost_z_score)
    before = sig.model_before
    during = sig.model_during

    # 1 model_changed_flag (falsifier)
    if model_changed:
        s1 = _skill("pass", value=True, falsifier=True)
    else:
        s1 = _skill("fail", value=False, detail="model did not change", falsifier=True)

    # 2 cost_growth_threshold
    if cost_g >= 200:
        s2 = _skill("pass", value=cost_g)
    elif cost_g >= 50:
        s2 = _skill("soft_pass", value=cost_g)
    else:
        s2 = _skill("fail", value=cost_g)

    # 3 token_stability_check (caps confidence if fail, not hard no_strong)
    if abs(token_g) < 50:
        s3 = _skill("pass", value=token_g, detail="tokens stable")
    elif abs(token_g) <= 100:
        s3 = _skill("soft_pass", value=token_g, detail="moderate token growth")
    else:
        s3 = _skill("fail", value=token_g, detail="large token growth confounds routing")

    # 4 cost_zscore_check
    if cost_z >= 3:
        s4 = _skill("pass", value=cost_z)
    elif cost_z >= 2:
        s4 = _skill("soft_pass", value=cost_z)
    else:
        s4 = _skill("fail", value=cost_z)

    # 5 price_tier_consistency (falsifier)
    if before and during:
        b_in, b_out, b_key = lookup_rates(before)
        d_in, d_out, d_key = lookup_rates(during)
        if b_key == "fallback_default" or d_key == "fallback_default":
            s5 = _skill(
                "soft_pass",
                value={"before": before, "during": during},
                detail="unknown model pricing",
                falsifier=True,
            )
        else:
            before_price = b_in + b_out
            after_price = d_in + d_out
            if after_price > before_price * 1.05:
                s5 = _skill(
                    "pass",
                    value={"before_price": before_price, "after_price": after_price},
                    detail="after model is pricier",
                    falsifier=True,
                )
            elif after_price >= before_price * 0.95:
                s5 = _skill(
                    "soft_pass",
                    value={"before_price": before_price, "after_price": after_price},
                    detail="similar price tier",
                    falsifier=True,
                )
            else:
                s5 = _skill(
                    "fail",
                    value={"before_price": before_price, "after_price": after_price},
                    detail="after model is cheaper — not a premium misroute",
                    falsifier=True,
                )
    else:
        s5 = _skill(
            "soft_pass",
            value={"before": before, "during": during},
            detail="model before/during incomplete",
            falsifier=True,
        )

    # 6 sample_call_confirmation
    if not samples:
        s6 = _skill("soft_pass", value=None, detail="no samples")
    elif during:
        n = sum(1 for c in samples if c.model == during)
        frac = n / len(samples)
        if frac >= 0.5:
            s6 = _skill("pass", value=frac, detail="samples show during model")
        elif n > 0:
            s6 = _skill("soft_pass", value=frac, detail="partial during-model presence")
        else:
            s6 = _skill("fail", value=0, detail="during model absent from samples")
    else:
        s6 = _skill("soft_pass", value=None, detail="no model_during")

    # 7 routing_scope_check (informational)
    models = sorted({c.model for c in samples}) if samples else list(sig.models_seen or [])
    s7 = _skill(
        "pass" if len(models) <= 2 else "soft_pass",
        value=models,
        detail="models in samples",
    )

    skills = {
        "model_changed_flag": s1,
        "cost_growth_threshold": s2,
        "token_stability_check": s3,
        "cost_zscore_check": s4,
        "price_tier_consistency": s5,
        "sample_call_confirmation": s6,
        "routing_scope_check": s7,
    }
    forced = _any_falsifier_fail(skills)

    if forced:
        band, conf, hyp = 0.49, 0.32, "no_strong_signal"
    elif (
        s1["verdict"] == "pass"
        and s2["verdict"] == "pass"
        and s3["verdict"] == "pass"
    ):
        band, conf, hyp = 1.0, 0.95, "expensive_model_misroute"
    elif s1["verdict"] == "pass" and s4["verdict"] == "pass":
        band, conf, hyp = 0.89, 0.84, "expensive_model_misroute"
    elif s1["verdict"] == "pass" and cost_g >= 100:
        band, conf, hyp = 0.74, 0.64, "expensive_model_misroute"
    else:
        band, conf, hyp = 0.49, 0.32, "no_strong_signal"

    # Cap when tokens confound
    if s3["verdict"] == "fail" and hyp != "no_strong_signal":
        band = min(band, 0.74)
        conf = min(conf, 0.64)

    return {
        "agent_name": "model_routing_agent",
        "skills": skills,
        "forced_no_strong_signal": forced,
        "band_ceiling": band,
        "suggested_confidence": conf,
        "suggested_hypothesis": hyp,
        "metrics": {
            "model_changed": model_changed,
            "model_before": before,
            "model_during": during,
            "cost_growth_pct": cost_g,
            "token_growth_pct": token_g,
            "cost_z_score": cost_z,
        },
    }


def score_all_agents(anomaly: AnomalyWindow) -> dict[str, dict[str, Any]]:
    return {
        "retry_loop_agent": score_retry_loop(anomaly),
        "token_context_agent": score_token_context(anomaly),
        "model_routing_agent": score_model_routing(anomaly),
    }
