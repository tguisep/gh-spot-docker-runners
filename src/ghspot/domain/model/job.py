"""Jobs waiting for a runner — the demand signal the scaling policy reacts to."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.target import RepositoryTarget


class WorkClass(StrEnum):
    """What kind of work a queued job is, and therefore who is waiting on it.

    Every job in a queue looks the same from the fleet's side — a set of labels and a wait.
    They are not the same to the people involved: a merge to the default branch is blocking
    everybody, and a draft pull request is blocking somebody who has not asked yet.

    Derived from the run, never configured per job. A workflow cannot be trusted to declare
    its own importance, and asking authors to would mean every workflow claiming the top.
    """

    DEFAULT_BRANCH = "default-branch"
    """A push or merge to the default branch. The build everyone else branches from: broken,
    it blocks the whole repository, and it is the one nobody can route around."""

    MANUAL = "manual"
    """`workflow_dispatch`. Somebody pressed a button and is watching the page."""

    PULL_REQUEST = "pull-request"
    """A pull request that is open for review. Someone is waiting on the result to merge."""

    BRANCH = "branch"
    """A push to any other branch, or a tag. Work in progress with no review attached."""

    DRAFT = "draft"
    """A draft pull request. The author is still writing it and has said so."""

    SCHEDULED = "scheduled"
    """`schedule`. Nobody is waiting; a nightly that runs ten minutes late is still nightly."""


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

    url: str = ""
    """Where this job lives on the forge, as the forge itself reported it.

    Taken from the API rather than assembled from the parts, because the web host and the API
    host are the same only on github.com — an Enterprise install has two, and a link built
    from the API base would point at a page that does not exist.
    """

    event: str = ""
    """What triggered the run: `push`, `pull_request`, `schedule`, `workflow_dispatch`."""

    branch: str = ""
    on_default_branch: bool = False
    """Whether `branch` is the repository's default. Resolved by the adapter, because only it
    can ask which branch that is — a pool serving two repositories serves two answers."""

    draft: bool = False
    """Whether the pull request behind this run is a draft.

    ``False`` when the run is not a pull request, and also when it could not be established —
    the listing that answers it needs a permission the daemon does not require. Not knowing
    reads as "not a draft", which is the safe way round: it ranks the job higher rather than
    quietly demoting work somebody is waiting on.
    """

    def waiting_for(self, now: datetime) -> float:
        """Seconds this job has been queued, never negative."""
        return max(0.0, (now - self.queued_at).total_seconds())

    def __str__(self) -> str:
        name = self.job_name or f"job {self.id}"
        return f"{self.repository}#{name}"
