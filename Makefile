# Common tasks. `make help` lists them.
PY ?= .venv/bin/python
export PYTHONPATH := web:.

.PHONY: help install dev api ui serve test bench heldout demo-seed docker
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
