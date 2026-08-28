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
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from starlette.requests import Request

from ghspot import __version__
from ghspot.application.queries.resolve import ResolveRunner
from ghspot.application.queries.stats import GatherStats
from ghspot.application.queries.views import GetPoolStatus, ListRunners, to_view
from ghspot.composition import Application
from ghspot.domain.errors import GhSpotError, RunnerBusyError, RunnerNotFoundError
from ghspot.domain.model.runner import RunnerState
from ghspot.interfaces.api.schemas import (
    ErrorResponse,
    HealthResponse,
    LogsResponse,
    PoolResponse,
    RunnerResponse,
    StatsResponse,
    TickResponse,
)


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

        return HealthResponse(
            status="ok" if docker_ok else "degraded",
            version=__version__,
            pools=len(app.settings.pools),
            docker=docker_ok,
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
    ) -> list[RunnerResponse]:
        query = ListRunners(app.runners, app.clock)
        views = await query(pool, include_terminal=include_terminal)
        return [RunnerResponse.of(view) for view in views]

    @api.get("/runners/{reference}", response_model=RunnerResponse, tags=["runners"])
    async def get_runner(reference: str, app: Wired) -> RunnerResponse:
        runner = await ResolveRunner(app.runners)(reference)
        return RunnerResponse.of(to_view(runner, app.clock.now()))

    @api.get("/runners/{reference}/logs", response_model=LogsResponse, tags=["runners"])
    async def get_runner_logs(
        reference: str,
        app: Wired,
        tail: Annotated[int, Query(ge=1, le=10_000)] = 200,
    ) -> LogsResponse:
        runner = await ResolveRunner(app.runners)(reference)
        lines = (
            ""
            if runner.container_id is None
            else await app.backend.logs(runner.container_id, tail=tail)
        )
        return LogsResponse(runner_id=str(runner.id), lines=lines)

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
        query = GatherStats(app.events, app.runners, app.clock)
        return StatsResponse.of(await query(start))

    @api.post("/reconcile", response_model=TickResponse, tags=["status"])
    async def reconcile(app: Wired) -> TickResponse:
        """Run one reconciliation tick now instead of waiting for the interval."""
        return TickResponse.of(await app.reconciler.tick())

    return api
