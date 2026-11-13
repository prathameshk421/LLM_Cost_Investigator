import type { WasteResponse } from "../types/analytics";
import { fmtNum, fmtUsd } from "./SpendFormat";

export function SpendWaste({ data, error }: { data: WasteResponse | null; error: string | null }) {
  const byFeature = data?.retry_waste_by_feature ?? [];
  const chains = data?.top_chains ?? [];
  const maxUsd = Math.max(0, ...byFeature.map((r) => r.usd));
  return (
    <section className="sp-card" aria-labelledby="sp-waste-h">
      <h3 className="sp-card-title" id="sp-waste-h">Waste</h3>
      <p className="sp-help">Money spent on repeated attempts and on long self-calling chains.</p>
      {error ? <div className="error-banner">Waste failed to load: {error}</div> : null}
      {data ? (
        <>
          <div className="sp-whatif-grid">
            <div>
              <div className="label">Retry waste</div>
              <div className="sp-whatif-val mono">{fmtUsd(data.retry_waste_usd)}</div>
            </div>
            <div>
              <div className="label">Deep chain cost</div>
              <div className="sp-whatif-val mono">{fmtUsd(data.deep_chain_cost_usd)}</div>
            </div>
          </div>

          <div className="label" style={{ marginTop: 10 }}>Retry waste by feature</div>
          {byFeature.length === 0 ? (
            <div className="sp-empty-inline">No retry waste found.</div>
          ) : (
            <ul className="sp-plain-list">
              {byFeature.slice(0, 8).map((r) => (
                <li key={r.feature_tag} className="sp-waste-row">
                  <span>{r.feature_tag}</span>
                  <span className="sp-share-track" aria-hidden="true">
                    <span className="sp-share-fill" style={{ width: `${maxUsd > 0 ? (r.usd / maxUsd) * 100 : 0}%` }} />
                  </span>
                  <span className="mono">{fmtUsd(r.usd)} · {fmtNum(r.retried_calls)} calls</span>
                </li>
              ))}
            </ul>
          )}

          <div className="label" style={{ marginTop: 10 }}>Top deep chains</div>
          {chains.length === 0 ? (
            <div className="sp-empty-inline">No deep chains found.</div>
          ) : (
            <div className="sp-scroll">
              <table className="sp-table sp-table-compact">
                <thead>
                  <tr>
                    <th>Root call</th>
                    <th>Feature</th>
                    <th className="sp-num">Depth</th>
                    <th className="sp-num">Calls</th>
                    <th className="sp-num">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {chains.slice(0, 8).map((c) => (
                    <tr key={c.root_call_id}>
                      <td className="mono sp-dim">{c.root_call_id.slice(0, 12)}</td>
                      <td>{c.feature_tag}</td>
                      <td className="sp-num mono">{c.depth}</td>
                      <td className="sp-num mono">{c.calls}</td>
                      <td className="sp-num mono">{fmtUsd(c.usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}
