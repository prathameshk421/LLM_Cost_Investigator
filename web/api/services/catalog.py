"""In-memory catalog of normalized replay incidents."""

from __future__ import annotations

import logging
from pathlib import Path

from api.models.replay import IncidentSummary, ReplayIncident

logger = logging.getLogger(__name__)

# Repo-root data/replay — fixtures live outside the package tree.
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "replay"


class IncidentCatalog:
    def __init__(self) -> None:
        self._by_id: dict[str, ReplayIncident] = {}

    def load(self, data_dir: Path | None = None) -> int:
        directory = data_dir or DEFAULT_DATA_DIR
        self._by_id.clear()
        if not directory.exists():
            logger.warning("Replay data directory missing: %s", directory)
            return 0

        for path in sorted(directory.glob("*.json")):
            try:
                incident = ReplayIncident.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                logger.error("Skipping invalid replay file %s: %s", path.name, exc)
                continue
            self._by_id[incident.id] = incident
            logger.info("Loaded replay incident: %s", incident.id)

        return len(self._by_id)

    def list_summaries(self) -> list[IncidentSummary]:
        # Main scenarios first, then thin must-call demos.
        kind_rank = {"main": 0, "thin_must_call": 1}
        items = sorted(
            self._by_id.values(),
            key=lambda i: (kind_rank.get(i.kind, 99), i.id),
        )
        return [self._to_summary(i) for i in items]

    def get(self, incident_id: str) -> ReplayIncident | None:
        return self._by_id.get(incident_id)

    def __len__(self) -> int:
        return len(self._by_id)

    @staticmethod
    def _to_summary(incident: ReplayIncident) -> IncidentSummary:
        has_tool = any(
            stage.id == "tool_trace"
            and bool((stage.payload or {}).get("calls"))
            for stage in incident.stages
        )
        return IncidentSummary(
            id=incident.id,
            title=incident.title,
            kind=incident.kind,
            feature_tag=incident.feature_tag,
            root_cause_hypothesis=incident.root_cause.hypothesis,
            has_tool_use=has_tool,
            winning_agent=incident.root_cause.winning_agent,
        )


catalog = IncidentCatalog()
