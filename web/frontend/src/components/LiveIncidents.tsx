import { useState } from "react";
import { diagnoseIncident } from "../api/analytics";
import type { DiagnoseProvider, DiagnosedIncident, LiveIncidentSummary } from "../types/analytics";
import { errMsg, fmtPct, fmtTimeUtc, fmtUsd, hypothesisLabel } from "./SpendFormat";

function IncidentRow({ inc }: { inc: LiveIncidentSummary }) {
  const [busy, setBusy] = useState<DiagnoseProvider | null>(null);
  const [result, setResult] = useState<DiagnosedIncident | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [used, setUsed] = useState<DiagnoseProvider | null>(null);

  async function run(p: DiagnoseProvider) {
    setBusy(p);
    setErr(null);
    try {
      setResult(await diagnoseIncident(inc.incident_id, p));
      setUsed(p);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  const rc = result?.root_cause;
  const recs = result?.recommendations ?? [];
  return (
    <li className="lv-incident">
      <div className="lv-inc-head">
        <span className="sp-strong">{inc.feature_tag}</span>
        <span className="sp-pill sp-pill-warn">{hypothesisLabel(inc.hypothesis)}</span>
        <span className="mono">{fmtPct(inc.confidence * 100)} confidence</span>
        <span className="mono">{fmtUsd(inc.cost_usd)}</span>
        <span className="sp-dim mono">{fmtTimeUtc(inc.end || inc.start)}</span>
      </div>
      <div className="lv-inc-actions">
        <button type="button" className="btn btn-primary sp-btn-sm" disabled={busy !== null} onClick={() => run("hf")}>
          {busy === "hf" ? "Diagnosing..." : "Diagnose with LLM"}
        </button>
        <button type="button" className="btn sp-btn-sm" disabled={busy !== null} onClick={() => run("fallback")}>
          {busy === "fallback" ? "Running..." : "Deterministic"}
        </button>
        <span className="sp-dim">status {inc.status}</span>
      </div>
      {err ? <div className="error-banner" style={{ marginTop: 8 }}>{err}</div> : null}
      {rc ? (
        <div className="lv-inc-result" aria-live="polite">
          <div>
            <span className="sp-strong">{hypothesisLabel(rc.hypothesis)}</span>{" "}
            <span className="mono">{fmtPct(rc.confidence * 100)}</span>
            <span className="sp-dim">
              {" "}
              via {used === "hf" ? "LLM" : "deterministic"}
              {rc.winning_agent ? ` (${rc.winning_agent})` : ""}
            </span>
          </div>
          {recs.length > 0 ? (
            <ul className="lv-recs">
              {recs.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function LiveIncidents({ incidents, error }: { incidents: LiveIncidentSummary[]; error: string | null }) {
  return (
    <section className="sp-card" aria-labelledby="lv-inc-h">
      <h3 className="sp-card-title" id="lv-inc-h">Incident feed</h3>
      {error ? <div className="error-banner">Incidents failed to load: {error}</div> : null}
      {incidents.length === 0 && !error ? (
        <div className="sp-empty-inline">No incidents yet. The detector opens one when spend or behavior leaves its baseline.</div>
      ) : (
        <ul className="sp-plain-list">
          {incidents.map((i) => (
            <IncidentRow key={i.incident_id} inc={i} />
          ))}
        </ul>
      )}
    </section>
  );
}
