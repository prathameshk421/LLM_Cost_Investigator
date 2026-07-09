import type { ReplayStage } from "../types/replay";

export function StageRail({
  stages,
  index,
  maxReached,
  onJump,
}: {
  stages: ReplayStage[];
  index: number;
  maxReached: number;
  onJump: (i: number) => void;
}) {
  return (
    <nav className="stage-rail" aria-label="Replay stages">
      {stages.map((stage, i) => {
        const unlocked = i <= maxReached;
        const active = i === index;
        return (
          <button
            key={`${stage.id}-${stage.agent_name ?? i}`}
            type="button"
            className={`rail-item ${active ? "active" : ""} ${unlocked ? "unlocked" : "locked"}`}
            onClick={() => unlocked && onJump(i)}
            disabled={!unlocked}
          >
            <span className={`rail-dot ${stage.kind}`} />
            <span className="rail-title">{stage.title}</span>
          </button>
        );
      })}
    </nav>
  );
}
