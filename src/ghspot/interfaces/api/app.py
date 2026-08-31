"""The REST API.

Read-mostly by design. The daemon owns the fleet; this exposes what it is doing and offers
the two interventions an operator actually needs — stop a runner, force a tick. Anything
that decides *how many* runners should exist stays in the scaling policy, where it is tested.

There is no authentication here. Bind it to localhost, or put a reverse proxy in front.
``ghspot config validate`` says so, and so does the docs page.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from starlette.requests import Request

from ghspot import __version__
from ghspot.application.queries.jobs import FindJobForRunner
from ghspot.application.queries.resolve import ResolveRunner
from ghspot.application.queries.stats import GatherStats
from ghspot.application.queries.views import GetPoolStatus, ListRunners, to_view
from ghspot.composition import Application
from ghspot.domain.errors import GhSpotError, RunnerBusyError, RunnerNotFoundError
from ghspot.domain.model.runner import Runner, RunnerState
from ghspot.infrastructure.config.settings import changed_on_disk, unconfigured
from ghspot.interfaces.api import dashboard
from ghspot.interfaces.api.schemas import (
    ErrorResponse,
    HealthResponse,
    JobLogsResponse,
    LogsResponse,
    PoolResponse,
    RunnerResponse,
    StatsResponse,
    TickResponse,
)


def _why_no_logs(runner: Runner) -> str:
    """Say which of the several reasons for an empty pane this one is.

    They need different things from the reader: waiting, looking at GitHub instead, or
    accepting that the evidence is gone. One blank box for all of them told nobody which.
    """
    if runner.is_terminal:
        return (
            "the container was removed when this runner retired, and nothing was kept — "
            "runners retired before log retention existed have no archived output. "
            "If it ran a job, GitHub still has that log."
        )
    if runner.container_id is None:
        return "this runner has no container yet"
    return "the container has not printed anything yet"


def _wired(request: Request) -> Application:
    """The application the API was built around, taken from Starlette's app state."""
    application: Application = request.app.state.application
    return application


Wired = Annotated["Application", Depends(_wired)]

DESCRIPTION = """
Self-hosted GitHub Actions runners as ephemeral Docker containers.

This API reports what the daemon is doing and offers the two interventions an operator
needs. It has **no authentication** — bind it to localhost or put a proxy in front of it.
"""


def create_app(application: Application) -> FastAPI:
    """Build the API around an already-wired application."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await application.aclose()

    api = FastAPI(
        title="ghspot",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    api.state.application = application

    @api.exception_handler(GhSpotError)
    async def domain_error(_: Request, error: GhSpotError) -> JSONResponse:
        """Domain failures are the caller's problem or the fleet's, never a stack trace."""
        status = 404 if isinstance(error, RunnerNotFoundError) else 409
        return JSONResponse(status_code=status, content={"detail": str(error)})

    @api.get("/health", response_model=HealthResponse, tags=["status"])
    async def health(app: Wired) -> HealthResponse:
        """Liveness, plus whether Docker is actually reachable.

        A daemon that is running but cannot reach Docker is not healthy in any useful sense.
        """
        try:
            docker_ok = await app.backend.ping()
        except GhSpotError:
            docker_ok = False

        # `status` stays about whether the daemon can *operate*. Being half-configured is a
        # different question with a different answer — and folding it in here would make a
        # fresh install look like a broken one to anything watching this endpoint.
        pending = unconfigured(app.settings)
        return HealthResponse(
            status="ok" if docker_ok else "degraded",
            version=__version__,
            host=app.settings.daemon.host,
            pools=len(app.settings.pools),
            docker=docker_ok,
            configured=pending is None,
            config_stale=changed_on_disk(app.settings),
            setup_reason=pending,
        )

    @api.get("/pools", response_model=list[PoolResponse], tags=["pools"])
    async def list_pools(app: Wired) -> list[PoolResponse]:
        query = GetPoolStatus(app.runners, app.clock)
        views = await query([pool.spec for pool in app.settings.pools])
        return [PoolResponse.of(view) for view in views]

    @api.get("/pools/{name}", response_model=PoolResponse, tags=["pools"])
    async def get_pool(name: str, app: Wired) -> PoolResponse:
        specs = [pool.spec for pool in app.settings.pools if pool.spec.name == name]
        if not specs:
            raise HTTPException(status_code=404, detail=f"no pool named {name!r}")
        query = GetPoolStatus(app.runners, app.clock)
        return PoolResponse.of((await query(specs))[0])

    @api.get("/runners", response_model=list[RunnerResponse], tags=["runners"])
    async def list_runners(
        app: Wired,
        pool: Annotated[str | None, Query(description="Restrict to one pool.")] = None,
        include_terminal: Annotated[
            bool, Query(description="Include retired and failed runners.")
        ] = False,
        usage: Annotated[
            bool,
            Query(
                description=(
                    "Sample CPU and memory for each running container. Off by default: it "
                    "costs one call to the Engine per runner."
                )
            ),
        ] = False,
    ) -> list[RunnerResponse]:
        query = ListRunners(app.runners, app.clock, app.backend)
        views = await query(pool, include_terminal=include_terminal, with_usage=usage)
        return [RunnerResponse.of(view) for view in views]

    @api.get("/runners/{reference}", response_model=RunnerResponse, tags=["runners"])
    async def get_runner(
        reference: str,
        app: Wired,
        usage: Annotated[bool, Query(description="Sample CPU and memory.")] = False,
    ) -> RunnerResponse:
        runner = await ResolveRunner(app.runners)(reference)
        view = to_view(runner, app.clock.now())
        if usage and runner.container_id:
            sampled = (await app.backend.usage([runner.container_id])).get(runner.container_id)
            if sampled is not None:
                view = replace(
                    view,
                    cpu_percent=sampled.cpu_percent,
                    memory_bytes=sampled.memory_bytes,
                    memory_limit_bytes=sampled.memory_limit_bytes,
                )
        return RunnerResponse.of(view)

    @api.get("/runners/{reference}/logs", response_model=LogsResponse, tags=["runners"])
    async def get_runner_logs(
        reference: str,
        app: Wired,
        tail: Annotated[int, Query(ge=1, le=10_000)] = 200,
    ) -> LogsResponse:
        runner = await ResolveRunner(app.runners)(reference)

        live = ""
        if runner.container_id is not None:
            live = await app.backend.logs(runner.container_id, tail=tail)
        if live.strip():
            return LogsResponse(runner_id=str(runner.id), lines=live, source="container")

        # The container is gone, or never said anything. Retiring a runner removes its
        # container, so for anything terminal this is the only copy there is.
        kept = await app.runner_logs.fetch(runner.id)
        if kept is not None:
            return LogsResponse(runner_id=str(runner.id), lines=kept, source="archive")

        return LogsResponse(
            runner_id=str(runner.id),
            lines="",
            source="none",
            reason=_why_no_logs(runner),
        )

    @api.get("/runners/{reference}/job-logs", response_model=JobLogsResponse, tags=["runners"])
    async def get_job_logs(
        reference: str,
        app: Wired,
        tail: Annotated[int, Query(ge=1, le=10_000)] = 500,
    ) -> JobLogsResponse:
        """The forge's log for this runner's job.

        The container's own output is the live view — the runner prints the job to stdout.
        This is the other half: written when the job finishes, and it outlives the container,
        which a just-in-time runner takes with it seconds later.
        """
        runner = await ResolveRunner(app.runners)(reference)
        job_id = await FindJobForRunner(app.forge, app.runners)(runner)

        lines = None
        if job_id is not None:
            lines = await app.forge.job_logs(runner.repository, job_id, tail=tail)
        return JobLogsResponse(
            runner_id=str(runner.id),
            job_id=job_id,
            available=lines is not None,
            lines=lines or "",
        )

    @api.delete("/runners/{reference}", response_model=RunnerResponse, tags=["runners"])
    async def stop_runner(
        reference: str,
        app: Wired,
        force: Annotated[bool, Query(description="Stop it even if it is running a job.")] = False,
    ) -> RunnerResponse:
        """Retire a runner on both sides.

        A busy runner is refused with 409 unless ``force`` is set: stopping it fails
        somebody's build, so the caller has to say they mean it.
        """
        runner = await ResolveRunner(app.runners)(reference)
        if runner.state in {RunnerState.BUSY, RunnerState.DRAINING} and not force:
            raise RunnerBusyError(
                f"{runner.name} is running job {runner.current_job_id or '(unknown)'}. "
                "Pass force=true to stop it anyway."
            )
        await app.retire(runner, reason="stopped via the API", force=force)
        return RunnerResponse.of(to_view(runner, app.clock.now()))

    @api.get("/stats", response_model=StatsResponse, tags=["status"])
    async def stats(
        app: Wired,
        since_seconds: Annotated[
            float | None,
            Query(
                ge=0,
                description="Window to report on, in seconds. Omit for the whole history.",
            ),
        ] = None,
    ) -> StatsResponse:
        """What the fleet did: runners, jobs, failures and time spent.

        Read from the event log, so it covers runners that no longer exist.
        """
        start = None
        if since_seconds is not None:
            start = app.clock.now() - timedelta(seconds=since_seconds)
        query = GatherStats(app.events, app.runners, app.clock, app.settings.daemon.host)
        return StatsResponse.of(await query(start))

    @api.post("/reconcile", response_model=TickResponse, tags=["status"])
    async def reconcile(app: Wired) -> TickResponse:
        """Run one reconciliation tick now instead of waiting for the interval."""
        return TickResponse.of(await app.reconciler.tick())

    # Last, so a dashboard route can never shadow an API one. Absent when it was not built,
    # which is the normal state of a source checkout and not a failure.
    dashboard.mount(api)

    return api
