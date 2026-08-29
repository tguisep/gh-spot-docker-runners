"""Read-side use cases: turn aggregates into the DTOs the interfaces render."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from ghspot.application.dto import PoolView, RunnerView
from ghspot.domain.model.pool import PoolSpec
from ghspot.domain.model.runner import Runner, RunnerState
from ghspot.domain.ports.backend import RunnerBackend
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
    """Every runner the projection knows about, newest first.

    ``backend`` is optional and only used when a caller asks for resource usage: the CLI and
    the API both read this list constantly, and sampling every container each time would put
    a `docker stats` call per runner behind an operation that is otherwise a file read.
    """

    def __init__(
        self, runners: RunnerRepository, clock: Clock, backend: RunnerBackend | None = None
    ) -> None:
        self._runners = runners
        self._clock = clock
        self._backend = backend

    async def __call__(
        self,
        pool: str | None = None,
        *,
        include_terminal: bool = False,
        with_usage: bool = False,
    ) -> list[RunnerView]:
        if pool is not None:
            found = await self._runners.list_for_pool(pool)
        elif include_terminal:
            found = await self._runners.list_all()
        else:
            found = await self._runners.list_active()
        now = self._clock.now()
        views = [
            to_view(runner, now) for runner in found if include_terminal or not runner.is_terminal
        ]
        if with_usage and self._backend is not None:
            views = await _with_usage(views, self._backend)
        return sorted(views, key=lambda view: view.created_at, reverse=True)


async def _with_usage(views: list[RunnerView], backend: RunnerBackend) -> list[RunnerView]:
    """Attach a resource sample to every view whose container is still running.

    A runner without a sample keeps ``None`` rather than zero: "not measured" and "idle" are
    different facts, and a table showing 0% for a container that has exited is a lie.
    """
    running = [view.container_id for view in views if view.container_id]
    if not running:
        return views

    sampled = await backend.usage(running)
    attached: list[RunnerView] = []
    for view in views:
        usage = sampled.get(view.container_id or "")
        if usage is None:
            attached.append(view)
            continue
        attached.append(
            replace(
                view,
                cpu_percent=usage.cpu_percent,
                memory_bytes=usage.memory_bytes,
                memory_limit_bytes=usage.memory_limit_bytes,
            )
        )
    return attached


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
