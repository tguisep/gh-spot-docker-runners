"""Concrete implementations of the ambient ports.

Small enough to look pointless, which is the point: because the domain asks for a clock
rather than calling ``datetime.now`` itself, a test can make a runner idle for an hour
without waiting one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ghspot.domain.model.runner import RunnerId


class SystemClock:
    """Wall-clock time, always timezone-aware and in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    """Runner identities.

    Random rather than sequential: the id becomes part of the runner's name on GitHub, and a
    name that collides with one still being torn down is rejected at registration.
    """

    def new_runner_id(self) -> RunnerId:
        return RunnerId(uuid.uuid4().hex)
