import { useMemo, useState } from "react";
import { previewIngest, runInvestigate } from "../api/client";
import type {
  AdapterChoice,
  InvestigateResponse,
  PreviewResponse,
} from "../types/investigate";
import { CsvMappingForm } from "./CsvMappingForm";
import { RemoteConnectionForm } from "./RemoteConnectionForm";
import { SimulatePanel } from "./SimulatePanel";

type SourceMode = "simulate" | "file" | "remote";

export function InvestigateUpload({
  onResult,
  hfReady = false,
}: {
  onResult: (result: InvestigateResponse) => void;
  hfReady?: boolean;
}) {
  const [source, setSource] = useState<SourceMode>("simulate");
  const [file, setFile] = useState<File | null>(null);
  const [adapter, setAdapter] = useState<AdapterChoice>("auto");
  const [featureDefault, setFeatureDefault] = useState("");
  const [label, setLabel] = useState("");
  const [provider, setProvider] = useState<"fallback" | "hf" | "groq" | "cerebras">("fallback");
  const [maxAnomalies, setMaxAnomalies] = useState(5);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [headers, setHeaders] = useState<string[]>([]);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const showCsvMapping = useMemo(() => {
    if (adapter === "csv") return true;
    if (preview?.ingest.adapter === "csv") return true;
    if (file?.name.toLowerCase().endsWith(".csv")) return true;
    return false;
  }, [adapter, preview, file]);

  async function handlePreview() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const res = await previewIngest({
        file,
        adapter,
        featureDefault: featureDefault || undefined,
        columnMapping: Object.keys(mapping).length ? mapping : undefined,
      });
      setPreview(res);
      if (res.ingest.detected_headers?.length) {
        setHeaders(res.ingest.detected_headers);
      }
      if (res.guessed_mapping && Object.keys(mapping).length === 0) {
        setMapping(res.guessed_mapping);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRun() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const res = await runInvestigate({
        file,
        adapter,
        featureDefault: featureDefault || undefined,
        columnMapping: Object.keys(mapping).length ? mapping : undefined,
        label: label || undefined,
        provider: provider === "fallback" ? undefined : provider,
        forceFallback: provider === "fallback",
        maxAnomalies,
      });
      onResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="investigate-panel">
      <div className="label" style={{ marginBottom: 6 }}>
        Source
      </div>
      <div className="investigate-actions" style={{ marginBottom: 12 }}>
        <button
          type="button"
          className={source === "simulate" ? "primary" : undefined}
          onClick={() => setSource("simulate")}
        >
          Simulate
        </button>
        <button
          type="button"
          className={source === "file" ? "primary" : undefined}
          onClick={() => setSource("file")}
        >
          File upload
        </button>
        <button
          type="button"
          className={source === "remote" ? "primary" : undefined}
          onClick={() => setSource("remote")}
        >
          Remote (Langfuse)
        </button>
      </div>

      {source === "simulate" ? (
        <SimulatePanel onResult={onResult} hfReady={hfReady} />
      ) : source === "remote" ? (
        <RemoteConnectionForm onResult={onResult} />
      ) : (
        <>
          <div className="label" style={{ marginBottom: 6 }}>
            Upload telemetry
          </div>
          <p className="investigate-help">
            JSONL (canonical), OpenAI-style JSON, or CSV. Missing cost is estimated from a
            pricing table.
          </p>

          <input
            type="file"
            accept=".jsonl,.json,.csv,application/json,text/csv"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f);
              setPreview(null);
              setHeaders([]);
              setMapping({});
              if (f && !label) setLabel(f.name.replace(/\.[^.]+$/, ""));
            }}
          />

          <div className="investigate-grid">
            <label>
              Adapter
              <select
                value={adapter}
                onChange={(e) => setAdapter(e.target.value as AdapterChoice)}
              >
                <option value="auto">Auto</option>
                <option value="jsonl">Canonical JSONL</option>
                <option value="openai">OpenAI-style</option>
                <option value="csv">CSV</option>
              </select>
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
            <label>
              Label
              <input
                type="text"
                value={label}
                placeholder="report slug"
                onChange={(e) => setLabel(e.target.value)}
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
          </div>

          {showCsvMapping && headers.length > 0 ? (
            <CsvMappingForm headers={headers} mapping={mapping} onChange={setMapping} />
          ) : null}

          {showCsvMapping && headers.length === 0 ? (
            <p className="investigate-help">
              For CSV: click Preview first to load headers, then map columns.
            </p>
          ) : null}

          <div className="investigate-actions">
            <button type="button" disabled={!file || busy} onClick={handlePreview}>
              Preview
            </button>
            <button
              type="button"
              className="primary"
              disabled={!file || busy}
              onClick={handleRun}
            >
              {busy ? "Running…" : "Run investigation"}
            </button>
          </div>

          {error ? <div className="error-banner">{error}</div> : null}

          {preview ? (
            <div className="ingest-banner">
              <div>
                <strong>Preview</strong> · adapter={preview.ingest.adapter} · accepted=
                {preview.ingest.accepted} · dropped={preview.ingest.dropped} · cost
                estimated={preview.ingest.cost_estimated_count}
              </div>
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
        </>
      )}
    </div>
  );
}
