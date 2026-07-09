import { CountUp } from "./CountUp";

interface Evidence {
  agent_name: string;
  hypothesis: string;
  confidence: number;
}

export function RootCausePanel({ payload }: { payload: Record<string, unknown> }) {
  const hypothesis = payload.hypothesis as string;
  const confidence = Number(payload.confidence ?? 0);
  const winning = payload.winning_agent as string | null;
  const evidence = (payload.all_evidence as Evidence[]) || [];
  const recommendations = (payload.recommendations as string[]) || [];
  const tieBreak = payload.tie_break_note as string | null;

  return (
    <div className="panel-stack">
      <div className="panel-header">
        <h2>Root cause locked</h2>
        <span className="badge badge-warm">aggregator</span>
      </div>
      <div className="glass glass-warm" style={{ padding: 24 }}>
        <div className="label">Hypothesis</div>
        <div style={{ fontSize: 22, fontWeight: 600, margin: "8px 0" }}>{hypothesis}</div>
        <div className="confidence-big">
          <CountUp value={confidence * 100} decimals={0} />
          <span style={{ fontSize: 20, marginLeft: 4 }}>%</span>
        </div>
        <div className="mono" style={{ color: "var(--text-1)", marginTop: 8 }}>
          winning agent: {winning ?? "none"}
        </div>
        {tieBreak ? (
          <div style={{ marginTop: 10, color: "var(--text-2)", fontSize: 12 }}>{tieBreak}</div>
        ) : null}
      </div>

      <div className="glass" style={{ padding: 16 }}>
        <div className="label" style={{ marginBottom: 10 }}>
          All evidence
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Hypothesis</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {evidence.map((e) => (
              <tr key={e.agent_name + e.hypothesis}>
                <td>{e.agent_name}</td>
                <td>{e.hypothesis}</td>
                <td>{(e.confidence * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="glass glass-warm" style={{ padding: 16 }}>
        <div className="label" style={{ marginBottom: 10 }}>
          Recommendations
        </div>
        <ul className="rec-list">
          {recommendations.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
