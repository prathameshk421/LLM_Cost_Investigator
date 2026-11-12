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

export interface FlaggedWindow {
  feature_tag: string;
  start: string;
  end: string;
  hypothesis: string;
  confidence: number;
}

export interface CostTimelineData {
  window_minutes: number;
  windows: string[];
  calls_per_window: number[];
  series: Record<string, number[]>;
  flagged: FlaggedWindow[];
}

export interface RunMeta {
  elapsed_ms: number;
  llm_calls: number;
  llm_tokens: number;
  llm_est_usd: number;
  provider_requested: string;
  scenario?: string;
  seed?: number;
  noise?: number;
  expected?: string;
  target_feature?: string;
  predicted?: string;
  correct?: boolean;
}

export interface InvestigateResponse {
  ingest: IngestSummary;
  summary: InvestigationSummary;
  incidents: LiveIncident[];
  pull_meta?: Record<string, unknown> | null;
  timeline?: CostTimelineData | null;
  run_meta?: RunMeta | null;
}

export type LabScenario =
  | "retry_loop"
  | "context_bloat"
  | "model_misroute"
  | "traffic_surge"
  | "clean";

export type ProviderChoice = "fallback" | "hf" | "deploy" | "groq" | "cerebras";

export interface UsageCall {
  ts: string;
  provider: string;
  model: string;
  tools: boolean;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  est_usd: number;
  finish_reason?: string | null;
  tool_calls?: number;
  error?: string;
}

export interface UsageSnapshot {
  calls: number;
  errors: number;
  blocked: number;
  prompt_tokens: number;
  completion_tokens: number;
  spent_usd: number;
  max_calls: number;
  budget_usd: number;
  remaining_usd: number;
  log_path: string;
  recent: UsageCall[];
  providers: Record<string, { configured: boolean; key_env: string; default_model: string }>;
}

export interface BenchmarkReport {
  config: { scenarios: string[]; seeds: number; noise: number; provider: string };
  overall_accuracy: number;
  runs_total: number;
  false_alarm_rate: number;
  per_scenario: Record<
    string,
    { runs: number; correct: number; accuracy: number; predictions: Record<string, number> }
  >;
  confusion: Record<string, Record<string, number>>;
  failures: Array<Record<string, unknown>>;
}

export interface PreviewResponse {
  ingest: IngestSummary;
  feature_tags: string[];
  sample_calls: Array<Record<string, unknown>>;
  guessed_mapping?: Record<string, string> | null;
  pull_meta?: Record<string, unknown> | null;
}

export type AdapterChoice = "auto" | "jsonl" | "openai" | "csv";
