"""Whether a planned launch may actually start.

The policy is pure, so every case here is arithmetic rather than a host. What is being
pinned down is the order of the two mechanisms — measured backpressure gates everything,
committed ceilings then allocate by priority — and the rule that a refusal is never a loss.
"""

from __future__ import annotations

from itertools import islice

import pytest

from ghspot.domain.policy.admission import (
    Admission,
    CapacityLimits,
    LaunchRequest,
    _shares,
    admit,
)
from ghspot.domain.ports.backend import HostLoad

GIB = 1024**3


def want(pool: str, wanted: int, **overrides: object) -> LaunchRequest:
    return LaunchRequest(pool=pool, wanted=wanted, **overrides)  # type: ignore[arg-type]


def granted(result: Admission) -> dict[str, int]:
    return {pool: count for pool, count in result.granted.items() if count}


def _take(shares: object, count: int) -> list:  # type: ignore[type-arg]
    """The first `count` slots the allocator hands out, taking each so the pool moves on."""
    taken = []
    for share in islice(shares, count):  # type: ignore[call-overload]
        share.take()
        taken.append(share)
    return taken


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


# ---------------------------------------------------------------- shares


def test_a_heavier_pool_gets_more_of_the_slots_but_not_all_of_them() -> None:
    """The whole point of a weight rather than a rank: 10 against 5 is two thirds, not
    everything. A pool that never starts a runner on a busy host is a pool nobody trusts."""
    result = admit(
        [want("batch", 6, priority=5), want("release", 6, priority=10)],
        HostLoad(),
        CapacityLimits(max_containers=6),
    )

    assert granted(result) == {"release": 4, "batch": 2}


def test_slots_are_interleaved_rather_than_handed_out_in_blocks() -> None:
    """Draining the heaviest pool first is what "priority" usually means, and it makes the
    lighter pool wait for the heavier one to be satisfied — on a fleet that is always busy,
    that is the same as never."""
    order = [
        share.request.pool
        for share in _take(_shares([want("a", 4, priority=10), want("b", 4, priority=5)]), 6)
    ]

    assert order == ["a", "b", "a", "a", "b", "a"]


def test_equal_weights_take_turns() -> None:
    order = [
        share.request.pool
        for share in _take(_shares([want("a", 3), want("b", 3), want("c", 3)]), 6)
    ]

    assert order == ["a", "b", "c", "a", "b", "c"]


def test_a_pool_that_stops_wanting_runners_gives_its_share_back() -> None:
    """Weights settle contention; they are not a quota held open for an idle pool."""
    result = admit(
        [want("busy", 5, priority=1), want("quiet", 1, priority=100)],
        HostLoad(),
        CapacityLimits(max_containers=4),
    )

    assert granted(result) == {"quiet": 1, "busy": 3}


def test_a_pool_too_expensive_for_what_is_left_does_not_block_a_cheaper_one() -> None:
    """Four CPUs do not fit in two remaining; one does. The fat pool drops out, the thin one
    carries on — stopping everything at the first refusal would waste the rest of the host."""
    result = admit(
        [want("fat", 2, cpus=4.0, priority=100), want("thin", 2, cpus=1.0, priority=1)],
        HostLoad(),
        CapacityLimits(max_cpus=6.0),
    )

    assert granted(result) == {"fat": 1, "thin": 2}


def test_the_pool_that_was_held_back_is_named_with_what_it_still_wants() -> None:
    result = admit(
        [want("batch", 4, priority=1), want("release", 4, priority=10)],
        HostLoad(),
        CapacityLimits(max_containers=2),
    )

    held = " ".join(result.reasons)
    assert "[batch]" in held
    assert "still wanted" in held


def test_ties_are_broken_by_name_so_a_tick_repeats() -> None:
    """Not fairness — determinism. The same input must produce the same tick."""
    first = admit([want("zulu", 1), want("alpha", 1)], HostLoad(), CapacityLimits(max_containers=1))
    again = admit([want("alpha", 1), want("zulu", 1)], HostLoad(), CapacityLimits(max_containers=1))

    assert granted(first) == granted(again) == {"alpha": 1}


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


# ---------------------------------------------------------------- the disk


def test_a_full_disk_defers_every_launch() -> None:
    """The failure that actually takes a runner host down is not a bad token or a missing
    image — it is a disk filled by build caches and pulled images, where every launch then
    fails with an error naming neither the disk nor the cause."""
    load = HostLoad(disk_used_bytes=95, disk_total_bytes=100)

    result = admit([want("a", 3)], load, CapacityLimits(disk_high_water=90))

    assert granted(result) == {}
    assert result.deferred == 3
    assert "docker filesystem 95% full" in result.reasons[0]


def test_a_disk_below_the_mark_launches_normally() -> None:
    load = HostLoad(disk_used_bytes=50, disk_total_bytes=100)

    result = admit([want("a", 3)], load, CapacityLimits(disk_high_water=90))

    assert granted(result) == {"a": 3}


def test_an_unreadable_disk_never_blocks() -> None:
    """A careful mechanism that stops the fleet when its own probe breaks is worse than no
    mechanism. Unknown degrades to the ceilings, which need no measurement."""
    result = admit([want("a", 3)], HostLoad(), CapacityLimits(disk_high_water=90))

    assert granted(result) == {"a": 3}


def test_no_mark_means_the_disk_is_not_watched() -> None:
    """Unset is unlimited, the same as every other ceiling — a host configured before this
    existed behaves exactly as it did."""
    load = HostLoad(disk_used_bytes=99, disk_total_bytes=100)

    result = admit([want("a", 3)], load, CapacityLimits())

    assert granted(result) == {"a": 3}
