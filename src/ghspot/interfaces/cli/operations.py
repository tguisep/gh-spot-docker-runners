"""Command-side controllers: the CLI actions that change something.

Each resolves a reference to a runner, then calls one use case. Nothing is decided here.
"""

from __future__ import annotations

import asyncio

from ghspot.application.queries.jobs import FindJobForRunner
from ghspot.application.queries.resolve import ResolveRunner
from ghspot.composition import build
from ghspot.domain.errors import RunnerBusyError
from ghspot.domain.model.runner import RunnerState
from ghspot.infrastructure.config.settings import Settings


async def runner_logs(settings: Settings, reference: str, tail: int) -> tuple[str, str]:
    """Container output for a runner, and where it came from.

    Retiring a runner removes its container, so for anything terminal the archived tail is the
    only copy there is. Returns ``(lines, source)`` where source is ``container``, ``archive``
    or ``none`` — the caller says which, because "gone forever" and "nothing yet" want
    different things from whoever is reading.
    """
    application = build(settings)
    try:
        runner = await ResolveRunner(application.runners)(reference)
        live = ""
        if runner.container_id is not None:
            live = await application.backend.logs(runner.container_id, tail=tail)
        if live.strip():
            return live, "container"

        kept = await application.runner_logs.fetch(runner.id)
        if kept is not None:
            return kept, "archive"
        return "", "none"
    finally:
        await application.aclose()


async def job_logs(settings: Settings, reference: str, tail: int) -> tuple[int | None, str | None]:
    """The forge's log for the job this runner is running.

    Returns the job id and the log, where ``None`` for the log means the forge has none yet
    — the normal state of a job still running, and different from an empty log.
    """
    application = build(settings)
    try:
        runner = await ResolveRunner(application.runners)(reference)
        job_id = await FindJobForRunner(application.forge, application.runners)(runner)
        if job_id is None:
            return None, None
        found = await application.forge.job_logs(runner.repository, job_id, tail=tail)
        return job_id, found
    finally:
        await application.aclose()


async def stop_runner(settings: Settings, reference: str, *, force: bool) -> None:
    """Retire a runner on both sides.

    A busy runner is refused without ``--force``: stopping it fails somebody's build, and the
    operator should have to say that they mean it.
    """
    application = build(settings)
    try:
        runner = await ResolveRunner(application.runners)(reference)
        if runner.state in {RunnerState.BUSY, RunnerState.DRAINING} and not force:
            raise RunnerBusyError(
                f"{runner.name} is running job {runner.current_job_id or '(unknown)'}. "
                "Pass --force to stop it anyway."
            )
        await application.retire(runner, reason="stopped by the operator", force=force)
    finally:
        await application.aclose()


async def stop_every_runner(
    settings: Settings, *, force: bool, pool: str | None = None
) -> tuple[list[str], list[str], int]:
    """Retire every runner, or every runner in one pool.

    Returns the names retired, the names refused for being busy, and how many the daemon will
    start again to satisfy `min_idle`. That last number is the point: this command empties the
    host, it does not keep it empty, and an operator who does not know that will run it twice
    and conclude it did not work.

    Concurrent, for the same reason shutdown is: each container is given its stop timeout, and
    a fleet of ten done in sequence is minutes of waiting.
    """
    application = build(settings)
    try:
        runners = [
            runner
            for runner in await application.runners.list_active()
            if not runner.is_terminal and (pool is None or runner.pool == pool)
        ]
        busy = {RunnerState.BUSY, RunnerState.DRAINING}
        refused = [r.name for r in runners if r.state in busy and not force]
        doomed = [r for r in runners if r.state not in busy or force]

        await asyncio.gather(
            *(
                application.retire(runner, reason="stopped by the operator", force=force)
                for runner in doomed
            )
        )

        # What the next tick will put back. min_idle is a floor the daemon maintains, so
        # emptying a pool that asks for one warm runner lasts exactly one poll interval.
        coming_back = sum(
            configured.spec.min_idle
            for configured in settings.pools
            if pool is None or configured.spec.name == pool
        )
        return [r.name for r in doomed], refused, coming_back
    finally:
        await application.aclose()
