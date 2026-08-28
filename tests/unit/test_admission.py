"""Whether a planned launch may actually start.

The policy is pure, so every case here is arithmetic rather than a host. What is being
pinned down is the order of the two mechanisms — measured backpressure gates everything,
committed ceilings then allocate by priority — and the rule that a refusal is never a loss.
"""

from __future__ import annotations

import pytest

from ghspot.domain.policy.admission import (
    Admission,
    CapacityLimits,
    LaunchRequest,
    admit,
)
from ghspot.domain.ports.backend import HostLoad

GIB = 1024**3


def want(pool: str, wanted: int, **overrides: object) -> LaunchRequest:
    return LaunchRequest(pool=pool, wanted=wanted, **overrides)  # type: ignore[arg-type]


def granted(result: Admission) -> dict[str, int]:
    return {pool: count for pool, count in result.granted.items() if count}


# ---------------------------------------------------------------- nothing configured


def test_without_limits_everything_asked_for_is_granted() -> None:
    """A host with no limits behaves exactly as it did before any of this existed."""
    result = admit([want("a", 3), want("b", 2)], HostLoad(), CapacityLimits())

    assert granted(result) == {"a": 3, "b": 2}
    assert result.deferred == 0
    assert result.reasons == ()


def test_a_tick_with_nothing_to_launch_is_not_an_allocation() -> None:
    result = admit([want("a", 0)], HostLoad(cpu_percent=99.0), CapacityLimits(cpu_high_water=50))

    assert result.deferred == 0
    assert result.reasons == ()


# ---------------------------------------------------------------- ceilings


def test_container_count_is_capped_across_every_pool() -> None:
    """The ceiling is the host's, not each pool's — that is what max_runners already does."""
    result = admit(
        [want("a", 3, committed=1), want("b", 3, committed=1)],
        HostLoad(),
        CapacityLimits(max_containers=4),
    )

    assert sum(result.granted.values()) == 2  # two already up, four allowed
    assert result.deferred == 4


def test_reserved_cpus_are_summed_across_pools() -> None:
    result = admit(
        [want("a", 4, cpus=2.0)],
        HostLoad(),
        CapacityLimits(max_cpus=6.0),
    )

    assert granted(result) == {"a": 3}
    assert "max_cpus=6" in result.reasons[0]


def test_memory_already_committed_counts_against_the_ceiling() -> None:
    """Two runners are up at 4g each, so only one more fits under 12g."""
    result = admit(
        [want("a", 3, memory_bytes=4 * GIB, committed=2)],
        HostLoad(),
        CapacityLimits(max_memory_bytes=12 * GIB),
    )

    assert granted(result) == {"a": 1}


def test_a_pool_that_reserves_nothing_is_still_bounded_by_the_count() -> None:
    """Which is why max_containers exists alongside the resource ceilings."""
    result = admit([want("a", 5)], HostLoad(), CapacityLimits(max_cpus=1.0, max_containers=2))

    assert granted(result) == {"a": 2}


# ---------------------------------------------------------------- priority


def test_the_higher_priority_pool_is_served_first() -> None:
    result = admit(
        [want("batch", 3, priority=0), want("release", 3, priority=10)],
        HostLoad(),
        CapacityLimits(max_containers=3),
    )

    assert granted(result) == {"release": 3}
    assert result.deferred == 3


def test_what_is_left_over_falls_to_the_next_pool_down() -> None:
    result = admit(
        [want("batch", 3, priority=0), want("release", 2, priority=10)],
        HostLoad(),
        CapacityLimits(max_containers=3),
    )

    assert granted(result) == {"release": 2, "batch": 1}


def test_pools_of_equal_priority_are_ordered_by_name_so_a_tick_repeats() -> None:
    """Not fairness — determinism. The same input must produce the same tick."""
    result = admit(
        [want("zulu", 2), want("alpha", 2)], HostLoad(), CapacityLimits(max_containers=2)
    )

    assert granted(result) == {"alpha": 2}


def test_the_pool_that_was_held_back_is_named() -> None:
    result = admit(
        [want("batch", 2, priority=0), want("release", 2, priority=5)],
        HostLoad(),
        CapacityLimits(max_containers=2),
    )

    assert any("[batch]" in reason for reason in result.reasons)


# ---------------------------------------------------------------- backpressure


@pytest.mark.parametrize("cpu", [90.0, 95.5, 100.0])
def test_a_busy_host_takes_nothing_new(cpu: float) -> None:
    """Which pool wanted it does not matter: starting anything makes the host worse."""
    result = admit(
        [want("a", 2, priority=99), want("b", 1)],
        HostLoad(cpu_percent=cpu),
        CapacityLimits(cpu_high_water=90),
    )

    assert granted(result) == {}
    assert result.deferred == 3
    assert "high water" in result.reasons[0]


def test_a_host_below_the_mark_is_left_alone() -> None:
    result = admit([want("a", 2)], HostLoad(cpu_percent=89.9), CapacityLimits(cpu_high_water=90))

    assert granted(result) == {"a": 2}


def test_memory_pressure_holds_launches_too() -> None:
    result = admit(
        [want("a", 2)],
        HostLoad(memory_used_bytes=15 * GIB, memory_total_bytes=16 * GIB),
        CapacityLimits(memory_high_water=90),
    )

    assert granted(result) == {}
    assert "memory" in result.reasons[0]


def test_a_reading_the_probe_could_not_take_never_blocks() -> None:
    """A probe that cannot see the host must not be able to stop the fleet."""
    result = admit(
        [want("a", 2)],
        HostLoad(cpu_percent=None, memory_used_bytes=None),
        CapacityLimits(cpu_high_water=10, memory_high_water=10),
    )

    assert granted(result) == {"a": 2}


def test_backpressure_gates_before_the_ceilings_allocate() -> None:
    """Otherwise a high-priority pool would be granted capacity on a host already at its
    limit, which is the case the gate exists for."""
    result = admit(
        [want("a", 1, priority=100)],
        HostLoad(cpu_percent=99.0),
        CapacityLimits(cpu_high_water=90, max_containers=100),
    )

    assert granted(result) == {}
