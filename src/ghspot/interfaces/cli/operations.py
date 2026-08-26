"""Command-side controllers: the CLI actions that change something.

Each resolves a reference to a runner, then calls one use case. Nothing is decided here.
"""

from __future__ import annotations

from ghspot.application.queries.resolve import ResolveRunner
from ghspot.composition import build
from ghspot.domain.errors import RunnerBusyError
from ghspot.domain.model.runner import RunnerState
from ghspot.infrastructure.config.settings import Settings


async def runner_logs(settings: Settings, reference: str, tail: int) -> str:
    """Container output for a runner."""
    application = build(settings)
    try:
        runner = await ResolveRunner(application.runners)(reference)
        if runner.container_id is None:
            return ""
        return await application.backend.logs(runner.container_id, tail=tail)
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
