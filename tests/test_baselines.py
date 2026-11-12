import time
from datetime import datetime, timedelta, timezone

from benchmarks.baselines import run_baselines
from llm_cost_investigator.schemas import LLMCall

T0 = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)
_n = [0]


def call(w, feature="f", model="m1", cost=1.0, retry=0, parent=None, cid=None, sec=10):
    _n[0] += 1
    return LLMCall(
        timestamp=T0 + timedelta(minutes=5 * w, seconds=sec),
        call_id=cid or f"c{_n[0]}",
        parent_call_id=parent,
        feature_tag=feature,
        model=model,
        input_tokens=100,
        output_tokens=10,
        cost_usd=cost,
        latency_ms=100,
        retry_count=retry,
    )


def base(n=20, **kw):
    return [call(w, **kw) for w in range(n)]


def causes(res, key):
    return [(i["cause"], i["start"], i["end"]) for i in res[key]]


def test_retry_threshold():
    for r, fires in ((4, True), (3, False)):
        res = run_baselines(base() + [call(15, retry=r, sec=20)])
        assert bool(res["B2"]) is fires
        if fires:
            assert res["B2"][0]["cause"] == "uncapped_retry_loop"
            assert res["B2"][0]["start"] == T0 + timedelta(minutes=75)


def test_new_vs_known_model():
    calls = base() + [call(15, model="m2", sec=20)]
    assert causes(run_baselines(calls), "B2")[0][0] == "expensive_model_misroute"
    calls = base() + [call(3, model="m2", sec=20), call(15, model="m2", sec=20)]
    assert run_baselines(calls)["B2"] == []  # m2 seen earlier (in warm-up)


def chain(w, length, prefix):
    out, parent = [], None
    for i in range(length):
        c = call(w, cid=f"{prefix}{i}", parent=parent, sec=20 + i)
        out.append(c)
        parent = c.call_id
    return out


def test_depth_threshold():
    res = run_baselines(base() + chain(15, 5, "a"))  # depth 4
    assert causes(res, "B2")[0][0] == "context_bloat_self_calling_agent"
    assert run_baselines(base() + chain(15, 4, "b"))["B2"] == []  # depth 3


def test_depth_cycle_guard():
    a = call(15, cid="x", parent="y")
    b = call(15, cid="y", parent="x")
    run_baselines(base() + [a, b])


def test_warmup_respected():
    early = run_baselines([call(11, retry=9)] + base(11))
    assert early["B2"] == []
    ok = run_baselines(base(12) + [call(12, retry=9, sec=20)])
    assert len(ok["B2"]) == 1
    # B1: huge spike inside warm-up is ignored
    flat = [call(w, cost=1.0 + 0.01 * (w % 3)) for w in range(11)] + [call(11, cost=50)]
    assert run_baselines(flat)["B1"] == []


def test_cost_z_flat_vs_spiky():
    flat = [call(w, cost=1.0 + 0.01 * (w % 3)) for w in range(30)] + [call(30, cost=2.0)]
    res = run_baselines(flat)
    assert [i["cause"] for i in res["B1"]] == ["expensive_model_misroute"]
    spiky = [call(w, cost=1.0 + (w % 2) * 9) for w in range(30)] + [call(30, cost=2.0)]
    assert run_baselines(spiky)["B1"] == []


def test_idle_windows_count_as_zero():
    # calls only every 5th window: the idle windows (cost 0) make cost 5 normal
    calls = [call(w, cost=5.0) for w in range(0, 60, 5)]
    assert run_baselines(calls)["B1"] == []


def test_flagged_windows_stay_in_baseline():
    calls = [call(w, cost=1.0 + 0.01 * (w % 3)) for w in range(30)]
    calls += [call(w, cost=20.0) for w in range(30, 36)]
    res = run_baselines(calls)["B1"]
    assert len(res) == 1
    assert res[0]["start"] == T0 + timedelta(minutes=150)
    # later windows of the burst are not all flagged: baseline absorbed them
    assert res[0]["end"] < T0 + timedelta(minutes=180)


def test_merge_same_label_and_gap():
    calls = base() + [call(15, retry=5, sec=20), call(16, retry=5, sec=20), call(18, retry=5, sec=20)]
    res = run_baselines(calls)["B2"]
    assert [(i["start"], i["end"]) for i in res] == [
        (T0 + timedelta(minutes=75), T0 + timedelta(minutes=85)),
        (T0 + timedelta(minutes=90), T0 + timedelta(minutes=95)),
    ]


def test_no_merge_across_labels_or_features():
    calls = base() + [call(15, retry=5, sec=20), call(16, model="m9", sec=20)]
    res = run_baselines(calls)["B2"]
    assert [i["cause"] for i in res] == ["uncapped_retry_loop", "expensive_model_misroute"]
    calls = base() + base(feature="g") + [call(15, retry=5, feature="g", sec=20)]
    assert [i["feature"] for i in run_baselines(calls)["B2"]] == ["g"]


def test_generated_case_fast():
    from benchmarks.heldout.gen import make_case

    calls, _truth, _t = make_case("retry_medium", 300)
    t = time.perf_counter()
    res = run_baselines(calls)
    dt = time.perf_counter() - t
    assert set(res) == {"B1", "B2"}
    assert dt < 0.5, dt
    print("baseline time", dt, len(calls), {k: len(v) for k, v in res.items()})
