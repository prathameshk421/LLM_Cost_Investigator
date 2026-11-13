import { CountUp } from "./CountUp";

interface Highlight {
  key: string;
  value: unknown;
  threshold?: string | null;
  status?: string;
}

export function SignalsPanel({ payload }: { payload: Record<string, unknown> }) {
  const highlights = (payload.highlights as Highlight[]) || [];
  const sampleCalls = (payload.sample_calls as Record<string, unknown>[]) || [];

  return (
    <div className="panel-stack">
      <div className="panel-header">
        <h2>Raw signals</h2>
        <span className="badge badge-cool">deterministic</span>
      </div>
      <div className="grid-metrics">
        {highlights.map((h) => (
          <div key={h.key} className={`glass glass-cool metric-card status-${h.status ?? "neutral"}`}>
            <div className="key">{h.key}</div>
            <div className="metric-value">
              {typeof h.value === "number" ? (
                <CountUp value={h.value} decimals={Number.isInteger(h.value) ? 0 : 2} />
              ) : typeof h.value === "boolean" ? (
                String(h.value)
              ) : (
                String(h.value)
              )}
            </div>
            {h.threshold ? <div className="threshold">{h.threshold}</div> : null}
          </div>
        ))}
      </div>
      {sampleCalls.length > 0 ? (
        <div className="glass" style={{ padding: 16 }}>
          <div className="label" style={{ marginBottom: 10 }}>
            Sample calls
          </div>
          <div className="json-block">{JSON.stringify(sampleCalls.slice(0, 6), null, 2)}</div>
        </div>
      ) : null}
    </div>
  );
}
