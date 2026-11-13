import { useEffect, useState } from "react";
import { getUsage } from "../api/client";
import type { UsageSnapshot } from "../types/investigate";

export function useUsage(pollMs = 5000) {
  const [usage, setUsage] = useState<UsageSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () =>
      getUsage()
        .then((u) => {
          if (alive) {
            setUsage(u);
            setError(null);
          }
        })
        .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    tick();
    const id = window.setInterval(tick, pollMs);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [pollMs]);
  return { usage, error };
}

export function UsageBadge({ usage, error }: { usage: UsageSnapshot | null; error: string | null }) {
  const [open, setOpen] = useState(false);
  if (error) {
    return <div className="usage-badge usage-down">API offline</div>;
  }
  if (!usage) return null;
  const pct = Math.min(100, (usage.spent_usd / Math.max(usage.budget_usd, 1e-9)) * 100);
  const warn = pct >= 80;
  return (
    <div className="usage-wrap">
      <button
        type="button"
        className={"usage-badge" + (warn ? " usage-warn" : "")}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title="Live LLM spend in this API process"
      >
        <span className="label" style={{ letterSpacing: "0.04em" }}>LLM budget</span>
        <span className="mono">
          ${usage.spent_usd.toFixed(4)} / ${usage.budget_usd.toFixed(2)}
        </span>
        <span className="usage-meter" aria-hidden>
          <span style={{ width: `${pct}%` }} />
        </span>
        <span className="mono" style={{ color: "var(--text-2)" }}>
          {usage.calls}/{usage.max_calls} calls
        </span>
      </button>
      {open ? (
        <div className="usage-pop">
          <div className="usage-grid">
            <div><div className="label">Tokens in</div><div className="mono">{usage.prompt_tokens.toLocaleString()}</div></div>
            <div><div className="label">Tokens out</div><div className="mono">{usage.completion_tokens.toLocaleString()}</div></div>
            <div><div className="label">Errors</div><div className="mono">{usage.errors}</div></div>
            <div><div className="label">Blocked by budget</div><div className="mono">{usage.blocked}</div></div>
          </div>
          <div className="label" style={{ margin: "12px 0 6px" }}>Providers</div>
          {Object.entries(usage.providers).map(([name, p]) => (
            <div key={name} className="usage-provider">
              <span className={"status-dot " + (p.configured ? "ok" : "off")} aria-hidden />
              <span>{name}</span>
              <span className="mono" style={{ color: "var(--text-2)" }}>
                {p.configured ? p.default_model : `set ${p.key_env}`}
              </span>
            </div>
          ))}
          <div className="label" style={{ margin: "12px 0 6px" }}>
            Recent calls · log: <span className="mono" style={{ textTransform: "none" }}>{usage.log_path}</span>
          </div>
          {usage.recent.length === 0 ? (
            <div style={{ color: "var(--text-2)", fontSize: 12 }}>No live calls yet.</div>
          ) : (
            <table className="table usage-table">
              <thead>
                <tr><th>time</th><th>model</th><th>tok in/out</th><th>ms</th><th>$</th><th></th></tr>
              </thead>
              <tbody>
                {usage.recent.slice(0, 12).map((c, i) => (
                  <tr key={c.ts + i}>
                    <td className="mono">{c.ts.slice(11, 19)}</td>
                    <td className="mono">{c.model}</td>
                    <td className="mono">{c.prompt_tokens}/{c.completion_tokens}</td>
                    <td className="mono">{c.latency_ms}</td>
                    <td className="mono">{c.est_usd.toFixed(5)}</td>
                    <td>{c.error ? <span title={c.error} style={{ color: "var(--status-fail)" }}>error</span> : c.tools ? (c.tool_calls ? `tool×${c.tool_calls}` : "tools") : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  );
}
