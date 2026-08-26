from __future__ import annotations

import pytest

from ghspot.domain.errors import IllegalStateTransitionError
from ghspot.domain.model.events import (
    RunnerCameOnline,
    RunnerRegistered,
    RunnerRetired,
    RunnerStarted,
    RunnerTookJob,
)
from ghspot.domain.model.runner import Runner, RunnerState, runner_name_for
from tests.unit.conftest import REPO, at, make_runner


def test_the_happy_path_walks_the_whole_lifecycle() -> None:
    runner = make_runner()

    runner.register(github_runner_id=42, at=at(seconds=1))
    runner.attach_container("c0ffee", at=at(seconds=2))
    runner.mark_online(at=at(seconds=5))
    runner.assign_job(777, at=at(seconds=9))
    runner.retire(at=at(minutes=3), reason="job finished")

    assert runner.state is RunnerState.RETIRED
    assert runner.github_runner_id == 42
    assert runner.container_id == "c0ffee"
    assert runner.current_job_id == 777

    kinds = [type(event) for event in runner.pull_events()]
    assert kinds == [
        RunnerRegistered,
        RunnerStarted,
        RunnerCameOnline,
        RunnerTookJob,
        RunnerRetired,
    ]


def test_events_are_taken_once() -> None:
    runner = make_runner()
    runner.register(github_runner_id=1, at=at(seconds=1))

    assert len(runner.pull_events()) == 1
    assert runner.pull_events() == []


@pytest.mark.parametrize(
    ("state", "move"),
    [
        (RunnerState.PENDING, "attach_container"),
        (RunnerState.PENDING, "mark_online"),
        (RunnerState.REGISTERED, "assign_job"),
        (RunnerState.IDLE, "register"),
        (RunnerState.RETIRED, "assign_job"),
        (RunnerState.RETIRED, "mark_online"),
        (RunnerState.BUSY, "mark_online"),
    ],
)
def test_illegal_moves_are_refused(state: RunnerState, move: str) -> None:
    """A runner that skips a step would leave an orphan on GitHub or in Docker."""
    runner = make_runner(state=state)
    argument = {"register": 1, "attach_container": "abc", "assign_job": 5}.get(move)

    with pytest.raises(IllegalStateTransitionError) as caught:
        if argument is None:
            getattr(runner, move)(at=at(seconds=1))
        else:
            getattr(runner, move)(argument, at=at(seconds=1))

    assert caught.value.current == state.value


def test_a_failed_runner_can_still_be_retired() -> None:
    """Cleanup has to be able to close out a broken runner, or it leaks forever."""
    runner = make_runner(state=RunnerState.STARTING)
    runner.fail(at=at(seconds=1), reason="image missing")

    assert runner.failure_reason == "image missing"
    runner.retire(at=at(seconds=2), reason="cleaned up")
    assert runner.state is RunnerState.RETIRED


def test_repeated_transitions_are_idempotent() -> None:
    """The reconciliation loop replays observations; seeing the same fact twice must be safe."""
    runner = make_runner(state=RunnerState.IDLE)

    runner.mark_online(at=at(seconds=1))
    runner.assign_job(5, at=at(seconds=2))
    runner.assign_job(5, at=at(seconds=3))
    runner.retire(at=at(seconds=4), reason="done")
    runner.retire(at=at(seconds=5), reason="done again")

    assert runner.state is RunnerState.RETIRED
    # mark_online on an already-idle runner records nothing: re-observing a known fact
    # must not produce a second event.
    assert [type(e).__name__ for e in runner.pull_events()] == [
        "RunnerTookJob",
        "RunnerRetired",
    ]


def test_a_different_job_on_a_busy_runner_is_refused() -> None:
    """Just-in-time runners take exactly one job; a second means our view is wrong."""
    runner = make_runner(state=RunnerState.BUSY, job_id=5)

    with pytest.raises(IllegalStateTransitionError):
        runner.assign_job(6, at=at(seconds=1))


def test_availability_and_activity() -> None:
    available = [RunnerState.REGISTERED, RunnerState.STARTING, RunnerState.IDLE]
    for state in RunnerState:
        runner = make_runner(state=state)
        assert runner.is_available is (state in available)
        assert runner.is_active is (state not in {RunnerState.RETIRED, RunnerState.FAILED})


def test_time_in_state_is_measured_from_the_last_move() -> None:
    runner = make_runner(state=RunnerState.IDLE, since=at(minutes=5))
    assert runner.idle_for(at(minutes=20)) == pytest.approx(900.0)
    assert runner.busy_for(at(minutes=20)) == 0.0

    working = make_runner(state=RunnerState.BUSY, since=at(minutes=5))
    assert working.busy_for(at(minutes=20)) == pytest.approx(900.0)
    assert working.idle_for(at(minutes=20)) == 0.0


def test_a_clock_that_went_backwards_never_yields_a_negative_age() -> None:
    runner = make_runner(state=RunnerState.IDLE, since=at(minutes=10))
    assert runner.idle_for(at(minutes=1)) == 0.0


def test_state_changed_at_defaults_to_creation() -> None:
    runner = Runner(
        id="x",  # type: ignore[arg-type]
        name="ghspot-default-x",
        pool="default",
        repository=REPO,
        labels=make_runner().labels,
        created_at=at(),
    )
    assert runner.state_changed_at == at()


def test_runner_names_are_prefixed_and_derived_from_the_id() -> None:
    name = runner_name_for("default", "abcdef0123456789")  # type: ignore[arg-type]
    assert name == "ghspot-default-abcdef012345"


@pytest.mark.parametrize("pool", ["Default", "with space", "-leading", "trailing-", "", "x" * 33])
def test_invalid_pool_names_are_refused_when_naming(pool: str) -> None:
    with pytest.raises(ValueError, match="not a valid pool name"):
        runner_name_for(pool, "abcdef0123456789")  # type: ignore[arg-type]


def test_draining_a_busy_runner_lets_it_finish() -> None:
    runner = make_runner(state=RunnerState.BUSY, job_id=5)

    runner.drain(at=at(seconds=1))
    runner.drain(at=at(seconds=2))

    assert runner.state is RunnerState.DRAINING
    assert runner.current_job_id == 5
    runner.retire(at=at(seconds=3), reason="drained")
    assert runner.is_terminal


def test_failing_twice_keeps_the_first_reason() -> None:
    runner = make_runner(state=RunnerState.STARTING)

    runner.fail(at=at(seconds=1), reason="image missing")
    runner.fail(at=at(seconds=2), reason="something else")

    assert runner.failure_reason == "image missing"
    assert runner.is_terminal


def test_only_retired_and_failed_are_terminal() -> None:
    for state in RunnerState:
        terminal = state in {RunnerState.RETIRED, RunnerState.FAILED}
        assert make_runner(state=state).is_terminal is terminal
