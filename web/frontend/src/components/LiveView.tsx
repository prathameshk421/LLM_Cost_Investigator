import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getLiveIncidents, setDemo } from "../api/analytics";
import type {
  BudgetEvent,
  DemoIncident,
  IngestEvent,
  LiveIncidentSummary,
  StatsEvent,
} from "../types/analytics";
import { LiveIncidents } from "./LiveIncidents";
import { LiveSpendChart, type LiveBucket } from "./LiveSpendChart";
import { errMsg, fmtNum, fmtPct, fmtUsd } from "./SpendFormat";
import { STATUS_META } from "./SpendBudgets";

type Conn = "connecting" | "live" | "error";

const BUCKET_MS = 2000;
const MAX_BUCKETS = 150; // 5 minutes
const MAX_FEATURES = 8;

interface Toast {
  id: number;
  ev: BudgetEvent;
}

function parse<T>(e: MessageEvent): T | null {
  try {
    return JSON.parse(e.data) as T;
  } catch {
    return null;
  }
}

const CONN_TEXT: Record<Conn, string> = {
  connecting: "Connecting",
  live: "Live",
  error: "Disconnected, retrying",
};

export function LiveView() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [stats, setStats] = useState<StatsEvent | null>(null);
  const [incidents, setIncidents] = useState<LiveIncidentSummary[]>([]);
  const [incError, setIncError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [tick, setTick] = useState(0);

  const bucketsRef = useRef<Map<number, Record<string, number>>>(new Map());
  const featuresRef = useRef<string[]>([]);
  const toastId = useRef(0);

  // demo control
  const [rate, setRate] = useState(20);
  const [incident, setIncident] = useState<string>("none");
  const [running, setRunning] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoErr, setDemoErr] = useState<string | null>(null);

  const addIncident = useCallback((inc: LiveIncidentSummary) => {
    setIncidents((prev) => {
      const rest = prev.filter((p) => p.incident_id !== inc.incident_id);
      return [inc, ...rest].slice(0, 100);
    });
  }, []);

  useEffect(() => {
    let alive = true;
    getLiveIncidents(50)
      .then((rows) => {
        if (!alive) return;
        setIncError(null);
        // Merge, keeping any that arrived over SSE already; newest first by end time.
        setIncidents((prev) => {
          const map = new Map<string, LiveIncidentSummary>();
          for (const r of [...rows, ...prev]) map.set(r.incident_id, r);
          return [...map.values()].sort((a, b) => (b.end || b.start).localeCompare(a.end || a.start));
        });
      })
      .catch((e) => alive && setIncError(errMsg(e)));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/stream");
    es.onopen = () => setConn("live");
    es.onerror = () => setConn("error");

    es.addEventListener("stats", (e) => {
      const d = parse<StatsEvent>(e as MessageEvent);
      if (d) {
        setStats(d);
        setConn("live");
      }
    });
    es.addEventListener("ingest", (e) => {
      const d = parse<IngestEvent>(e as MessageEvent);
      if (!d || !d.by_feature) return;
      const key = Math.floor(Date.now() / BUCKET_MS);
      const b = bucketsRef.current.get(key) ?? {};
      for (const [f, usd] of Object.entries(d.by_feature)) {
        if (!featuresRef.current.includes(f)) featuresRef.current.push(f);
        b[f] = (b[f] ?? 0) + (Number.isFinite(usd) ? usd : 0);
      }
      bucketsRef.current.set(key, b);
    });
    es.addEventListener("incident", (e) => {
      const d = parse<LiveIncidentSummary>(e as MessageEvent);
      if (d && d.incident_id) addIncident({ ...d, status: d.status ?? "open" });
    });
    es.addEventListener("budget", (e) => {
      const d = parse<BudgetEvent>(e as MessageEvent);
      if (!d) return;
      const id = ++toastId.current;
      setToasts((t) => [...t.slice(-3), { id, ev: d }]);
      window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 10000);
    });

    return () => es.close();
  }, [addIncident]);

  // Once a second: prune old buckets and redraw. Cheap: at most 150 buckets.
  useEffect(() => {
    const id = window.setInterval(() => {
      const min = Math.floor(Date.now() / BUCKET_MS) - MAX_BUCKETS;
      for (const k of bucketsRef.current.keys()) if (k <= min) bucketsRef.current.delete(k);
      setTick((t) => t + 1);
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  const { buckets, features } = useMemo(() => {
    const now = Math.floor(Date.now() / BUCKET_MS);
    const out: LiveBucket[] = [];
    for (let k = now - MAX_BUCKETS + 1; k <= now; k++) {
      out.push({ t: new Date(k * BUCKET_MS).toISOString(), by_feature: bucketsRef.current.get(k) ?? {} });
    }
    const hasData = out.some((b) => Object.keys(b.by_feature).length > 0);
    return { buckets: hasData ? out : [], features: featuresRef.current.slice(0, MAX_FEATURES) };
    // tick drives recompute
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  async function toggleDemo() {
    setDemoBusy(true);
    setDemoErr(null);
    try {
      const inc = (incident === "none" ? null : incident) as DemoIncident;
      const r = await setDemo(running ? "stop" : "start", rate, inc);
      setRunning(!!r.running);
    } catch (e) {
      setDemoErr(errMsg(e));
    } finally {
      setDemoBusy(false);
    }
  }

  return (
    <div className="sp-root lv-root">
      <div className="sp-head">
        <div>
          <h2 className="sp-title">Live</h2>
          <div className="sp-dim">Streaming ingest, spend and incidents</div>
        </div>
        <div className={`lv-conn lv-conn-${conn}`} role="status" aria-live="polite">
          <span className="lv-dot" aria-hidden="true" />
          {CONN_TEXT[conn]}
        </div>
      </div>

      <div className="lv-toasts" aria-live="polite">
        {toasts.map((t) => {
          const m = STATUS_META[t.ev.status] ?? STATUS_META.warning;
          const name = t.ev.scope === "global" ? "Global budget" : `Budget ${t.ev.scope}: ${t.ev.scope_value}`;
          return (
            <div key={t.id} className={`lv-toast sp-pill-${m.cls}`} role="alert">
              <span aria-hidden="true">{m.icon}</span>
              <span>
                {name} at {fmtPct(t.ev.pct_used)} ({m.text.toLowerCase()})
              </span>
              <button
                type="button"
                className="lv-toast-x"
                aria-label="Dismiss alert"
                onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}
              >
                {"×"}
              </button>
            </div>
          );
        })}
      </div>

      <div className="sp-tiles">
        <div className="sp-tile">
          <div className="label">Events / s</div>
          <div className="sp-tile-value mono">{stats ? stats.events_per_sec.toFixed(1) : "-"}</div>
        </div>
        <div className="sp-tile">
          <div className="label">Queue depth</div>
          <div className="sp-tile-value mono">{stats ? fmtNum(stats.queue_depth) : "-"}</div>
        </div>
        <div className="sp-tile">
          <div className="label">Total calls</div>
          <div className="sp-tile-value mono">{stats ? fmtNum(stats.total_calls) : "-"}</div>
        </div>
        <div className="sp-tile">
          <div className="label">Spend, last 5 min</div>
          <div className="sp-tile-value mono">{stats ? fmtUsd(stats.spend_last_5m_usd) : "-"}</div>
        </div>
      </div>

      <section className="sp-card" aria-labelledby="lv-chart-h">
        <h3 className="sp-card-title" id="lv-chart-h">Spend per 2 s, last 5 minutes</h3>
        <LiveSpendChart buckets={buckets} features={features} />
      </section>

      <section className="sp-card" aria-labelledby="lv-demo-h">
        <h3 className="sp-card-title" id="lv-demo-h">Demo traffic</h3>
        <div className="sp-form">
          <label>
            Rate: {rate} events/s
            <input
              type="range"
              min={5}
              max={100}
              step={5}
              value={rate}
              onChange={(e) => setRate(Number(e.target.value))}
              disabled={running}
              aria-valuetext={`${rate} events per second`}
            />
          </label>
          <label>
            Incident
            <select value={incident} onChange={(e) => setIncident(e.target.value)} disabled={running}>
              <option value="none">none</option>
              <option value="model_misroute">model_misroute</option>
              <option value="retry_loop">retry_loop</option>
              <option value="context_bloat">context_bloat</option>
            </select>
          </label>
          <button
            type="button"
            className={"btn " + (running ? "btn-warm" : "btn-primary")}
            onClick={toggleDemo}
            disabled={demoBusy}
          >
            {demoBusy ? "Working..." : running ? "Stop traffic" : "Start traffic"}
          </button>
        </div>
        <p className="sp-help" style={{ marginTop: 10 }}>
          Incident starts 45 s after start; the detector checks every 30 s.
          {running ? " Traffic is running." : ""}
        </p>
        {demoErr ? <div className="error-banner">{demoErr}</div> : null}
      </section>

      <LiveIncidents incidents={incidents} error={incError} />
    </div>
  );
}
