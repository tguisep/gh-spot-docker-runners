from __future__ import annotations

from datetime import timedelta

import pytest

from ghspot.domain.errors import InvalidPoolSpecError, PoolAtCapacityError
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.runner import RunnerState
from ghspot.domain.model.target import RepositoryTarget
from tests.unit.conftest import REPO, make_job, make_pool, make_runner, make_spec


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"name": "Default"}, "pool name"),
        ({"name": ""}, "pool name"),
        ({"min_idle": -1}, "min_idle cannot be negative"),
        ({"max_runners": 0}, "max_runners must be at least 1"),
        ({"min_idle": 5, "max_runners": 2}, "exceeds"),
        ({"idle_timeout": timedelta(0)}, "idle_timeout must be positive"),
        ({"max_job_duration": timedelta(seconds=-1)}, "max_job_duration must be positive"),
        ({"max_launch_per_tick": 0}, "max_launch_per_tick"),
    ],
)
def test_incoherent_configuration_is_refused(overrides: dict[str, object], expected: str) -> None:
    """A bad config.toml must fail at load, not halfway through a reconciliation tick."""
    with pytest.raises(InvalidPoolSpecError, match=expected):
        make_spec(**overrides)


def test_a_pool_serves_jobs_that_match_its_repository_and_labels() -> None:
    spec = make_spec(labels=LabelSet.of("self-hosted", "linux", "x64"))

    assert spec.can_serve(make_job(labels=LabelSet.of("self-hosted", "linux")))
    assert not spec.can_serve(make_job(labels=LabelSet.of("self-hosted", "gpu")))
    assert not spec.can_serve(
        make_job(labels=LabelSet.of("self-hosted"), repository=RepositoryTarget("other", "repo"))
    )


def test_counts_distinguish_active_from_available() -> None:
    pool = make_pool(
        make_runner("a", state=RunnerState.IDLE),
        make_runner("b", state=RunnerState.STARTING),
        make_runner("c", state=RunnerState.BUSY),
        make_runner("d", state=RunnerState.RETIRED),
        max_runners=6,
    )

    assert pool.active_count == 3
    assert pool.available_count == 2
    assert pool.headroom == 3
    assert len(pool) == 4


def test_the_ceiling_is_enforced_on_admission() -> None:
    pool = make_pool(
        make_runner("a", state=RunnerState.IDLE),
        make_runner("b", state=RunnerState.BUSY),
        max_runners=2,
    )

    with pytest.raises(PoolAtCapacityError):
        pool.admit(make_runner("c", state=RunnerState.PENDING))


def test_retired_runners_do_not_hold_a_slot() -> None:
    pool = make_pool(make_runner("a", state=RunnerState.RETIRED), max_runners=1)
    pool.admit(make_runner("b", state=RunnerState.IDLE))
    assert pool.active_count == 1


def test_readmitting_a_known_runner_updates_it_rather_than_refusing() -> None:
    """Each tick re-observes the same runners; that must not trip the ceiling."""
    pool = make_pool(make_runner("a", state=RunnerState.IDLE), max_runners=1)

    pool.admit(make_runner("a", state=RunnerState.BUSY))

    assert len(pool) == 1
    known = pool.get("a")  # type: ignore[arg-type]
    assert known is not None and known.state is RunnerState.BUSY


def test_discard_is_quiet_about_unknown_runners() -> None:
    pool = make_pool(spec_name := make_runner("a"))
    pool.discard(spec_name.id)
    pool.discard(spec_name.id)
    assert len(pool) == 0
    assert pool.get(spec_name.id) is None


def test_pool_specs_carry_the_repository_they_serve() -> None:
    assert make_spec().repository == REPO


def test_a_pool_exposes_its_name_and_iterates_its_runners() -> None:
    pool = make_pool(
        make_runner("a", state=RunnerState.IDLE),
        make_runner("b", state=RunnerState.BUSY),
    )

    assert pool.name == "default"
    assert {runner.id for runner in pool} == {"a", "b"}


# ---------------------------------------------------------------- requires_labels


def test_a_pool_serves_jobs_that_never_asked_for_its_extra_labels() -> None:
    """The default, and for an ordinary pool it is what you want."""
    spec = make_spec(labels=LabelSet.of("self-hosted", "linux", "x64", "gpu-a100"))

    assert spec.can_serve(make_job(labels=LabelSet.of("self-hosted", "linux", "x64")))


def test_a_required_label_must_be_asked_for_by_name() -> None:
    """What stops a GPU being spent on work that never wanted one.

    Label matching is a subset rule, so without this a pool carrying `gpu-a100` happily takes
    a job asking only for `self-hosted, linux, x64`.
    """
    spec = make_spec(
        labels=LabelSet.of("self-hosted", "linux", "x64", "gpu-a100"),
        requires_labels=LabelSet.of("gpu-a100"),
    )

    assert not spec.can_serve(make_job(labels=LabelSet.of("self-hosted", "linux", "x64")))
    assert spec.can_serve(make_job(labels=LabelSet.of("self-hosted", "linux", "x64", "gpu-a100")))


def test_every_required_label_must_be_present_not_just_one() -> None:
    spec = make_spec(
        labels=LabelSet.of("self-hosted", "gpu-a100", "cuda-12"),
        requires_labels=LabelSet.of("gpu-a100", "cuda-12"),
    )

    assert not spec.can_serve(make_job(labels=LabelSet.of("self-hosted", "gpu-a100")))
    assert spec.can_serve(make_job(labels=LabelSet.of("self-hosted", "gpu-a100", "cuda-12")))


def test_requiring_a_label_the_pool_lacks_is_refused() -> None:
    """It could never match, so the pool would idle while its jobs queued."""
    with pytest.raises(InvalidPoolSpecError, match="could never serve"):
        make_spec(
            labels=LabelSet.of("self-hosted", "linux"),
            requires_labels=LabelSet.of("gpu-a100"),
        )


def test_a_required_label_still_respects_the_repository() -> None:
    spec = make_spec(
        labels=LabelSet.of("self-hosted", "gpu-a100"),
        requires_labels=LabelSet.of("gpu-a100"),
    )

    assert not spec.can_serve(
        make_job(
            labels=LabelSet.of("self-hosted", "gpu-a100"),
            repository=RepositoryTarget("someone", "else"),
        )
    )
