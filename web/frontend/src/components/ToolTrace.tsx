import { motion } from "framer-motion";

interface ToolCall {
  name: string;
  args: Record<string, unknown>;
}

export function ToolTrace({ payload }: { payload: Record<string, unknown> }) {
  const toolRequired = Boolean(payload.tool_required);
  const calls = (payload.calls as ToolCall[]) || [];
  const result = payload.result as Record<string, unknown>[] | null;
  const summary = payload.result_summary as string | null;
  const toolName = (payload.tool_name as string) || "tool";

  if (!toolRequired || calls.length === 0) {
    return (
      <div className="panel-stack">
        <div className="panel-header">
          <h2>Tool trace</h2>
          <span className="badge badge-skip">skipped</span>
        </div>
        <div className="glass" style={{ padding: 20, color: "var(--text-1)" }}>
          {summary || "Tool skipped — aggregate signals sufficient."}
          <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 12 }}>
            gate said DO NOT CALL · no {toolName} request issued
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="panel-stack">
      <div className="panel-header">
        <h2>Tool trace</h2>
        <span className="badge badge-cool">tool use</span>
      </div>

      <div className="glass glass-cool" style={{ padding: 16 }}>
        <div className="label" style={{ marginBottom: 8 }}>
          Request
        </div>
        {calls.map((c, i) => (
          <div key={i} className="mono" style={{ color: "var(--accent-cool)", fontSize: 13 }}>
            {c.name}({Object.entries(c.args)
              .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
              .join(", ")}
            )
          </div>
        ))}
      </div>

      <div className="glass glass-cool" style={{ padding: 16 }}>
        <div className="label" style={{ marginBottom: 8 }}>
          Result {summary ? `· ${summary}` : ""}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {(result || []).map((row, i) => (
            <motion.div
              key={i}
              className="json-block"
              style={{ maxHeight: "none" }}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05, duration: 0.2 }}
            >
              {JSON.stringify(row, null, 2)}
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  );
}
