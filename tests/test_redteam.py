from benchmarks.redteam import cases as C
from benchmarks.redteam import run as R


def test_cases_sane_on_dev_seed():
    assert len(C.CASES) == 12
    for name in C.CASES:
        calls, truth, t_inc, note = C.make_case(name, 300)
        assert len(calls) > 100 and note
        assert calls[0].timestamp < t_inc < calls[-1].timestamp
        feats = {c.feature_tag for c in calls}
        for cause, feat in truth:
            assert feat in feats
    assert C.make_case("c06_three_causes", 300)[1].__len__() == 3
    assert C.make_case("c08_new_feature", 300)[1] == []


def test_runner_two_cases_one_seed(tmp_path):
    res = R.run(["c08_new_feature", "c09_planned_upgrade"], [300], workers=1)
    assert res["summary"]["n_datasets"] == 2 and res["summary"]["crashes"] == 0
    assert "| c08_new_feature" in R.markdown(res)
