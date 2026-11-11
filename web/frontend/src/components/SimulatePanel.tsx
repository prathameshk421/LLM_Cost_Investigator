import { useState } from "react";
import { runSimulation } from "../api/client";
import type { InvestigateResponse, LabScenario, ProviderChoice } from "../types/investigate";

const SCENARIOS: Array<{ id: LabScenario; title: string; hint: string }> = [
  { id: "retry_loop", title: "Retry loop", hint: "support_reply retries 6–12x on one parent" },
  { id: "context_bloat", title: "Context bloat", hint: "agent_reflection calls itself, prompt grows per hop" },
  { id: "model_misroute", title: "Model misroute", hint: "summarizer switched to a premium model" },
  { id: "traffic_surge", title: "Traffic surge", hint: "control: 3–5x real demand, no bug" },
  { id: "clean", title: "Clean", hint: "control: normal traffic only" },
];

export function SimulatePanel({
  onResult,
  hfReady,
}: {
  onResult: (r: InvestigateResponse) => void;
  hfReady: boolean;
}) {
  const [scenario, setScenario] = useState<LabScenario>("model_misroute");
  const [seed, setSeed] = useState(7);
  const [noise, setNoise] = useState(0.08);
  const [provider, setProvider] = useState<ProviderChoice>("fallback");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(nextSeed = seed) {
    setBusy(true);
    setError(null);
    try {
      onResult(await runSimulation({ scenario, seed: nextSeed, noise, provider }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="label" style={{ marginBottom: 6 }}>
        Scenario lab
      </div>
      <p className="investigate-help">
        Makes a new noisy telemetry set for each seed (about 450 calls, 5 features), then
        runs the full pipeline: detector → router → agents → aggregator.
      </p>

      <div className="scenario-chips" role="radiogroup" aria-label="Scenario">
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            type="button"
            role="radio"
            aria-checked={scenario === s.id}
            className={"scenario-chip" + (scenario === s.id ? " active" : "")}
            onClick={() => setScenario(s.id)}
          >
            <span className="chip-title">{s.title}</span>
            <span className="chip-hint">{s.hint}</span>
          </button>
        ))}
      </div>

      <div className="investigate-grid">
        <label>
          Seed
          <input
            type="number"
            min={0}
            value={seed}
            onChange={(e) => setSeed(Math.max(0, Number(e.target.value) || 0))}
          />
        </label>
        <label>
          Baseline noise · {(noise * 100).toFixed(0)}%
          <input
            type="range"
            min={0}
            max={0.4}
            step={0.01}
            value={noise}
            onChange={(e) => setNoise(Number(e.target.value))}
          />
        </label>
        <label>
          Agents
          <select value={provider} onChange={(e) => setProvider(e.target.value as ProviderChoice)}>
            <option value="fallback">Deterministic (free, offline)</option>
            <option value="hf" disabled={!hfReady}>
              Live LLM · Hugging Face{hfReady ? "" : " (no token)"}
            </option>
            <option value="groq">Live LLM · Groq</option>
            <option value="cerebras">Live LLM · Cerebras</option>
          </select>
        </label>
      </div>

      {provider !== "fallback" ? (
        <p className="investigate-help live-note">
          Live mode calls a real model for each routed agent (about $0.0003 per call). A
          hard budget in the API stops spend at the limit shown top right; agents then
          use the deterministic path.
        </p>
      ) : null}

      <div className="investigate-actions">
        <button type="button" className="primary" disabled={busy} onClick={() => run()}>
          {busy ? "Running…" : "Run investigation"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            const s = Math.floor(Math.random() * 100000);
            setSeed(s);
            run(s);
          }}
        >
          Random seed
        </button>
      </div>
      {error ? <div className="error-banner" style={{ marginTop: 10 }}>{error}</div> : null}
    </>
  );
}
