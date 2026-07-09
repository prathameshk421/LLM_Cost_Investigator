import { useEffect, useState } from "react";
import { ScenarioSwitcher } from "./components/ScenarioSwitcher";
import { StageRail } from "./components/StageRail";
import { StageContainer } from "./components/StageContainer";
import { ReplayControls } from "./components/ReplayControls";
import { useIncident, useIncidentList } from "./hooks/useIncident";
import { useStageProgression } from "./hooks/useStageProgression";

export default function App() {
  const { items, loading: listLoading, error: listError } = useIncidentList();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { incident, loading: detailLoading, error: detailError } = useIncident(selectedId);

  useEffect(() => {
    if (!selectedId && items.length > 0) {
      // Prefer a thin MUST-CALL demo as the first story beat if present.
      const thin = items.find((i) => i.kind === "thin_must_call");
      setSelectedId(thin?.id ?? items[0].id);
    }
  }, [items, selectedId]);

  const stages = incident?.stages ?? [];
  const progression = useStageProgression(stages.length, selectedId);
  const activeStage = stages[progression.index];

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>LLM Cost Investigator</h1>
          <div className="subtitle">
            Investigation replay · deterministic DECISION injection · tool-use centerpiece
          </div>
        </div>
        {incident ? (
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
      </header>

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
                  Main incidents show strong-signal skips. Thin demos force MUST CALL tool use.
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
    </div>
  );
}
