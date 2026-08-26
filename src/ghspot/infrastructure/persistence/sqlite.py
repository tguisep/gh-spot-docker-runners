"""SQLite storage.

This is a *projection*, not the truth. The reconciler rebuilds the fleet from Docker and the
forge on every tick, so a lost or rolled-back database costs history and nothing else — the
next tick adopts the containers back. That is why there is no locking here, and why a write
failure is never allowed to abort a tick.

The SDK is synchronous, so calls run on a worker thread; the connection is created per
operation, which is cheap for SQLite and avoids threading a single connection across them.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ghspot.domain.model import events as domain_events
from ghspot.domain.model.events import DomainEvent
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runners (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    pool              TEXT NOT NULL,
    repository        TEXT NOT NULL,
    labels            TEXT NOT NULL,
    state             TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    state_changed_at  TEXT NOT NULL,
    github_runner_id  INTEGER,
    container_id      TEXT,
    current_job_id    INTEGER,
    failure_reason    TEXT
);
CREATE INDEX IF NOT EXISTS runners_by_pool ON runners (pool);
CREATE INDEX IF NOT EXISTS runners_by_state ON runners (state);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    kind        TEXT NOT NULL,
    runner_id   TEXT,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_time ON events (occurred_at DESC);
"""


class SqliteStore:
    """Owns the file and the schema. The repositories below share one."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self._prepared = False

    def prepare(self) -> None:
        """Create the database and bring the schema up to date. Safe to call repeatedly."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._prepared = True

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        # WAL lets the CLI read while the daemon writes, which is the whole point of having
        # the projection on disk rather than in the daemon's memory.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    async def _run(self, work: Any) -> Any:
        if not self._prepared:
            await asyncio.to_thread(self.prepare)

        def call() -> Any:
            with self.connect() as connection:
                return work(connection)

        return await asyncio.to_thread(call)


class SqliteRunnerRepository(SqliteStore):
    """A :class:`~ghspot.domain.ports.repository.RunnerRepository` on SQLite."""

    async def save(self, runner: Runner) -> None:
        row = _to_row(runner)

        def work(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO runners (
                    id, name, pool, repository, labels, state, created_at, state_changed_at,
                    github_runner_id, container_id, current_job_id, failure_reason
                ) VALUES (
                    :id, :name, :pool, :repository, :labels, :state, :created_at,
                    :state_changed_at, :github_runner_id, :container_id, :current_job_id,
                    :failure_reason
                )
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    pool = excluded.pool,
                    repository = excluded.repository,
                    labels = excluded.labels,
                    state = excluded.state,
                    state_changed_at = excluded.state_changed_at,
                    github_runner_id = excluded.github_runner_id,
                    container_id = excluded.container_id,
                    current_job_id = excluded.current_job_id,
                    failure_reason = excluded.failure_reason
                """,
                row,
            )

        await self._run(work)

    async def get(self, runner_id: RunnerId) -> Runner | None:
        def work(connection: sqlite3.Connection) -> Runner | None:
            row = connection.execute(
                "SELECT * FROM runners WHERE id = ?", (str(runner_id),)
            ).fetchone()
            return _from_row(row) if row else None

        result: Runner | None = await self._run(work)
        return result

    async def list_active(self) -> Sequence[Runner]:
        terminal = (RunnerState.RETIRED.value, RunnerState.FAILED.value)

        def work(connection: sqlite3.Connection) -> list[Runner]:
            rows = connection.execute(
                "SELECT * FROM runners WHERE state NOT IN (?, ?) ORDER BY created_at", terminal
            ).fetchall()
            return [_from_row(row) for row in rows]

        result: list[Runner] = await self._run(work)
        return result

    async def list_all(self) -> Sequence[Runner]:
        def work(connection: sqlite3.Connection) -> list[Runner]:
            rows = connection.execute("SELECT * FROM runners ORDER BY created_at").fetchall()
            return [_from_row(row) for row in rows]

        result: list[Runner] = await self._run(work)
        return result

    async def list_for_pool(self, pool: str) -> Sequence[Runner]:
        def work(connection: sqlite3.Connection) -> list[Runner]:
            rows = connection.execute(
                "SELECT * FROM runners WHERE pool = ? ORDER BY created_at", (pool,)
            ).fetchall()
            return [_from_row(row) for row in rows]

        result: list[Runner] = await self._run(work)
        return result

    async def delete(self, runner_id: RunnerId) -> None:
        def work(connection: sqlite3.Connection) -> None:
            connection.execute("DELETE FROM runners WHERE id = ?", (str(runner_id),))

        await self._run(work)

    async def prune(self, keep_last: int = 500) -> int:
        """Drop the oldest terminal records, keeping recent history for the CLI."""

        def work(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                """
                DELETE FROM runners
                WHERE state IN ('retired', 'failed')
                  AND id NOT IN (
                      SELECT id FROM runners
                      WHERE state IN ('retired', 'failed')
                      ORDER BY state_changed_at DESC LIMIT ?
                  )
                """,
                (keep_last,),
            )
            return cursor.rowcount or 0

        result: int = await self._run(work)
        return result


class SqliteEventLog(SqliteStore):
    """Append-only history, for post-mortems and ``ghspot history``."""

    async def append(self, events: Sequence[DomainEvent]) -> None:
        if not events:
            return
        rows = [_event_row(event) for event in events]

        def work(connection: sqlite3.Connection) -> None:
            connection.executemany(
                "INSERT INTO events (occurred_at, kind, runner_id, payload) "
                "VALUES (:occurred_at, :kind, :runner_id, :payload)",
                rows,
            )

        await self._run(work)

    async def recent(self, limit: int = 100) -> Sequence[DomainEvent]:
        def work(connection: sqlite3.Connection) -> list[DomainEvent]:
            rows = connection.execute(
                "SELECT occurred_at, kind, payload FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [event for event in (_event_from_row(row) for row in rows) if event]

        result: list[DomainEvent] = await self._run(work)
        return result

    async def publish(self, events: Sequence[DomainEvent]) -> None:
        """Also satisfies :class:`~ghspot.domain.ports.system.EventPublisher`."""
        await self.append(events)


# -- mapping -------------------------------------------------------------------------


def _to_row(runner: Runner) -> dict[str, Any]:
    assert runner.state_changed_at is not None
    return {
        "id": str(runner.id),
        "name": runner.name,
        "pool": runner.pool,
        "repository": str(runner.repository),
        "labels": json.dumps(runner.labels.as_list()),
        "state": runner.state.value,
        "created_at": runner.created_at.isoformat(),
        "state_changed_at": runner.state_changed_at.isoformat(),
        "github_runner_id": runner.github_runner_id,
        "container_id": runner.container_id,
        "current_job_id": runner.current_job_id,
        "failure_reason": runner.failure_reason,
    }


def _from_row(row: sqlite3.Row) -> Runner:
    return Runner(
        id=RunnerId(row["id"]),
        name=row["name"],
        pool=row["pool"],
        repository=RepositoryTarget.parse(row["repository"]),
        labels=LabelSet.from_iterable(json.loads(row["labels"])),
        created_at=_time(row["created_at"]),
        state=RunnerState(row["state"]),
        state_changed_at=_time(row["state_changed_at"]),
        github_runner_id=row["github_runner_id"],
        container_id=row["container_id"],
        current_job_id=row["current_job_id"],
        failure_reason=row["failure_reason"],
    )


def _event_row(event: DomainEvent) -> dict[str, Any]:
    payload = {
        field.name: _encode(getattr(event, field.name))
        for field in dataclass_fields(event)
        if field.name != "occurred_at"
    }
    return {
        "occurred_at": event.occurred_at.isoformat(),
        "kind": type(event).__name__,
        "runner_id": payload.get("runner_id"),
        "payload": json.dumps(payload),
    }


def _event_from_row(row: sqlite3.Row) -> DomainEvent | None:
    kind = getattr(domain_events, row["kind"], None)
    if not isinstance(kind, type) or not issubclass(kind, DomainEvent):
        return None
    payload = json.loads(row["payload"])
    known = {field.name for field in dataclass_fields(kind)}
    arguments = {key: value for key, value in payload.items() if key in known}
    if "repository" in arguments and isinstance(arguments["repository"], str):
        arguments["repository"] = RepositoryTarget.parse(arguments["repository"])
    try:
        return kind(occurred_at=_time(row["occurred_at"]), **arguments)
    except (TypeError, ValueError):
        # A record written by an older schema whose event has since changed shape. Skipping it
        # loses a line of history; raising would break `ghspot history` entirely.
        return None


def _encode(value: Any) -> Any:
    if isinstance(value, RepositoryTarget):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
