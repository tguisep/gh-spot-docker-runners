"""Whether a planned launch may actually start.

`plan_scaling` answers one pool's question — how many runners *this* pool wants. Nothing
there knows about the machine underneath, so a host serving four pools can be asked for
four pools' worth of containers at once and agree to all of it.

This is the second half: a pure function from every pool's plan, plus what the host looks
like, to how many launches each pool actually gets. Two mechanisms, deliberately separate
because they fail differently:

  ceilings      Arithmetic on what is *committed* — containers, CPUs, memory promised to
                runners. Deterministic, needs no measurement, and cannot be wrong.
  backpressure  A gate on what is *measured* — the host's real load right now. Catches what
                the arithmetic cannot: everything else on the box, a job using far more than
                its pool reserved, a machine already struggling before ghspot woke up.

Priority decides who gets scarce capacity. There is no queue to persist: a pool that is
refused this tick simply wants the same thing on the next one, and the next tick re-derives
everything anyway. The queue is the reconciliation loop.

Only launches are trimmed. Retiring and terminating *release* capacity, so a host under
pressure must still be allowed to do them — refusing those would be the one thing that turns
a busy host into a stuck one.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from ghspot.domain.ports.backend import HostLoad

UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    """What the operator will let the host commit, and how hard it may be working.

    Every field is optional and unset means unlimited, so a host with no limits configured
    behaves exactly as it did before any of this existed.
    """

    max_containers: int | None = None
    """Runners across every pool. The ceiling that matters when pools have no cpus set."""

    max_cpus: float | None = None
    """Summed `cpus` of every running runner. Reserved, not measured: a pool that sets no
    cpus contributes nothing here, which is why max_containers exists too."""

    max_memory_bytes: int | None = None

    cpu_high_water: float | None = None
    """Percent. At or above it, nothing new starts until the host recovers."""

    memory_high_water: float | None = None

    @property
    def has_backpressure(self) -> bool:
        return self.cpu_high_water is not None or self.memory_high_water is not None


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """One pool asking for runners, and what each of them costs."""

    pool: str
    wanted: int
    priority: int = 0
    """Higher goes first. Ties break on the pool name, so a tick is reproducible."""

    cpus: float | None = None
    memory_bytes: int | None = None
    committed: int = 0
    """Runners this pool already has up, which the ceilings count against."""


@dataclass(frozen=True, slots=True)
class Admission:
    """How many each pool may start, and why anyone got less than they asked for."""

    granted: Mapping[str, int]
    reasons: tuple[str, ...] = field(default=())
    deferred: int = 0
    """Launches asked for and not granted. They are not lost — the next tick asks again."""

    def for_pool(self, pool: str) -> int:
        return self.granted.get(pool, 0)


def admit(
    requests: Sequence[LaunchRequest],
    load: HostLoad,
    limits: CapacityLimits,
) -> Admission:
    """Decide how much of what every pool wants the host can take."""
    granted = {request.pool: 0 for request in requests}
    wanted_total = sum(max(0, request.wanted) for request in requests)
    reasons: list[str] = []

    if wanted_total == 0:
        return Admission(granted=granted)

    held = _backpressure(load, limits)
    if held is not None:
        return Admission(granted=granted, reasons=(held,), deferred=wanted_total)

    committed = _Committed(
        containers=sum(request.committed for request in requests),
        cpus=sum(request.committed * (request.cpus or 0.0) for request in requests),
        memory=sum(request.committed * (request.memory_bytes or 0) for request in requests),
    )

    for pool in _shares(requests):
        request = pool.request
        blocked = _ceiling_reached(
            limits,
            containers=committed.containers + 1,
            cpus=committed.cpus + (request.cpus or 0.0),
            memory=committed.memory + (request.memory_bytes or 0),
        )
        if blocked is not None:
            # This pool cannot take another runner, but a cheaper one still might: a pool
            # reserving four CPUs is blocked by two remaining where a pool reserving one is
            # not. So the pool drops out and the rest carry on.
            reasons.append(
                f"[{request.pool}] held back by {blocked} "
                f"(weight {request.priority}, {pool.remaining} still wanted)"
            )
            pool.give_up()
            continue

        granted[request.pool] += 1
        pool.take()
        committed.add(request)

    deferred = wanted_total - sum(granted.values())
    return Admission(granted=granted, reasons=tuple(reasons), deferred=deferred)


@dataclass
class _Committed:
    """What the host has promised so far, as slots are handed out."""

    containers: int
    cpus: float
    memory: int

    def add(self, request: LaunchRequest) -> None:
        self.containers += 1
        self.cpus += request.cpus or 0.0
        self.memory += request.memory_bytes or 0


@dataclass
class _Share:
    """One pool's claim on the next slot."""

    request: LaunchRequest
    remaining: int
    current: int = 0
    """Running credit. The pool with the most credit takes the next slot and pays for it."""

    def take(self) -> None:
        self.remaining -= 1

    def give_up(self) -> None:
        self.remaining = 0


def _shares(requests: Sequence[LaunchRequest]) -> Iterator[_Share]:
    """Hand out slots in proportion to weight, interleaved rather than in blocks.

    Smooth weighted round robin, the algorithm nginx uses to spread requests across upstreams.
    Each round every contender gains its weight in credit, the richest takes the slot and pays
    the total back:

        weights 10 and 5, five slots →  A A B A A     (2:1, and never A A A A B)

    Interleaving is the point. Draining the heaviest pool first is what "priority" usually
    means and it starves everyone else on a busy host: a weight-5 pool would wait for a
    weight-10 pool to be completely satisfied, which on a fleet that is always busy is never.
    Here it waits its turn, which arrives in proportion to what it was given.

    A pool that stops wanting runners drops out and its share is redistributed, so weights
    describe how contention is settled rather than a fixed quota.
    """
    shares = [
        _Share(request=request, remaining=max(0, request.wanted))
        for request in sorted(requests, key=lambda item: item.pool)
    ]

    while True:
        contenders = [share for share in shares if share.remaining > 0]
        if not contenders:
            return

        total = sum(max(1, share.request.priority) for share in contenders)
        for share in contenders:
            share.current += max(1, share.request.priority)

        # Ties break on the pool name — the list is already sorted, and max() keeps the
        # first — so the same input always produces the same tick.
        winner = max(contenders, key=lambda share: share.current)
        winner.current -= total
        yield winner


def _backpressure(load: HostLoad, limits: CapacityLimits) -> str | None:
    """Whether the host is working too hard to take anything new.

    A gate rather than an allocation: when the machine is already at its limit, *which* pool
    wanted the runner does not matter, and starting the highest-priority one would still make
    things worse. Unknown readings never trip it.
    """
    if (
        limits.cpu_high_water is not None
        and load.cpu_percent is not None
        and load.cpu_percent >= limits.cpu_high_water
    ):
        return (
            f"host cpu at {load.cpu_percent:.0f}% (high water {limits.cpu_high_water:.0f}%); "
            "deferring every launch until it recovers"
        )

    used = load.memory_percent
    if (
        limits.memory_high_water is not None
        and used is not None
        and used >= limits.memory_high_water
    ):
        return (
            f"host memory at {used:.0f}% (high water {limits.memory_high_water:.0f}%); "
            "deferring every launch until it recovers"
        )

    return None


def _ceiling_reached(
    limits: CapacityLimits, *, containers: int, cpus: float, memory: int
) -> str | None:
    """Which committed ceiling one more runner would cross, if any."""
    if limits.max_containers is not None and containers > limits.max_containers:
        return f"max_containers={limits.max_containers}"
    if limits.max_cpus is not None and cpus > limits.max_cpus + 1e-9:
        return f"max_cpus={limits.max_cpus:g}"
    if limits.max_memory_bytes is not None and memory > limits.max_memory_bytes:
        return f"max_memory={_gib(limits.max_memory_bytes)}"
    return None


def _gib(count: int) -> str:
    return f"{count / 1024**3:.1f}g"
