import type { Citation } from "../types/replay";
import { CitationHighlight } from "./CitationHighlight";
import { CountUp } from "./CountUp";

export function ExplanationPanel({ payload }: { payload: Record<string, unknown> }) {
  const agentName = payload.agent_name as string;
  const hypothesis = payload.hypothesis as string;
  const confidence = Number(payload.confidence ?? 0);
  const explanation = (payload.explanation as string) || "";
  const citations = (payload.citations as Citation[]) || [];
  const metrics = (payload.supporting_metrics as Record<string, unknown>) || {};

  return (
    <div className="panel-stack">
      <div className="panel-header">
        <h2>Agent explanation</h2>
        <span className="badge badge-warm">agentic</span>
      </div>
      <div className="chip-row">
        <span className="badge badge-warm">{hypothesis}</span>
        <span className="badge badge-cool">{agentName}</span>
        <span className="confidence-big" style={{ fontSize: 28 }}>
          <CountUp value={confidence * 100} decimals={0} />%
        </span>
      </div>
      <div className="glass glass-warm" style={{ padding: 20 }}>
        <CitationHighlight text={explanation} citations={citations} />
        {citations.length > 0 ? (
          <div style={{ marginTop: 14, fontSize: 12, color: "var(--text-1)" }}>
            <span className="cite-text">Warm</span> spans cite tool-result values (
            <span className="cite-tool" style={{ marginLeft: 4 }}>
              cool
            </span>{" "}
            in tool trace).
          </div>
        ) : null}
      </div>
      <div className="glass" style={{ padding: 16 }}>
        <div className="label" style={{ marginBottom: 8 }}>
          Supporting metrics
        </div>
        <div className="json-block">{JSON.stringify(metrics, null, 2)}</div>
      </div>
    </div>
  );
}
