import type { Driver, DriversResponse } from "../types/analytics";
import { fmtPct, fmtSigned, fmtUsd } from "./SpendFormat";

function Effect({ label, v, max }: { label: string; v: number; max: number }) {
  const w = max > 0 ? Math.min(100, (Math.abs(v) / max) * 100) : 0;
  const up = v > 0;
  const arrow = v > 0 ? "▲" : v < 0 ? "▼" : "–";
  return (
    <div className="sp-effect" title={`${label} effect: ${fmtSigned(v)}`}>
      <span className="sp-effect-label">{label}</span>
      <span className="sp-effect-track" aria-hidden="true">
        <span className="sp-effect-mid" />
        <span
          className={"sp-effect-fill " + (up ? "up" : "down")}
          style={up ? { left: "50%", width: `${w / 2}%` } : { right: "50%", width: `${w / 2}%` }}
        />
      </span>
      <span className="sp-effect-val mono">
        {arrow} {fmtSigned(v)}
      </span>
    </div>
  );
}

function DriverRow({ d }: { d: Driver }) {
  const max = Math.max(Math.abs(d.volume_effect_usd), Math.abs(d.tokens_effect_usd), Math.abs(d.price_effect_usd));
  const share = Math.max(0, Math.min(100, Math.abs(d.share_of_delta) * 100));
  return (
    <tr>
      <td>
        <div className="sp-strong">{d.feature_tag}</div>
        <div className="sp-dim mono">{d.model}</div>
      </td>
      <td className="sp-num mono">
        {fmtUsd(d.prev_usd)} <span aria-hidden="true">{"→"}</span> {fmtUsd(d.curr_usd)}
        <div className={"sp-delta " + (d.delta_usd > 0 ? "up" : d.delta_usd < 0 ? "down" : "")}>
          {d.delta_usd > 0 ? "▲" : d.delta_usd < 0 ? "▼" : ""} {fmtSigned(d.delta_usd)}
        </div>
      </td>
      <td>
        <div className="sp-share">
          <span className="sp-share-track" aria-hidden="true">
            <span className="sp-share-fill" style={{ width: `${share}%` }} />
          </span>
          <span className="mono">{fmtPct(d.share_of_delta * 100)}</span>
        </div>
      </td>
      <td className="sp-effects">
        <Effect label="Volume" v={d.volume_effect_usd} max={max} />
        <Effect label="Tokens/call" v={d.tokens_effect_usd} max={max} />
        <Effect label="Price" v={d.price_effect_usd} max={max} />
      </td>
    </tr>
  );
}

export function SpendDrivers({ data, error, days }: { data: DriversResponse | null; error: string | null; days: number }) {
  const rows = data?.drivers ?? [];
  const total = data?.total_delta_usd ?? 0;
  return (
    <section className="sp-card" aria-labelledby="sp-drivers-h">
      <h3 className="sp-card-title" id="sp-drivers-h">Why did spend change?</h3>
      <p className="sp-help">
        Last {days === 1 ? "24 hours" : `${days} days`} against the period before it, by feature and model.
        Each change splits into volume (more or fewer calls), tokens per call (bigger or smaller prompts) and price (a different model rate).
        The three effects add up to the change.
      </p>
      {error ? <div className="error-banner">Drivers failed to load: {error}</div> : null}
      {data ? (
        <div className="sp-driver-total">
          Total change <strong className={"mono " + (total > 0 ? "sp-up" : total < 0 ? "sp-down" : "")}>{fmtSigned(total)}</strong>
        </div>
      ) : null}
      {rows.length === 0 && !error ? (
        <div className="sp-empty-inline">No change between the two periods.</div>
      ) : (
        <div className="sp-scroll">
          <table className="sp-table">
            <thead>
              <tr>
                <th>Feature / model</th>
                <th className="sp-num">Before {"→"} now</th>
                <th>Share of change</th>
                <th>What drove it</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <DriverRow key={`${d.feature_tag}|${d.model}`} d={d} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
