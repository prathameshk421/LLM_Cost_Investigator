import type { ReplayIncident, ReplayStage } from "./replay";

export interface IngestSummary {
  adapter: string;
  accepted: number;
  dropped: number;
  warnings: string[];
  cost_estimated_count: number;
  sample_dropped: Array<Record<string, unknown>>;
  detected_headers?: string[] | null;
}

export interface InvestigationSummary {
  label: string;
  call_count: number;
  feature_tags: string[];
  anomaly_count: number;
  no_anomaly_reason?: string | null;
}

export interface LiveIncident extends Omit<ReplayIncident, "kind"> {
  kind: string;
  stages: ReplayStage[];
  raw_report?: Record<string, unknown> | null;
}

export interface InvestigateResponse {
  ingest: IngestSummary;
  summary: InvestigationSummary;
  incidents: LiveIncident[];
  pull_meta?: Record<string, unknown> | null;
}

export interface PreviewResponse {
  ingest: IngestSummary;
  feature_tags: string[];
  sample_calls: Array<Record<string, unknown>>;
  guessed_mapping?: Record<string, string> | null;
  pull_meta?: Record<string, unknown> | null;
}

export type AdapterChoice = "auto" | "jsonl" | "openai" | "csv";
