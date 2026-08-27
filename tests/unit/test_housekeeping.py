"""Reclaiming what jobs leave on the host."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ghspot.application.commands.housekeeping import ReclaimHostSpace, parse_size
from ghspot.domain.ports.backend import PruneReport, PruneRequest
from tests.fakes.adapters import FakeBackend, FakeClock
from tests.unit.conftest import T0

EVERYTHING = PruneRequest(
    containers_older_than=timedelta(hours=1),
    images_older_than=timedelta(hours=24),
    volumes=True,
    build_cache_older_than=timedelta(hours=24),
    keep_build_cache_bytes=10 * 1024**3,
)


def build(
    request: PruneRequest = EVERYTHING,
    *,
    every: timedelta = timedelta(hours=1),
    enabled: bool = True,
) -> tuple[ReclaimHostSpace, FakeBackend, FakeClock]:
    backend = FakeBackend()
    clock = FakeClock(T0)
    reclaim = ReclaimHostSpace(
        backend=backend, clock=clock, every=every, request=request, enabled=enabled
    )
    return reclaim, backend, clock


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("512m", 512 * 1024**2),
        ("10g", 10 * 1024**3),
        ("1024", 1024),
        ("2G", 2 * 1024**3),
        ("1.5g", int(1.5 * 1024**3)),
        ("100k", 100 * 1024),
    ],
)
def test_sizes_are_written_the_way_docker_writes_them(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "lots", "10x", "-5g"])
def test_a_nonsense_size_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="not a size"):
        parse_size(text)


async def test_it_runs_the_first_time_it_is_asked() -> None:
    reclaim, backend, _ = build()

    result = await reclaim()

    assert result is not None
    assert len(backend.pruned) == 1


async def test_it_does_not_run_again_until_the_interval_has_passed() -> None:
    """The interval is wall clock, not ticks, so tuning poll_interval cannot change it."""
    reclaim, backend, clock = build(every=timedelta(hours=1))

    await reclaim()
    clock.advance(minutes=59)
    assert await reclaim() is None

    clock.advance(minutes=2)
    assert await reclaim() is not None
    assert len(backend.pruned) == 2


async def test_force_ignores_the_schedule() -> None:
    reclaim, backend, _ = build()

    await reclaim()
    await reclaim(force=True)

    assert len(backend.pruned) == 2


async def test_disabled_means_disabled() -> None:
    reclaim, backend, _ = build(enabled=False)

    assert await reclaim(force=True) is None
    assert backend.pruned == []


async def test_a_request_that_would_reclaim_nothing_is_not_run() -> None:
    """An empty request is a no-op at the daemon, not a pointless round trip to Docker."""
    reclaim, backend, _ = build(PruneRequest())

    assert await reclaim(force=True) is None
    assert backend.pruned == []


async def test_a_backend_failure_is_reported_not_raised() -> None:
    """Housekeeping failing must never take the reconciliation loop down with it."""
    reclaim, backend, _ = build()
    backend.fail_on.add("prune")

    result = await reclaim()

    assert isinstance(result, PruneReport)
    assert result.errors
    assert not result.removed_anything


async def test_a_failure_still_counts_as_having_run() -> None:
    """Otherwise a broken prune is retried every tick, burying the reason it broke."""
    reclaim, backend, clock = build()
    backend.fail_on.add("prune")

    await reclaim()
    clock.advance(minutes=5)

    assert await reclaim() is None


async def test_the_request_reaches_the_backend_intact() -> None:
    reclaim, backend, _ = build()

    await reclaim()

    sent = backend.pruned[0]
    assert sent.images_older_than == timedelta(hours=24)
    assert sent.volumes is True
    assert sent.keep_build_cache_bytes == 10 * 1024**3


def test_an_empty_request_knows_it_is_empty() -> None:
    assert PruneRequest().is_noop
    assert not PruneRequest(volumes=True).is_noop
    assert not EVERYTHING.is_noop


def test_a_report_knows_whether_it_did_anything() -> None:
    assert not PruneReport().removed_anything
    assert PruneReport(images=1).removed_anything
    assert PruneReport(reclaimed_bytes=5).removed_anything
    assert not PruneReport(errors=("boom",)).removed_anything
