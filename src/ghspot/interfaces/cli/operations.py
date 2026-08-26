"""Command-side controllers: the CLI actions that change something.

Each one resolves a human-typed reference to a runner, then calls a single use case. The
resolution is the only real logic, and it exists because an operator reading a table should
be able to paste back whichever column they have to hand.
"""

from __future__ import annotations

from ghspot.composition import Application, build
from ghspot.domain.errors import GhSpotError
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.infrastructure.config.settings import Settings


class RunnerNotFoundError(GhSpotError):
    """No runner in the projection matches what was asked for."""


class RunnerBusyError(GhSpotError):
    """The runner is executing a job and ``--force`` was not given."""


async def runner_logs(settings: Settings, reference: str, tail: int) -> str:
    """Container output for a runner."""
    application = build(settings)
    try:
        runner = await _resolve(application, reference)
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
        runner = await _resolve(application, reference)
        if runner.state in {RunnerState.BUSY, RunnerState.DRAINING} and not force:
            raise RunnerBusyError(
                f"{runner.name} is running job {runner.current_job_id or '(unknown)'}. "
                "Pass --force to stop it anyway."
            )
        await application.retire(runner, reason="stopped by the operator", force=force)
    finally:
        await application.aclose()


async def _resolve(application: Application, reference: str) -> Runner:
    """Find a runner by its id, its name, or its container id — whole or abbreviated.

    Operators paste whichever column is in front of them; making them work out which one the
    command wanted is friction for no benefit.
    """
    exact = await application.runners.get(RunnerId(reference))
    if exact is not None:
        return exact

    candidates = [
        runner
        for runner in await application.runners.list_active()
        if runner.name == reference
        or str(runner.id).startswith(reference)
        or (runner.container_id or "").startswith(reference)
    ]
    if not candidates:
        raise RunnerNotFoundError(
            f"no runner matching {reference!r}. Try: ghspot runner list --all"
        )
    if len(candidates) > 1:
        names = ", ".join(runner.name for runner in candidates[:5])
        raise RunnerNotFoundError(f"{reference!r} matches more than one runner: {names}")
    return candidates[0]
