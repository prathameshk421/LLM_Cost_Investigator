import { useEffect, useState } from "react";
import { ScenarioSwitcher } from "./components/ScenarioSwitcher";
import { StageRail } from "./components/StageRail";
import { StageContainer } from "./components/StageContainer";
import { ReplayControls } from "./components/ReplayControls";
import { InvestigateUpload } from "./components/InvestigateUpload";
import { InvestigateResults } from "./components/InvestigateResults";
import { BenchmarkView } from "./components/BenchmarkView";
import { UsageBadge, useUsage } from "./components/UsageBadge";
import { useIncident, useIncidentList } from "./hooks/useIncident";
import { useStageProgression } from "./hooks/useStageProgression";
import type { InvestigateResponse } from "./types/investigate";

type AppMode = "replay" | "investigate" | "benchmark";

const SUBTITLE: Record<AppMode, string> = {
  investigate: "Detect a cost spike, route it to diagnostic agents, explain the root cause",
  replay: "Investigation replay · deterministic DECISION injection · tool-use centerpiece",
  benchmark: "Root-cause accuracy across seeded, noisy scenarios",
};

export default function App() {
  const [mode, setMode] = useState<AppMode>("investigate");
  const { usage, error: usageError } = useUsage();
  const [investigateResult, setInvestigateResult] =
    useState<InvestigateResponse | null>(null);

  const { items, loading: listLoading, error: listError } = useIncidentList();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { incident, loading: detailLoading, error: detailError } = useIncident(selectedId);

  useEffect(() => {
    if (!selectedId && items.length > 0) {
      const thin = items.find((i) => i.kind === "thin_must_call");
      setSelectedId(thin?.id ?? items[0].id);
    }
  }, [items, selectedId]);

  const stages = incident?.stages ?? [];
  const progression = useStageProgression(stages.length, selectedId);
  const activeStage = stages[progression.index];

  return (
    <div className={"app-shell" + (mode !== "replay" ? " mode-investigate" : "")}>
      <header className="app-header">
        <div>
          <h1>LLM Cost Investigator</h1>
          <div className="subtitle">{SUBTITLE[mode]}</div>
          <div className="mode-toggle" role="tablist">
            {(
              [
                ["investigate", "Investigate"],
                ["replay", "Replay"],
                ["benchmark", "Benchmark"],
              ] as Array<[AppMode, string]>
            ).map(([m, label]) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                className={mode === m ? "active" : ""}
                onClick={() => setMode(m)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="header-right">
        {mode === "replay" && incident ? (
          <ReplayControls
            atStart={progression.atStart}
            atEnd={progression.atEnd}
            auto={progression.auto}
            onPrev={progression.prev}
            onNext={progression.next}
            onReset={progression.reset}
            onToggleAuto={() => progression.setAuto((a) => !a)}
          />
        ) : null}
        <UsageBadge usage={usage} error={usageError} />
        </div>
      </header>

      {mode === "replay" ? (
        <>
          <aside className="sidebar">
            {listLoading ? <div className="loading">loading catalog…</div> : null}
            {listError ? <div className="error-banner">{listError}</div> : null}
            <ScenarioSwitcher
              items={items}
              selectedId={selectedId}
              onSelect={(id) => setSelectedId(id)}
            />
          </aside>

          <main className="main">
            {incident ? (
              <StageRail
                stages={stages}
                index={progression.index}
                maxReached={progression.maxReached}
                onJump={progression.jump}
              />
            ) : (
              <div />
            )}
            <section className="stage-body">
              {detailLoading ? <div className="loading">loading incident…</div> : null}
              {detailError ? <div className="error-banner">{detailError}</div> : null}
              {!detailLoading && !incident ? (
                <div className="empty-state">
                  <div>
                    <div style={{ fontSize: 16, marginBottom: 8 }}>Select a scenario</div>
                    <div style={{ color: "var(--text-2)" }}>
                      Main incidents show strong-signal skips. Thin demos force MUST CALL
                      tool use.
                    </div>
                  </div>
                </div>
              ) : null}
              {incident && activeStage ? (
                <>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      marginBottom: 16,
                      alignItems: "baseline",
                    }}
                  >
                    <div>
                      <div className="label">Incident</div>
                      <div style={{ fontWeight: 600, fontSize: 15 }}>{incident.title}</div>
                    </div>
                    <div className="mono" style={{ color: "var(--text-2)", fontSize: 11 }}>
                      stage {progression.index + 1}/{stages.length}
                      {incident.meta.provider ? ` · ${incident.meta.provider}` : ""}
                    </div>
                  </div>
                  <StageContainer
                    stage={activeStage}
                    stageKey={`${incident.id}-${progression.index}-${activeStage.id}`}
                  />
                </>
              ) : null}
            </section>
          </main>
        </>
      ) : mode === "benchmark" ? (
        <main className="main investigate-main">
          <section className="stage-body investigate-body">
            <BenchmarkView />
          </section>
        </main>
      ) : (
        <main className="main investigate-main">
          <section className="stage-body investigate-body">
            <InvestigateUpload
              onResult={setInvestigateResult}
              hfReady={Boolean(usage?.providers?.hf?.configured)}
            />
            {investigateResult ? (
              <InvestigateResults result={investigateResult} />
            ) : (
              <div className="empty-state" style={{ minHeight: 120, marginTop: 16 }}>
                <div style={{ color: "var(--text-2)" }}>
                  Pick a scenario and press Run, or upload your own telemetry.
                </div>
              </div>
            )}
          </section>
        </main>
      )}
    </div>
  );
}
