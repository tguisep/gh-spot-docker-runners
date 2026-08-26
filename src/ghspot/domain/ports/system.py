"""Ambient dependencies the domain refuses to reach for directly.

Time and identity are injected so that tests can make a runner idle for an hour without
waiting one, and so that a runner's name is reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from ghspot.domain.model.events import DomainEvent
from ghspot.domain.model.runner import RunnerId


class Clock(Protocol):
    """The current time, always timezone-aware and in UTC."""

    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    """Fresh runner identities."""

    def new_runner_id(self) -> RunnerId: ...


class EventPublisher(Protocol):
    """Where an aggregate's recorded events go once the use case is done with them."""

    async def publish(self, events: Sequence[DomainEvent]) -> None: ...
