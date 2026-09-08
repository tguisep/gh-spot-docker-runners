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
from contextlib import suppress
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ghspot.domain.model import events as domain_events
from ghspot.domain.model.events import DomainEvent
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.queue import (
    HostPressure,
    PoolPressure,
    QueueEntry,
    QueueSnapshot,
    WaitReason,
)
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget

SCHEMA_VERSION = 2

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

-- A retired runner's container is gone, and with it its output. This keeps the tail.
--
-- Its own table rather than a column on `runners`: every listing does SELECT * on that one,
-- and a log-sized TEXT beside twelve small columns would be read on every `ghspot runner
-- list`. The cascade means the existing prune takes these with it and nothing else has to
-- remember they exist.
CREATE TABLE IF NOT EXISTS runner_logs (
    runner_id   TEXT PRIMARY KEY REFERENCES runners (id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    lines       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    kind        TEXT NOT NULL,
    runner_id   TEXT,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_time ON events (occurred_at DESC);

-- What the last tick saw waiting, and what it decided was in the way. One row, replaced
-- every tick: the queue is a live thing, and a reader asking what is waiting means now.
--
-- Stored as a JSON document rather than normalised into tables because nothing ever queries
-- inside it — it is written whole and read whole, by one writer and any number of readers.
CREATE TABLE IF NOT EXISTS queue_snapshot (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    taken_at TEXT NOT NULL,
    document TEXT NOT NULL
);
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


class SqliteRunnerLogs(SqliteStore):
    """A :class:`~ghspot.domain.ports.repository.RunnerLogArchive` on SQLite."""

    MAX_BYTES = 256 * 1024
    """Ceiling per runner. A job that prints a megabyte a minute must not be able to grow the
    projection without bound, and the interesting part of a failure is the end anyway."""

    async def store(self, runner_id: RunnerId, lines: str) -> None:
        kept = lines.encode("utf-8")[-self.MAX_BYTES :].decode("utf-8", errors="ignore")
        captured_at = datetime.now(UTC).isoformat()

        def work(connection: sqlite3.Connection) -> None:
            # The runner's row has to exist for the foreign key to hold. It does by the time
            # anything retires it, but a caller that got the order wrong should not take the
            # retirement down with it — losing the log is the smaller failure.
            with suppress(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT OR REPLACE INTO runner_logs (runner_id, captured_at, lines) "
                    "VALUES (?, ?, ?)",
                    (str(runner_id), captured_at, kept),
                )

        await self._run(work)

    async def fetch(self, runner_id: RunnerId) -> str | None:
        def work(connection: sqlite3.Connection) -> str | None:
            row = connection.execute(
                "SELECT lines FROM runner_logs WHERE runner_id = ?", (str(runner_id),)
            ).fetchone()
            return str(row["lines"]) if row is not None else None

        result: str | None = await self._run(work)
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

    async def since(self, moment: datetime | None = None) -> Sequence[DomainEvent]:
        """Everything at or after ``moment``, oldest first. ``None`` reads the whole log."""

        def work(connection: sqlite3.Connection) -> list[DomainEvent]:
            if moment is None:
                rows = connection.execute(
                    "SELECT occurred_at, kind, payload FROM events ORDER BY id"
                ).fetchall()
            else:
                # The bound is compared as text, so it has to be written the way the rows
                # were: the clock only ever produces UTC, and a caller passing something
                # else would otherwise select the wrong window rather than failing.
                bound = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
                rows = connection.execute(
                    "SELECT occurred_at, kind, payload FROM events "
                    "WHERE occurred_at >= ? ORDER BY id",
                    (bound.isoformat(),),
                ).fetchall()
            return [event for event in (_event_from_row(row) for row in rows) if event]

        result: list[DomainEvent] = await self._run(work)
        return result

    async def publish(self, events: Sequence[DomainEvent]) -> None:
        """Also satisfies :class:`~ghspot.domain.ports.system.EventPublisher`."""
        await self.append(events)


class SqliteQueueSnapshots(SqliteStore):
    """The queue view the daemon leaves behind for the CLI and the API to read.

    Writing is best-effort by contract: the reconciler calls it at the end of a tick, and a
    tick that did its actual work must not be reported as failed because a note about it
    could not be filed. Reading is strict — a corrupt document reads as "no snapshot", which
    the interfaces already render as "the daemon has not looked yet".
    """

    async def record(self, snapshot: QueueSnapshot) -> None:
        document = json.dumps(_snapshot_document(snapshot))
        taken_at = snapshot.taken_at.isoformat()

        def work(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO queue_snapshot (id, taken_at, document)
                VALUES (1, :taken_at, :document)
                ON CONFLICT(id) DO UPDATE SET
                    taken_at = excluded.taken_at,
                    document = excluded.document
                """,
                {"taken_at": taken_at, "document": document},
            )

        with suppress(sqlite3.Error, OSError):
            await self._run(work)

    async def latest(self) -> QueueSnapshot | None:
        def work(connection: sqlite3.Connection) -> QueueSnapshot | None:
            row = connection.execute("SELECT document FROM queue_snapshot WHERE id = 1").fetchone()
            if row is None:
                return None
            try:
                return _snapshot_from_document(json.loads(row["document"]))
            except (ValueError, TypeError, KeyError):
                # Written by an older schema whose shape has since changed. Nothing is lost
                # that the next tick will not write again a few seconds from now.
                return None

        result: QueueSnapshot | None = await self._run(work)
        return result


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


def _snapshot_document(snapshot: QueueSnapshot) -> dict[str, Any]:
    """The snapshot as plain JSON.

    Written out field by field rather than through `asdict`, so that adding a field to the
    domain model is a deliberate act here too — the reader below has to learn about it, and a
    silent asymmetry between the two is exactly the bug this shape prevents.
    """
    return {
        "taken_at": snapshot.taken_at.isoformat(),
        "notes": list(snapshot.notes),
        "unreadable": list(snapshot.unreadable),
        "host": {
            "cpu_percent": snapshot.host.cpu_percent,
            "memory_percent": snapshot.host.memory_percent,
            "disk_percent": snapshot.host.disk_percent,
            "io_percent": snapshot.host.io_percent,
            "containers_running": snapshot.host.containers_running,
            "cpu_high_water": snapshot.host.cpu_high_water,
            "memory_high_water": snapshot.host.memory_high_water,
            "disk_high_water": snapshot.host.disk_high_water,
            "io_high_water": snapshot.host.io_high_water,
            "max_containers": snapshot.host.max_containers,
            "max_cpus": snapshot.host.max_cpus,
            "max_memory_bytes": snapshot.host.max_memory_bytes,
            "holding": snapshot.host.holding,
        },
        "pools": [
            {
                "pool": pressure.pool,
                "repository": pressure.repository,
                "priority": pressure.priority,
                "queued": pressure.queued,
                "available": pressure.available,
                "active": pressure.active,
                "max_runners": pressure.max_runners,
                "launching": pressure.launching,
                "wanted": pressure.wanted,
                "blocked_by": pressure.blocked_by,
            }
            for pressure in snapshot.pools
        ],
        "entries": [
            {
                "job_id": entry.job_id,
                "run_id": entry.run_id,
                "repository": entry.repository,
                "workflow": entry.workflow,
                "job_name": entry.job_name,
                "labels": list(entry.labels),
                "queued_at": entry.queued_at.isoformat(),
                "pool": entry.pool,
                "priority": entry.priority,
                "position": entry.position,
                "reason": entry.reason.value,
                "detail": entry.detail,
            }
            for entry in snapshot.entries
        ],
    }


def _snapshot_from_document(document: Any) -> QueueSnapshot:
    if not isinstance(document, dict):
        raise ValueError("queue snapshot is not an object")
    host = document.get("host") or {}
    return QueueSnapshot(
        taken_at=_time(document["taken_at"]),
        entries=tuple(_entry_from_document(item) for item in document.get("entries", [])),
        pools=tuple(
            PoolPressure(
                pool=str(item["pool"]),
                repository=str(item.get("repository", "")),
                priority=int(item.get("priority", 0)),
                queued=int(item.get("queued", 0)),
                available=int(item.get("available", 0)),
                active=int(item.get("active", 0)),
                max_runners=int(item.get("max_runners", 0)),
                launching=int(item.get("launching", 0)),
                wanted=int(item.get("wanted", 0)),
                blocked_by=str(item.get("blocked_by", "")),
            )
            for item in document.get("pools", [])
        ),
        host=HostPressure(
            cpu_percent=_number(host.get("cpu_percent")),
            memory_percent=_number(host.get("memory_percent")),
            disk_percent=_number(host.get("disk_percent")),
            io_percent=_number(host.get("io_percent")),
            containers_running=_count(host.get("containers_running")),
            cpu_high_water=_number(host.get("cpu_high_water")),
            memory_high_water=_number(host.get("memory_high_water")),
            disk_high_water=_number(host.get("disk_high_water")),
            io_high_water=_number(host.get("io_high_water")),
            max_containers=_count(host.get("max_containers")),
            max_cpus=_number(host.get("max_cpus")),
            max_memory_bytes=_count(host.get("max_memory_bytes")),
            holding=str(host.get("holding") or ""),
        ),
        notes=tuple(str(note) for note in document.get("notes", [])),
        unreadable=tuple(str(name) for name in document.get("unreadable", [])),
    )


def _number(value: Any) -> float | None:
    """A reading, or ``None``. Absent and zero are different facts about a host."""
    return float(value) if isinstance(value, int | float) else None


def _count(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _entry_from_document(item: Any) -> QueueEntry:
    return QueueEntry(
        job_id=int(item["job_id"]),
        run_id=int(item.get("run_id", 0)),
        repository=str(item.get("repository", "")),
        workflow=str(item.get("workflow", "")),
        job_name=str(item.get("job_name", "")),
        labels=tuple(str(label) for label in item.get("labels", [])),
        queued_at=_time(item["queued_at"]),
        pool=str(item.get("pool", "")),
        priority=int(item.get("priority", 0)),
        position=int(item.get("position", 0)),
        # An unknown reason from a newer writer degrades to "waiting", which is true of every
        # queued job and so can never mislead.
        reason=_reason(item.get("reason")),
        detail=str(item.get("detail", "")),
    )


def _reason(value: Any) -> WaitReason:
    try:
        return WaitReason(value)
    except ValueError:
        return WaitReason.CONTENDED
