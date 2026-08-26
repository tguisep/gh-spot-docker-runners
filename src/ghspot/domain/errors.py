"""Domain-level failures.

Every error the domain raises is a violated invariant or a rejected transition — never an
I/O failure. Adapters translate their own exceptions into these before the domain sees them.
"""

from __future__ import annotations


class GhSpotError(Exception):
    """Base class for every error this project raises."""


class DomainError(GhSpotError):
    """A domain rule was violated."""


class InvalidRepositoryTargetError(DomainError):
    """A repository reference is not a well-formed ``owner/name`` pair."""


class InvalidLabelSetError(DomainError):
    """A label set is empty or contains a label GitHub would reject."""


class InvalidPoolSpecError(DomainError):
    """A pool's configuration violates its own invariants."""


class IllegalStateTransitionError(DomainError):
    """A runner was asked to move to a state it cannot reach from its current one."""

    def __init__(self, runner_name: str, current: str, requested: str) -> None:
        super().__init__(f"runner {runner_name!r} cannot move from {current} to {requested}")
        self.runner_name = runner_name
        self.current = current
        self.requested = requested


class PoolAtCapacityError(DomainError):
    """A pool was asked to admit a runner beyond its ceiling."""

    def __init__(self, pool_name: str, max_runners: int) -> None:
        super().__init__(f"pool {pool_name!r} already holds its maximum of {max_runners} runners")
        self.pool_name = pool_name
        self.max_runners = max_runners
