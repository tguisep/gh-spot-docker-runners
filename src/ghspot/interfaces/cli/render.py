"""The view layer: turning application DTOs into something readable in a terminal.

Nothing here knows about Docker, GitHub or the domain — only about DTOs and Rich.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ghspot.application.dto import PoolView, RunnerView, StatsView, TickReport, UsageStats
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


def _percent(fraction: float) -> str:
    return f"{fraction * 100:.0f}%"


def _usage_table(rows: Sequence[UsageStats], total: UsageStats, heading: str) -> Table:
    table = Table(header_style="bold", expand=False)
    # Folded rather than ellipsized: `owner/repo` truncated to `own…` names nothing, and a
    # narrow terminal is exactly where the reader most needs to know which row is which.
    table.add_column(heading, style="bold", overflow="fold", min_width=12)
    table.add_column("runners", justify="right")
    table.add_column("jobs", justify="right")
    table.add_column("fail", justify="right")
    table.add_column("fail%", justify="right")
    table.add_column("busy", justify="right")
    table.add_column("avg job", justify="right")
    table.add_column("avg wait", justify="right")
    table.add_column("used", justify="right")
    table.add_column("live", justify="right")

    def add(stats: UsageStats, name: str, style: str = "") -> None:
        failed = Text(str(stats.failed), style="red" if stats.failed else "dim")
        table.add_row(
            Text(name, style=style),
            str(stats.runners),
            str(stats.jobs),
            failed,
            _percent(stats.failure_rate) if stats.runners else "-",
            duration(stats.busy_seconds),
            duration(stats.mean_busy_seconds) if stats.jobs else "-",
            duration(stats.mean_wait_seconds) if stats.waits_counted else "-",
            _percent(stats.utilisation) if stats.alive_seconds else "-",
            str(stats.live) if stats.live else "-",
            style=style,
        )

    for stats in rows:
        add(stats, stats.key or "(none)")
    if len(rows) > 1:
        add(total, "all", style="bold")
    return table


def stats_tables(view: StatsView) -> list[Table | Text]:
    """The usage report: one table per axis, plus failures when there are any."""
    window = (
        f"since {view.since:%Y-%m-%d %H:%M} UTC"
        if view.since is not None
        else "the whole recorded history"
    )
    blocks: list[Table | Text] = [
        Text.assemble(
            ("usage ", "bold"),
            (f"— {window}, {view.events_read} event(s) read", "dim"),
        )
    ]

    if not view.by_repository:
        blocks.append(
            Text(
                "nothing recorded in this window",
                style="dim",
            )
        )
        return blocks

    blocks.append(_usage_table(view.by_repository, view.total, "repository"))
    if len(view.by_pool) > 1 or (view.by_pool and view.by_pool[0].key):
        blocks.append(_usage_table(view.by_pool, view.total, "pool"))

    if view.failures:
        failures = Table(title="failures", header_style="bold", expand=False)
        failures.add_column("reason")
        failures.add_column("count", justify="right")
        for reason, count in view.failures:
            failures.add_row(Text(reason, style="red"), str(count))
        blocks.append(failures)

    return blocks


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
