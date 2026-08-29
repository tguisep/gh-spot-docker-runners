"""The port to the code forge — GitHub today, anything with the same shape tomorrow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.target import RepositoryTarget


@dataclass(frozen=True, slots=True)
class JitRegistration:
    """A minted just-in-time runner configuration.

    ``encoded_config`` is the single-use blob the runner process consumes. It is scoped to one
    runner and one job, which is what lets the container run without ever seeing a token.
    """

    github_runner_id: int
    name: str
    encoded_config: str

    def __repr__(self) -> str:
        # The blob is a credential. Keep it out of logs and tracebacks.
        return f"JitRegistration(github_runner_id={self.github_runner_id}, name={self.name!r})"


@dataclass(frozen=True, slots=True)
class ForgeRunner:
    """A runner as GitHub currently reports it."""

    id: int
    name: str
    status: str
    """``online`` or ``offline``, as GitHub words it."""

    busy: bool
    labels: LabelSet

    @property
    def is_online(self) -> bool:
        return self.status.casefold() == "online"


class ForgeClient(Protocol):
    """Everything the daemon needs from the forge.

    Implementations translate transport failures into :class:`ghspot.domain.errors.GhSpotError`
    subclasses, so callers never catch an HTTP exception.
    """

    async def create_jit_registration(
        self,
        repository: RepositoryTarget,
        name: str,
        labels: LabelSet,
        work_folder: str = "_work",
    ) -> JitRegistration:
        """Mint a just-in-time configuration for one runner."""
        ...

    async def list_runners(self, repository: RepositoryTarget) -> Sequence[ForgeRunner]:
        """Every self-hosted runner the forge currently lists for this repository."""
        ...

    async def delete_runner(self, repository: RepositoryTarget, github_runner_id: int) -> None:
        """Remove a runner registration. Deleting an unknown runner must succeed quietly."""
        ...

    async def list_queued_jobs(self, repository: RepositoryTarget) -> Sequence[QueuedJob]:
        """Jobs waiting for a runner. Implementations should use conditional requests so an
        unchanged queue costs no rate limit."""
        ...

    async def job_logs(
        self, repository: RepositoryTarget, job_id: int, tail: int = 500
    ) -> str | None:
        """The forge's own log for one job, or ``None`` while it does not have one.

        Distinct from the runner's container output, and available on a different schedule:
        GitHub writes this blob when the job **finishes**, so a job in progress has none.
        What it buys is the half the container cannot give — the log outlives the container,
        which a just-in-time runner takes with it seconds after the job ends.
        """
        ...

    async def rate_limit_reset_at(self) -> datetime | None:
        """When the current rate-limit window resets, if the forge reports one."""
        ...
