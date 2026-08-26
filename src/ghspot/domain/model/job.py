"""Jobs waiting for a runner — the demand signal the scaling policy reacts to."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.target import RepositoryTarget


@dataclass(frozen=True, slots=True)
class QueuedJob:
    """A workflow job GitHub has queued and not yet assigned to a runner."""

    id: int
    run_id: int
    repository: RepositoryTarget
    labels: LabelSet
    queued_at: datetime
    workflow_name: str = ""
    job_name: str = ""

    def waiting_for(self, now: datetime) -> float:
        """Seconds this job has been queued, never negative."""
        return max(0.0, (now - self.queued_at).total_seconds())

    def __str__(self) -> str:
        name = self.job_name or f"job {self.id}"
        return f"{self.repository}#{name}"
