"""Read-side use cases: turn aggregates into the DTOs the interfaces render."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from ghspot.application.dto import PoolView, RunnerView
from ghspot.domain.model.pool import PoolSpec
from ghspot.domain.model.runner import Runner, RunnerState
from ghspot.domain.ports.repository import RunnerRepository
from ghspot.domain.ports.system import Clock


def to_view(runner: Runner, now: datetime) -> RunnerView:
    return RunnerView(
        id=str(runner.id),
        name=runner.name,
        pool=runner.pool,
        repository=str(runner.repository),
        state=runner.state,
        labels=runner.labels.as_list(),
        created_at=runner.created_at,
        github_runner_id=runner.github_runner_id,
        container_id=runner.container_id,
        current_job_id=runner.current_job_id,
        age_seconds=max(0.0, (now - runner.created_at).total_seconds()),
        time_in_state_seconds=runner.time_in_state(now),
        failure_reason=runner.failure_reason,
    )


class ListRunners:
    """Every runner the projection knows about, newest first."""

    def __init__(self, runners: RunnerRepository, clock: Clock) -> None:
        self._runners = runners
        self._clock = clock

    async def __call__(
        self, pool: str | None = None, *, include_terminal: bool = False
    ) -> list[RunnerView]:
        found = (
            await self._runners.list_for_pool(pool)
            if pool is not None
            else await self._runners.list_active()
        )
        now = self._clock.now()
        views = [
            to_view(runner, now) for runner in found if include_terminal or not runner.is_terminal
        ]
        return sorted(views, key=lambda view: view.created_at, reverse=True)


class GetPoolStatus:
    """Each configured pool with the runners currently in it."""

    def __init__(self, runners: RunnerRepository, clock: Clock) -> None:
        self._runners = runners
        self._clock = clock

    async def __call__(
        self,
        specs: Sequence[PoolSpec],
        queued: Mapping[str, int] | None = None,
    ) -> list[PoolView]:
        now = self._clock.now()
        counts = queued or {}
        views: list[PoolView] = []

        for spec in specs:
            live = [
                runner
                for runner in await self._runners.list_for_pool(spec.name)
                if not runner.is_terminal
            ]
            views.append(
                PoolView(
                    name=spec.name,
                    repository=str(spec.repository),
                    labels=spec.labels.as_list(),
                    min_idle=spec.min_idle,
                    max_runners=spec.max_runners,
                    idle=_count(live, RunnerState.IDLE),
                    busy=_count(live, RunnerState.BUSY, RunnerState.DRAINING),
                    starting=_count(live, RunnerState.REGISTERED, RunnerState.STARTING),
                    active=sum(1 for runner in live if runner.is_active),
                    queued_jobs=counts.get(spec.name, 0),
                    runners=[to_view(runner, now) for runner in live],
                )
            )
        return views


def _count(runners: Sequence[Runner], *states: RunnerState) -> int:
    wanted = frozenset(states)
    return sum(1 for runner in runners if runner.state in wanted)
