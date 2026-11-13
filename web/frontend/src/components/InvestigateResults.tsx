import { useEffect, useState } from "react";
import type { InvestigateResponse, LiveIncident } from "../types/investigate";
import { useStageProgression } from "../hooks/useStageProgression";
import { StageRail } from "./StageRail";
import { StageContainer } from "./StageContainer";
import { ReplayControls } from "./ReplayControls";
import { CostTimeline } from "./CostTimeline";
import { RunVerdict } from "./RunVerdict";

export function InvestigateResults({ result }: { result: InvestigateResponse }) {
  const [selectedId, setSelectedId] = useState<string | null>(
    result.incidents[0]?.id ?? null
  );

  useEffect(() => {
    setSelectedId(result.incidents[0]?.id ?? null);
  }, [result]);

  const incident: LiveIncident | undefined = result.incidents.find(
    (i) => i.id === selectedId
  );
  const stages = incident?.stages ?? [];
  const progression = useStageProgression(stages.length, selectedId);
  const activeStage = stages[progression.index];

  return (
    <div className="investigate-results">
      {result.run_meta ? <RunVerdict meta={result.run_meta} /> : null}
      {result.timeline && result.timeline.windows.length > 1 ? (
        <CostTimeline data={result.timeline} />
      ) : null}
      <div className="ingest-banner">
        <div>
          <strong>Ingest</strong> · {result.ingest.adapter} · accepted=
          {result.ingest.accepted} · dropped={result.ingest.dropped} · cost estimated=
          {result.ingest.cost_estimated_count}
        </div>
        <div style={{ marginTop: 4 }}>
          <strong>Summary</strong> · calls={result.summary.call_count} · anomalies=
          {result.summary.anomaly_count} · features=
          {result.summary.feature_tags.join(", ") || "(none)"}
        </div>
        {result.ingest.warnings.slice(0, 6).map((w) => (
          <div key={w} style={{ fontSize: 12, marginTop: 2 }}>
            ⚠ {w}
          </div>
        ))}
      </div>

      {result.incidents.length === 0 ? (
        <div className="empty-state" style={{ minHeight: 160 }}>
          <div>
            <div style={{ fontSize: 16, marginBottom: 8 }}>No cost anomalies detected</div>
            <div style={{ color: "var(--text-2)" }}>
              {result.summary.no_anomaly_reason ||
                "Telemetry ingested successfully; detector found no anomalous windows."}
            </div>
          </div>
        </div>
      ) : (
        <div className="investigate-results-body">
          <aside className="anomaly-list">
            <div className="label" style={{ marginBottom: 8 }}>
              Anomalies
            </div>
            {result.incidents.map((inc) => (
              <button
                key={inc.id}
                type="button"
                className={
                  "anomaly-item" + (inc.id === selectedId ? " active" : "")
                }
                onClick={() => setSelectedId(inc.id)}
              >
                <div style={{ fontWeight: 600 }}>{inc.feature_tag}</div>
                <div className="mono" style={{ fontSize: 11, color: "var(--text-2)" }}>
                  {inc.root_cause.hypothesis} ·{" "}
                  {(inc.root_cause.confidence * 100).toFixed(0)}%
                </div>
              </button>
            ))}
          </aside>

          <div className="investigate-stage-host">
            {incident ? (
              <>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: 12,
                    gap: 12,
                  }}
                >
                  <div>
                    <div className="label">Incident</div>
                    <div style={{ fontWeight: 600 }}>{incident.title}</div>
                  </div>
                  <ReplayControls
                    atStart={progression.atStart}
                    atEnd={progression.atEnd}
                    auto={progression.auto}
                    onPrev={progression.prev}
                    onNext={progression.next}
                    onReset={progression.reset}
                    onToggleAuto={() => progression.setAuto((a) => !a)}
                  />
                </div>
                <StageRail
                  stages={stages}
                  index={progression.index}
                  maxReached={progression.maxReached}
                  onJump={progression.jump}
                />
                {activeStage ? (
                  <StageContainer
                    stage={activeStage}
                    stageKey={`${incident.id}-${progression.index}-${activeStage.id}`}
                  />
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
