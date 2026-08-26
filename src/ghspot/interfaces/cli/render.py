"""The view layer: turning application DTOs into something readable in a terminal.

Nothing here knows about Docker, GitHub or the domain — only about DTOs and Rich.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ghspot.application.dto import PoolView, RunnerView, TickReport
from ghspot.domain.model.runner import RunnerState

console = Console()
error_console = Console(stderr=True)

_STATE_COLOUR = {
    RunnerState.PENDING: "dim",
    RunnerState.REGISTERED: "yellow",
    RunnerState.STARTING: "yellow",
    RunnerState.IDLE: "cyan",
    RunnerState.BUSY: "green",
    RunnerState.DRAINING: "magenta",
    RunnerState.RETIRED: "dim",
    RunnerState.FAILED: "red",
}


def state_text(state: RunnerState) -> Text:
    return Text(state.value, style=_STATE_COLOUR.get(state, ""))


def duration(seconds: float) -> str:
    """Compact and approximate: an operator wants 'about 5 minutes', not 312.4s."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    hours, remainder = divmod(seconds, 3600)
    return f"{hours}h{remainder // 60:02d}m"


def runners_table(runners: Sequence[RunnerView], *, title: str | None = None) -> Table:
    table = Table(title=title, header_style="bold", expand=False)
    table.add_column("runner", style="bold")
    table.add_column("pool")
    table.add_column("state")
    table.add_column("age", justify="right")
    table.add_column("in state", justify="right")
    table.add_column("container")
    table.add_column("gh id", justify="right")

    for runner in runners:
        table.add_row(
            runner.name,
            runner.pool,
            state_text(runner.state),
            duration(runner.age_seconds),
            duration(runner.time_in_state_seconds),
            runner.short_container_id or "—",
            str(runner.github_runner_id or "—"),
        )
    return table


def pools_table(pools: Sequence[PoolView]) -> Table:
    table = Table(header_style="bold", expand=False)
    table.add_column("pool", style="bold")
    table.add_column("repository")
    table.add_column("labels", style="dim")
    table.add_column("idle", justify="right")
    table.add_column("busy", justify="right")
    table.add_column("active", justify="right")
    table.add_column("max", justify="right")
    table.add_column("queued", justify="right")

    for pool in pools:
        table.add_row(
            pool.name,
            pool.repository,
            ", ".join(pool.labels),
            str(pool.idle),
            str(pool.busy),
            str(pool.active),
            str(pool.max_runners),
            str(pool.queued_jobs) if pool.queued_jobs else "—",
        )
    return table


def tick_summary(report: TickReport) -> Text:
    if not report.changed_anything and not report.errors:
        return Text(f"nothing to do ({report.queued_jobs} queued)", style="dim")

    parts = []
    if report.launched:
        parts.append(f"launched {report.launched}")
    if report.retired:
        parts.append(f"retired {report.retired}")
    if report.terminated:
        parts.append(f"terminated {report.terminated}")
    if report.repaired:
        parts.append(f"repaired {report.repaired}")

    text = Text(", ".join(parts) or "no changes")
    for problem in report.errors:
        text.append(f"\n  ! {problem}", style="red")
    for note in report.notes:
        text.append(f"\n  · {note}", style="dim")
    return text


def fail(message: str) -> None:
    error_console.print(f"[red]error[/red] {message}")


def hint(message: str) -> None:
    error_console.print(f"[dim]hint:[/dim] {message}")
