import { useEffect, useState } from "react";
import {
  listConnectorProfiles,
  previewConnector,
  runConnectorInvestigate,
  testConnector,
} from "../api/client";
import type { ProfileSummary } from "../types/connectors";
import type { InvestigateResponse, PreviewResponse } from "../types/investigate";

const HOUR_PRESETS = [1, 6, 24, 72];

export function RemoteConnectionForm({
  onResult,
}: {
  onResult: (result: InvestigateResponse) => void;
}) {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [profileName, setProfileName] = useState("");
  const [sinceHours, setSinceHours] = useState(24);
  const [maxRows, setMaxRows] = useState(10000);
  const [featureDefault, setFeatureDefault] = useState("");
  const [environment, setEnvironment] = useState("");
  const [label, setLabel] = useState("langfuse");
  const [provider, setProvider] = useState<"fallback" | "hf" | "groq" | "cerebras">("fallback");
  const [maxAnomalies, setMaxAnomalies] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);

  useEffect(() => {
    listConnectorProfiles()
      .then(setProfiles)
      .catch(() => setProfiles([]));
  }, []);

  function buildBody(overrides: Record<string, unknown> = {}) {
    const filters: Record<string, string> = {};
    if (environment.trim()) filters.environment = environment.trim();
    const base: Record<string, unknown> = {
      since_hours: sinceHours,
      max_rows: maxRows,
      feature_default: featureDefault.trim() || undefined,
      filters: Object.keys(filters).length ? filters : undefined,
      label: label.trim() || undefined,
      provider: provider === "fallback" ? undefined : provider,
      force_fallback: provider === "fallback",
      max_anomalies: maxAnomalies,
      ...overrides,
    };
    if (profileName) {
      base.connection = profileName;
    } else {
      base.connector = "langfuse";
    }
    return base;
  }

  async function handleTest() {
    setBusy(true);
    setError(null);
    setTestMsg(null);
    try {
      const res = await testConnector(
        buildBody({ since_hours: Math.min(sinceHours, 24), max_rows: 50 }) as never
      );
      if (!res.ok) {
        setError(res.message);
      } else {
        setTestMsg(res.message);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handlePreview() {
    setBusy(true);
    setError(null);
    try {
      const res = await previewConnector(buildBody() as never);
      setPreview(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRun() {
    setBusy(true);
    setError(null);
    try {
      const res = await runConnectorInvestigate(buildBody() as never);
      onResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="investigate-help">
        Pull GENERATION observations from Langfuse. Set{" "}
        <code>LANGFUSE_PUBLIC_KEY</code> and <code>LANGFUSE_SECRET_KEY</code> in the API
        process environment (project <code>.env</code>). Keys are never entered here.
      </p>

      <div className="investigate-grid">
        <label>
          Profile
          <select
            value={profileName}
            onChange={(e) => {
              setProfileName(e.target.value);
              if (e.target.value) {
                setLabel(`langfuse_${e.target.value}`);
              } else {
                setLabel("langfuse");
              }
            }}
          >
            <option value="">Langfuse (env defaults)</option>
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
                {p.host ? ` — ${p.host}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Since (hours)
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <select
              value={HOUR_PRESETS.includes(sinceHours) ? sinceHours : "custom"}
              onChange={(e) => {
                if (e.target.value !== "custom") {
                  setSinceHours(Number(e.target.value));
                }
              }}
            >
              {HOUR_PRESETS.map((h) => (
                <option key={h} value={h}>
                  {h}h
                </option>
              ))}
              <option value="custom">Custom</option>
            </select>
            <input
              type="number"
              min={0.1}
              max={720}
              step={0.1}
              value={sinceHours}
              onChange={(e) => setSinceHours(Number(e.target.value) || 24)}
              style={{ width: 80 }}
            />
          </div>
        </label>
        <label>
          Max rows
          <input
            type="number"
            min={1}
            max={100000}
            value={maxRows}
            onChange={(e) => setMaxRows(Number(e.target.value) || 10000)}
          />
        </label>
        <label>
          Environment filter
          <input
            type="text"
            value={environment}
            placeholder="optional"
            onChange={(e) => setEnvironment(e.target.value)}
          />
        </label>
        <label>
          Feature default
          <input
            type="text"
            value={featureDefault}
            placeholder="if rows lack feature_tag"
            onChange={(e) => setFeatureDefault(e.target.value)}
          />
        </label>
        <label>
          Label
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label>
          Provider
          <select
            value={provider}
            onChange={(e) =>
              setProvider(e.target.value as "fallback" | "hf" | "groq" | "cerebras")
            }
          >
            <option value="fallback">Fallback (offline)</option>
            <option value="groq">Groq</option>
            <option value="cerebras">Cerebras</option>
            <option value="hf">Hugging Face</option>
          </select>
        </label>
        <label>
          Max anomalies
          <input
            type="number"
            min={1}
            max={20}
            value={maxAnomalies}
            onChange={(e) => setMaxAnomalies(Number(e.target.value) || 5)}
          />
        </label>
      </div>

      <div className="investigate-actions">
        <button type="button" disabled={busy} onClick={handleTest}>
          Test connection
        </button>
        <button type="button" disabled={busy} onClick={handlePreview}>
          Preview
        </button>
        <button type="button" className="primary" disabled={busy} onClick={handleRun}>
          {busy ? "Running…" : "Run investigation"}
        </button>
      </div>

      {testMsg ? (
        <div className="ingest-banner" style={{ marginTop: 8 }}>
          {testMsg}
        </div>
      ) : null}

      {error ? <div className="error-banner">{error}</div> : null}

      {preview ? (
        <div className="ingest-banner">
          <div>
            <strong>Preview</strong> · adapter={preview.ingest.adapter} · accepted=
            {preview.ingest.accepted} · dropped={preview.ingest.dropped} · cost estimated=
            {preview.ingest.cost_estimated_count}
          </div>
          {preview.pull_meta ? (
            <div style={{ color: "var(--text-2)", marginTop: 4 }}>
              host={String(preview.pull_meta.host ?? "")} · pages=
              {String(preview.pull_meta.pages_fetched ?? "")}
            </div>
          ) : null}
          <div style={{ color: "var(--text-2)", marginTop: 4 }}>
            features: {preview.feature_tags.join(", ") || "(none)"}
          </div>
          {preview.ingest.warnings.slice(0, 5).map((w) => (
            <div key={w} style={{ fontSize: 12, marginTop: 2 }}>
              ⚠ {w}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
