"""The SQLite projection.

What matters here is that a record survives a round trip intact, and that losing the file is
survivable — the reconciler rebuilds from Docker and the forge either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ghspot.domain.errors import StorageError
from ghspot.domain.model.events import RunnerRegistered, RunnerRetired
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget
from ghspot.infrastructure.persistence.sqlite import (
    SqliteEventLog,
    SqliteRunnerRepository,
)

REPO = RepositoryTarget("tguisep", "gh-spot-docker-runners")
T0 = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path: Path) -> SqliteRunnerRepository:
    return SqliteRunnerRepository(tmp_path / "state.db")


@pytest.fixture
def events(tmp_path: Path) -> SqliteEventLog:
    return SqliteEventLog(tmp_path / "state.db")


def make_runner(runner_id: str = "r1", **overrides: object) -> Runner:
    defaults: dict[str, object] = {
        "id": RunnerId(runner_id),
        "name": f"ghspot-default-{runner_id}",
        "pool": "default",
        "repository": REPO,
        "labels": LabelSet.of("self-hosted", "linux", "x64"),
        "created_at": T0,
        "state": RunnerState.IDLE,
        "state_changed_at": T0,
        "github_runner_id": 42,
        "container_id": "c0ffee",
    }
    defaults.update(overrides)
    return Runner(**defaults)  # type: ignore[arg-type]


async def test_a_runner_survives_a_round_trip(repository: SqliteRunnerRepository) -> None:
    original = make_runner(current_job_id=777)

    await repository.save(original)
    restored = await repository.get(RunnerId("r1"))

    assert restored is not None
    assert restored.id == original.id
    assert restored.name == original.name
    assert restored.repository == REPO
    assert restored.labels.as_list() == ["self-hosted", "linux", "x64"]
    assert restored.state is RunnerState.IDLE
    assert restored.created_at == T0
    assert restored.github_runner_id == 42
    assert restored.container_id == "c0ffee"
    assert restored.current_job_id == 777


async def test_saving_the_same_runner_twice_updates_it(
    repository: SqliteRunnerRepository,
) -> None:
    """Every tick re-saves the runners it observed; this must not accumulate rows."""
    runner = make_runner()
    await repository.save(runner)

    runner.assign_job(5, at=T0)
    await repository.save(runner)

    assert len(await repository.list_for_pool("default")) == 1
    restored = await repository.get(RunnerId("r1"))
    assert restored is not None and restored.state is RunnerState.BUSY


async def test_active_runners_exclude_the_terminal_ones(
    repository: SqliteRunnerRepository,
) -> None:
    await repository.save(make_runner("live", state=RunnerState.IDLE))
    await repository.save(make_runner("done", state=RunnerState.RETIRED))
    await repository.save(make_runner("bad", state=RunnerState.FAILED))

    assert {str(r.id) for r in await repository.list_active()} == {"live"}


async def test_runners_are_listed_per_pool(repository: SqliteRunnerRepository) -> None:
    await repository.save(make_runner("a", pool="default"))
    await repository.save(make_runner("b", pool="heavy"))

    assert {str(r.id) for r in await repository.list_for_pool("heavy")} == {"b"}


async def test_getting_and_deleting_an_unknown_runner_is_quiet(
    repository: SqliteRunnerRepository,
) -> None:
    assert await repository.get(RunnerId("nobody")) is None
    await repository.delete(RunnerId("nobody"))


async def test_deleting_removes_the_record(repository: SqliteRunnerRepository) -> None:
    await repository.save(make_runner())

    await repository.delete(RunnerId("r1"))

    assert await repository.get(RunnerId("r1")) is None


async def test_pruning_keeps_recent_history_and_never_touches_live_runners(
    repository: SqliteRunnerRepository,
) -> None:
    for index in range(10):
        await repository.save(
            make_runner(
                f"old{index}",
                state=RunnerState.RETIRED,
                state_changed_at=datetime(2026, 8, 26, 12, index, tzinfo=UTC),
            )
        )
    await repository.save(make_runner("live", state=RunnerState.IDLE))

    removed = await repository.prune(keep_last=3)

    assert removed == 7
    assert len(await repository.list_for_pool("default")) == 4  # 3 kept + the live one
    assert await repository.get(RunnerId("live")) is not None


async def test_the_database_is_created_on_first_use(tmp_path: Path) -> None:
    """No install step: the daemon makes its own state directory."""
    path = tmp_path / "nested" / "deeper" / "state.db"
    repository = SqliteRunnerRepository(path)

    await repository.save(make_runner())

    assert path.exists()


async def test_a_wiped_database_costs_history_not_correctness(tmp_path: Path) -> None:
    """The claim the architecture rests on, made concrete."""
    path = tmp_path / "state.db"
    repository = SqliteRunnerRepository(path)
    await repository.save(make_runner())

    for leftover in tmp_path.glob("state.db*"):
        leftover.unlink()
    fresh = SqliteRunnerRepository(path)

    assert list(await fresh.list_active()) == []
    await fresh.save(make_runner("adopted"))
    assert len(await fresh.list_active()) == 1


# ---------------------------------------------------------------- events


async def test_events_round_trip_with_their_fields(events: SqliteEventLog) -> None:
    await events.append(
        [
            RunnerRegistered(
                occurred_at=T0,
                runner_id="r1",
                runner_name="ghspot-default-r1",
                github_runner_id=42,
                repository=REPO,
            ),
            RunnerRetired(occurred_at=T0, runner_id="r1", reason="job finished"),
        ]
    )

    recent = list(await events.recent())

    assert [type(event).__name__ for event in recent] == ["RunnerRetired", "RunnerRegistered"]
    registered = recent[1]
    assert isinstance(registered, RunnerRegistered)
    assert registered.github_runner_id == 42
    assert registered.repository == REPO
    assert registered.occurred_at == T0


async def test_appending_nothing_is_a_no_op(events: SqliteEventLog) -> None:
    await events.append([])
    assert list(await events.recent()) == []


async def test_the_event_log_doubles_as_the_publisher(events: SqliteEventLog) -> None:
    """The reconciler publishes; the store is one thing that can receive."""
    await events.publish([RunnerRetired(occurred_at=T0, runner_id="r1", reason="done")])

    assert len(list(await events.recent())) == 1


async def test_recent_is_capped(events: SqliteEventLog) -> None:
    await events.append(
        [RunnerRetired(occurred_at=T0, runner_id=f"r{n}", reason="done") for n in range(20)]
    )

    assert len(list(await events.recent(limit=5))) == 5


async def test_an_unwritable_path_says_what_to_set(tmp_path: Path) -> None:
    """The failure an operator actually hits, and the one that used to arrive as a traceback.

    Under the shipped systemd unit ProtectSystem makes most of the filesystem read-only, so
    a state_db left at its per-user default cannot be created.
    """
    unwritable = tmp_path / "locked"
    unwritable.mkdir(mode=0o500)
    repository = SqliteRunnerRepository(unwritable / "nested" / "state.db")

    with pytest.raises(StorageError, match="state_db"):
        await repository.save(make_runner())
