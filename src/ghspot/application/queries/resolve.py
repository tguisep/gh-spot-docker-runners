"""Finding a runner from something a human typed.

Both the CLI and the API need this, and neither should own it. Operators paste whichever
column of the table is in front of them, so all of them are accepted.
"""

from __future__ import annotations

from ghspot.domain.errors import RunnerNotFoundError
from ghspot.domain.model.runner import Runner, RunnerId
from ghspot.domain.ports.repository import RunnerRepository


class ResolveRunner:
    """Resolve a runner id, name, or container id — whole or abbreviated."""

    def __init__(self, runners: RunnerRepository) -> None:
        self._runners = runners

    async def __call__(self, reference: str, *, include_terminal: bool = True) -> Runner:
        exact = await self._runners.get(RunnerId(reference))
        if exact is not None:
            return exact

        candidates = [
            runner
            for runner in await self._runners.list_active()
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
