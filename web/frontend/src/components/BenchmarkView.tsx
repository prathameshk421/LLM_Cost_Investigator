import { useEffect, useState } from "react";
import { getBenchmark } from "../api/client";
import type { BenchmarkReport } from "../types/investigate";

const SHORT: Record<string, string> = {
  uncapped_retry_loop: "retry loop",
  context_bloat_self_calling_agent: "context bloat",
  expensive_model_misroute: "misroute",
  no_strong_signal: "no signal",
};
const ORDER = [
  "uncapped_retry_loop",
  "context_bloat_self_calling_agent",
  "expensive_model_misroute",
  "no_strong_signal",
];

export function BenchmarkView() {
  const [seeds, setSeeds] = useState(20);
  const [noise, setNoise] = useState(0.15);
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(refresh: boolean) {
    setBusy(true);
    setError(null);
    try {
      setReport(await getBenchmark(seeds, noise, refresh));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const confusionMax = report
    ? Math.max(1, ...Object.values(report.confusion).flatMap((row) => Object.values(row)))
    : 1;

  return (
    <div className="bench">
      <div className="investigate-panel">
        <div className="label" style={{ marginBottom: 6 }}>Accuracy benchmark</div>
        <p className="investigate-help">
          Runs every lab scenario across many seeds with the deterministic agents (no API
          spend) and scores the root cause. The two control scenarios measure false alarms.
          CLI: <span className="mono">python -m llm_cost_investigator.benchmark</span>
        </p>
        <div className="investigate-grid">
          <label>
            Seeds per scenario
            <input type="number" min={1} max={40} value={seeds} onChange={(e) => setSeeds(Math.min(40, Math.max(1, Number(e.target.value) || 1)))} />
          </label>
          <label>
            Baseline noise · {(noise * 100).toFixed(0)}%
            <input type="range" min={0} max={0.4} step={0.01} value={noise} onChange={(e) => setNoise(Number(e.target.value))} />
          </label>
        </div>
        <div className="investigate-actions">
          <button type="button" className="primary" disabled={busy} onClick={() => load(true)}>
            {busy ? "Running…" : "Run benchmark"}
          </button>
        </div>
        {error ? <div className="error-banner" style={{ marginTop: 10 }}>{error}</div> : null}
      </div>

      {report ? (
        <>
          <div className="bench-tiles">
            <div className="bench-tile">
              <div className="label">Overall accuracy</div>
              <div className="bench-hero">{(report.overall_accuracy * 100).toFixed(1)}%</div>
              <div className="bench-sub">{report.runs_total} runs · {report.config.seeds} seeds × {report.config.scenarios.length} scenarios</div>
            </div>
            <div className="bench-tile">
              <div className="label">False alarm rate</div>
              <div className="bench-hero">{(report.false_alarm_rate * 100).toFixed(1)}%</div>
              <div className="bench-sub">bug reported on clean or surge data</div>
            </div>
            <div className="bench-tile">
              <div className="label">Noise</div>
              <div className="bench-hero">{(report.config.noise * 100).toFixed(0)}%</div>
              <div className="bench-sub">relative std dev of baseline jitter</div>
            </div>
          </div>

          <div className="investigate-panel" style={{ marginTop: 14 }}>
            <div className="label" style={{ marginBottom: 10 }}>Accuracy per scenario</div>
            {Object.entries(report.per_scenario).map(([s, row]) => (
              <div key={s} className="bench-row">
                <span className="mono bench-name">{s}</span>
                <span className="bench-bar" aria-hidden>
                  <span style={{ width: `${row.accuracy * 100}%` }} />
                </span>
                <span className="mono bench-val">
                  {(row.accuracy * 100).toFixed(0)}% <span style={{ color: "var(--text-2)" }}>({row.correct}/{row.runs})</span>
                </span>
              </div>
            ))}
          </div>

          <div className="investigate-panel" style={{ marginTop: 14 }}>
            <div className="label" style={{ marginBottom: 10 }}>Confusion matrix · rows expected, columns predicted</div>
            <table className="table confusion">
              <thead>
                <tr>
                  <th />
                  {ORDER.map((c) => <th key={c} className="mono">{SHORT[c]}</th>)}
                </tr>
              </thead>
              <tbody>
                {ORDER.filter((r) => report.confusion[r]).map((r) => (
                  <tr key={r}>
                    <th className="mono">{SHORT[r]}</th>
                    {ORDER.map((c) => {
                      const v = report.confusion[r]?.[c] ?? 0;
                      const diag = r === c;
                      return (
                        <td
                          key={c}
                          className={"mono cm-cell" + (diag ? " diag" : v ? " off" : "")}
                          style={{ ["--a" as string]: v / confusionMax }}
                        >
                          {v || "·"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {report.failures.length ? (
              <details style={{ marginTop: 12 }}>
                <summary className="label" style={{ cursor: "pointer" }}>
                  {report.failures.length} failed run(s) shown
                </summary>
                <pre className="json-block">{JSON.stringify(report.failures, null, 2)}</pre>
              </details>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
