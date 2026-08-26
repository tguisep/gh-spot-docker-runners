"""The composition root.

The one place that knows which concrete adapter satisfies which port. Every other module
takes its dependencies as constructor arguments and never imports an adapter, which is what
lets the whole reconciliation loop run against fakes in the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass

from ghspot.application.commands.provision import ProvisionRunner
from ghspot.application.commands.retire import RetireRunner
from ghspot.application.reconciliation import ReconciliationService
from ghspot.infrastructure.config.settings import Settings
from ghspot.infrastructure.docker.backend import DockerRunnerBackend
from ghspot.infrastructure.github.auth import (
    GitHubAppTokenProvider,
    StaticTokenProvider,
    TokenProvider,
)
from ghspot.infrastructure.github.client import GitHubClient
from ghspot.infrastructure.persistence.sqlite import SqliteEventLog, SqliteRunnerRepository
from ghspot.infrastructure.system import SystemClock, UuidGenerator


@dataclass(slots=True)
class Application:
    """Everything wired together, ready to use."""

    settings: Settings
    forge: GitHubClient
    backend: DockerRunnerBackend
    runners: SqliteRunnerRepository
    events: SqliteEventLog
    reconciler: ReconciliationService
    clock: SystemClock
    provision: ProvisionRunner
    retire: RetireRunner

    async def aclose(self) -> None:
        await self.forge.aclose()


def build_auth(settings: Settings) -> TokenProvider:
    """Choose how to authenticate, from what the configuration provides.

    A GitHub App is used when one is configured, since it is the better credential in every
    respect that matters here. A personal access token remains supported because it is the
    faster thing to set up when trying the project out.
    """
    github = settings.github
    if not github.uses_app:
        return StaticTokenProvider(github.resolve_token())

    assert github.app_id is not None
    return GitHubAppTokenProvider(
        app_id=github.app_id,
        private_key=github.resolve_private_key(),
        installation_id=github.installation_id,
        base_url=github.api_url,
        # Falling back to the first configured repository lets an operator skip
        # installation_id entirely in the common single-installation case.
        discovery_repository=next(iter(settings.repositories), None),
    )


def build(settings: Settings, *, backend: DockerRunnerBackend | None = None) -> Application:
    """Assemble the application from validated settings.

    ``backend`` is injectable so ``ghspot doctor`` can report a broken Docker connection
    rather than failing to construct.
    """
    clock = SystemClock()
    ids = UuidGenerator()

    forge = GitHubClient(
        auth=build_auth(settings),
        base_url=settings.github.api_url,
        timeout_seconds=settings.github.request_timeout.total_seconds(),
    )
    container_backend = backend or DockerRunnerBackend()
    runners = SqliteRunnerRepository(settings.daemon.state_db)
    events = SqliteEventLog(settings.daemon.state_db)

    provision = ProvisionRunner(
        forge=forge, backend=container_backend, runners=runners, clock=clock, ids=ids, events=events
    )
    retire = RetireRunner(
        forge=forge,
        backend=container_backend,
        runners=runners,
        clock=clock,
        events=events,
        stop_timeout_seconds=int(settings.daemon.stop_timeout.total_seconds()),
    )
    reconciler = ReconciliationService(
        pools=settings.pools,
        forge=forge,
        backend=container_backend,
        runners=runners,
        clock=clock,
        events=events,
        provision=provision,
        retire=retire,
    )

    return Application(
        settings=settings,
        forge=forge,
        backend=container_backend,
        runners=runners,
        events=events,
        reconciler=reconciler,
        clock=clock,
        provision=provision,
        retire=retire,
    )


def read_only_store(settings: Settings) -> SqliteRunnerRepository:
    """Just the projection, with no token and no Docker connection.

    Query commands read from here, so an expired token or a stopped Docker daemon does not
    also take away the operator's ability to see what the fleet was doing.
    """
    return SqliteRunnerRepository(settings.daemon.state_db)
