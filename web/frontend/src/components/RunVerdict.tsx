import type { RunMeta } from "../types/investigate";

const SHORT: Record<string, string> = {
  uncapped_retry_loop: "uncapped retry loop",
  context_bloat_self_calling_agent: "context bloat (self-calling agent)",
  expensive_model_misroute: "expensive model misroute",
  no_strong_signal: "no strong signal",
};

export function RunVerdict({ meta }: { meta: RunMeta }) {
  const graded = meta.expected !== undefined && meta.predicted !== undefined;
  return (
    <div className="verdict">
      {graded ? (
        <div className={"verdict-main " + (meta.correct ? "ok" : "bad")}>
          <span className="verdict-icon" aria-hidden>
            {meta.correct ? "✓" : "✕"}
          </span>
          <div>
            <div className="label">{meta.correct ? "Correct diagnosis" : "Wrong diagnosis"}</div>
            <div className="verdict-text">
              Predicted <strong>{SHORT[meta.predicted!] ?? meta.predicted}</strong>
              {meta.correct ? null : (
                <>
                  {" "}
                  · expected <strong>{SHORT[meta.expected!] ?? meta.expected}</strong>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
      <div className="verdict-stats">
        {meta.scenario ? <Stat k="scenario" v={`${meta.scenario} #${meta.seed}`} /> : null}
        <Stat k="pipeline" v={`${meta.elapsed_ms} ms`} />
        <Stat
          k="agents"
          v={meta.provider_requested === "fallback" ? "deterministic" : meta.provider_requested}
        />
        <Stat k="LLM calls" v={String(meta.llm_calls)} />
        <Stat k="tokens" v={meta.llm_tokens.toLocaleString()} />
        <Stat k="est. spend" v={`$${meta.llm_est_usd.toFixed(5)}`} />
      </div>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="verdict-stat">
      <div className="label">{k}</div>
      <div className="mono">{v}</div>
    </div>
  );
}
