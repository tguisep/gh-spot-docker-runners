"""Command-side controllers: the CLI actions that change something.

Each resolves a reference to a runner, then calls one use case. Nothing is decided here.
"""

from __future__ import annotations

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
        if runner.current_job_id is None:
            return None, None
        found = await application.forge.job_logs(
            runner.repository, runner.current_job_id, tail=tail
        )
        return runner.current_job_id, found
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
