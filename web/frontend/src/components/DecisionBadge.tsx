import { motion } from "framer-motion";

interface Comparison {
  metric: string;
  value: unknown;
  operator: string;
  threshold: unknown;
  passed: boolean;
}

export function DecisionBadge({ payload }: { payload: Record<string, unknown> }) {
  const decision = payload.decision as string;
  const decisionLine = (payload.decision_line as string) || "";
  const comparisons = (payload.comparisons as Comparison[]) || [];
  const toolName = payload.tool_name as string;
  const agentName = payload.agent_name as string;
  const must = decision === "MUST_CALL";

  return (
    <div className="panel-stack">
      <div className="panel-header">
        <h2>DECISION injection</h2>
        <span className={`badge ${must ? "badge-must" : "badge-skip"}`}>
          {must ? "MUST CALL" : "DO NOT CALL"}
        </span>
      </div>
      <div className="label">
        {agentName} · tool {toolName}
      </div>
      <motion.div
        className="decision-line"
        initial={{ opacity: 0, scale: 0.98 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4 }}
      >
        {decisionLine}
      </motion.div>
      <div className="glass glass-cool" style={{ padding: 12 }}>
        <table className="table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Value</th>
              <th>Check</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {comparisons.map((c) => (
              <tr key={c.metric}>
                <td>{c.metric}</td>
                <td>{String(c.value)}</td>
                <td>
                  {c.operator} {String(c.threshold)}
                </td>
                <td style={{ color: c.passed ? "var(--status-pass)" : "var(--status-skip)" }}>
                  {c.passed ? "pass" : "fail"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p style={{ margin: 0, color: "var(--text-1)", fontSize: 13 }}>
        Python pre-evaluates the gate and injects this line so the model never interprets the
        conditional itself.
      </p>
    </div>
  );
}
