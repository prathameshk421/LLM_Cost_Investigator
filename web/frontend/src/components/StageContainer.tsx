import { AnimatePresence, motion } from "framer-motion";
import type { ReplayStage } from "../types/replay";
import { DecisionBadge } from "./DecisionBadge";
import { ExplanationPanel } from "./ExplanationPanel";
import { RootCausePanel } from "./RootCausePanel";
import { RouterPanel } from "./RouterPanel";
import { SignalsPanel } from "./SignalsPanel";
import { ToolTrace } from "./ToolTrace";

export function StageContainer({ stage, stageKey }: { stage: ReplayStage; stageKey: string }) {
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={stageKey}
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
      >
        {renderStage(stage)}
      </motion.div>
    </AnimatePresence>
  );
}

function renderStage(stage: ReplayStage) {
  switch (stage.id) {
    case "signals":
      return <SignalsPanel payload={stage.payload} />;
    case "router":
      return <RouterPanel payload={stage.payload} />;
    case "decision":
      return <DecisionBadge payload={stage.payload} />;
    case "tool_trace":
      return <ToolTrace payload={stage.payload} />;
    case "explanation":
      return <ExplanationPanel payload={stage.payload} />;
    case "root_cause":
      return <RootCausePanel payload={stage.payload} />;
    default:
      return <div className="json-block">{JSON.stringify(stage, null, 2)}</div>;
  }
}
