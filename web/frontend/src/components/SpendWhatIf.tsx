import { useEffect, useState } from "react";
import { getWhatIf } from "../api/analytics";
import type { PricingRow, WhatIfResponse } from "../types/analytics";
import { errMsg, fmtPct, fmtUsd } from "./SpendFormat";

export function SpendWhatIf({
  pricing,
  pricingError,
  featureKeys,
  days,
}: {
  pricing: PricingRow[];
  pricingError: string | null;
  featureKeys: string[];
  days: number;
}) {
  const [model, setModel] = useState("");
  const [feature, setFeature] = useState("");
  const [res, setRes] = useState<WhatIfResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const target = model || pricing[0]?.model || "";

  useEffect(() => {
    if (!target) return;
    let alive = true;
    setBusy(true);
    setErr(null);
    getWhatIf(days, target, feature || undefined)
      .then((r) => alive && setRes(r))
      .catch((e) => alive && setErr(errMsg(e)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [target, feature, days]);

  const saves = (res?.monthly_savings_usd ?? 0) > 0;
  return (
    <section className="sp-card" aria-labelledby="sp-whatif-h">
      <h3 className="sp-card-title" id="sp-whatif-h">What if we switched model?</h3>
      <p className="sp-help">Re-prices the last {days === 1 ? "24 hours" : `${days} days`} of tokens at another model's list price.</p>
      {pricingError ? <div className="error-banner">Pricing failed to load: {pricingError}</div> : null}
      <div className="sp-form sp-form-tight">
        <label>
          Target model
          <select value={target} onChange={(e) => setModel(e.target.value)} disabled={pricing.length === 0}>
            {pricing.map((p) => (
              <option key={p.model} value={p.model}>
                {p.model} (${p.input_per_million_usd}/${p.output_per_million_usd} per 1M)
              </option>
            ))}
          </select>
        </label>
        <label>
          Feature
          <select value={feature} onChange={(e) => setFeature(e.target.value)}>
            <option value="">all features</option>
            {featureKeys.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </label>
      </div>
      {err ? <div className="error-banner">{err}</div> : null}
      {res ? (
        <div className={busy ? "sp-busy" : ""} aria-live="polite">
          <div className="sp-whatif-grid">
            <div>
              <div className="label">Actual</div>
              <div className="sp-whatif-val mono">{fmtUsd(res.actual_usd)}</div>
            </div>
            <div>
              <div className="label">Projected</div>
              <div className="sp-whatif-val mono">{fmtUsd(res.projected_usd)}</div>
            </div>
            <div>
              <div className="label">Per month</div>
              <div className={"sp-whatif-val mono " + (saves ? "sp-down" : res.monthly_savings_usd < 0 ? "sp-up" : "")}>
                {saves ? "Save " : res.monthly_savings_usd < 0 ? "Cost +" : ""}
                {fmtUsd(Math.abs(res.monthly_savings_usd))}
              </div>
              <div className="sp-dim">{fmtPct(res.savings_pct, 1)} {saves ? "cheaper" : "change"}</div>
            </div>
          </div>
          {res.rows.length > 0 ? (
            <div className="sp-scroll">
              <table className="sp-table sp-table-compact">
                <thead>
                  <tr>
                    <th>Feature / model</th>
                    <th className="sp-num">Actual</th>
                    <th className="sp-num">Projected</th>
                    <th className="sp-num">Saving</th>
                  </tr>
                </thead>
                <tbody>
                  {res.rows.slice(0, 8).map((r) => (
                    <tr key={`${r.feature_tag}|${r.model}`}>
                      <td>
                        {r.feature_tag} <span className="sp-dim mono">{r.model}</span>
                      </td>
                      <td className="sp-num mono">{fmtUsd(r.actual_usd)}</td>
                      <td className="sp-num mono">{fmtUsd(r.projected_usd)}</td>
                      <td className="sp-num mono">{fmtUsd(r.savings_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <div className="sp-dim">Pricing source: {res.pricing_source}</div>
        </div>
      ) : busy ? (
        <div className="sp-empty-inline">Calculating...</div>
      ) : null}
    </section>
  );
}
