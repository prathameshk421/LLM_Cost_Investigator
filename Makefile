# Common tasks. `make help` lists them.
PY ?= .venv/bin/python
export PYTHONPATH := web:.

.PHONY: help install dev api ui serve test bench heldout demo-seed docker burstgpt proof proof-synth proof-real proof-redteam proof-labels proof-live
help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'
install:         ## create venv, install backend + frontend deps
	python3 -m venv .venv && $(PY) -m pip install -q -e ".[web]" pytest
	cd web/frontend && npm ci
api:             ## run the API with reload on :8000
	$(PY) -m uvicorn api.app:app --reload --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 3
ui:              ## run the Vite dev server on :5173
	cd web/frontend && npm run dev
dev:             ## API + UI dev servers together
	$(MAKE) -j2 api ui
serve:           ## build the UI and serve everything on :8000
	cd web/frontend && npm run build
	$(PY) -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 3
test:            ## all tests + scenario CLI + accuracy gate
	$(PY) -m pytest -q
	$(PY) -m llm_cost_investigator.cli --scenario all --force-fallback
	$(PY) -m llm_cost_investigator.benchmark --seeds 30 --noise 0.15 --out /tmp/llmci-bench.json --min-accuracy 0.97
	PYTHONPATH=. $(PY) -m benchmarks.heldout.run --seed-from 60 --seed-to 64 --min-hit 0.95 --max-false-alarm 0.05
bench:           ## accuracy benchmark across noise levels
	for n in 0.08 0.15 0.25 0.35; do $(PY) -m llm_cost_investigator.benchmark --seeds 60 --noise $$n --out /tmp/llmci-bench-$$n.json; done
heldout:         ## held-out benchmark on fresh seeds (the honest accuracy number)
	PYTHONPATH=. $(PY) -m benchmarks.heldout.run --seed-from 30 --seed-to 60
demo-seed:       ## load 30 days of demo history into the running API
	curl -s -X POST localhost:8000/api/warehouse/seed -H 'content-type: application/json' -d '{"days":60,"seed":1,"incidents":true,"reset":true}'
docker:          ## build and run the single-container app on :8000
	docker compose up --build

# ---- proof tiers (docs/proof/PLAN.md, thresholds in benchmarks/PREREGISTRATION.md)
BURSTGPT ?= data/external/BurstGPT_1.csv
proof-synth:     ## Tier A: 2,800 held-out datasets + baselines (free, ~12 min)
	PYTHONPATH=. $(PY) -m benchmarks.heldout.tier_a --seed-from 200 --seed-to 300 --baselines --out docs/proof/data/tier_a_200_299.json
burstgpt:        ## download the BurstGPT trace (52 MB, CC-BY-4.0) for Tier B
	mkdir -p data/external && gh release download v2.0 -R HPMLL/BurstGPT -p BurstGPT_1.csv -D data/external --clobber
proof-real:      ## Tier B: BurstGPT real-traffic background (free; needs BURSTGPT=path)
	TIERB_CLEAN_MAX_ANOMALIES=50 BURSTGPT_CSV=$(BURSTGPT) PYTHONPATH=. $(PY) -m benchmarks.real.tier_b --slices-seed-from 200 --slices-seed-to 300 --clean-from 500 --clean-to 600 --out docs/proof/data/tier_b.json
proof-redteam:   ## Tier C: 12 adversarial cases x 20 seeds (free)
	PYTHONPATH=. $(PY) -m benchmarks.redteam.run --seed-from 200 --seed-to 220 --out docs/proof/data/tier_c.json
proof-labels:    ## Tier E: export blind slices, model labels (HF), score
	PYTHONPATH=. $(PY) -m benchmarks.tier_e.blind_labels export
	PYTHONPATH=. $(PY) -m benchmarks.tier_e.blind_labels model
	PYTHONPATH=. $(PY) -m benchmarks.tier_e.blind_labels score
proof-live:      ## Tier D: real app, real LLM calls, 3 induced bugs (~2.5 h, ~$0.5; needs DEPLOY_API_KEY + HF login)
	PYTHONUNBUFFERED=1 PYTHONPATH=. $(PY) examples/live_app/app.py --bug retry_loop,misroute,bloat --rate 4 --minutes 20 --gap-minutes 10
proof: proof-synth proof-redteam   ## all free, offline tiers
