interface Candidate {
  agent_name: string;
  selected: boolean;
  reasons: string[];
}

export function RouterPanel({ payload }: { payload: Record<string, unknown> }) {
  const candidates = (payload.candidates as Candidate[]) || [];
  const selected = (payload.selected as string[]) || [];
  const maxAgents = (payload.max_agents as number) ?? 2;

  return (
    <div className="panel-stack">
      <div className="panel-header">
        <h2>Router decision</h2>
        <span className="badge badge-cool">max {maxAgents} agents</span>
      </div>
      <p style={{ margin: 0, color: "var(--text-1)" }}>
        Deterministic cost control selects which diagnostic agents are worth paying for.
      </p>
      <div className="chip-row">
        {candidates.map((c) => (
          <div key={c.agent_name} className={`candidate ${c.selected ? "selected" : "dim"}`}>
            <div className="name">
              {c.agent_name}
              {c.selected ? " · selected" : ""}
            </div>
            <ul>
              {c.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="mono" style={{ color: "var(--accent-cool)", fontSize: 12 }}>
        selected: [{selected.join(", ")}]
      </div>
    </div>
  );
}
