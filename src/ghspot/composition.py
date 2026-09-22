"""The composition root.

The one place that knows which concrete adapter satisfies which port. Every other module
takes its dependencies as constructor arguments and never imports an adapter, which is what
lets the whole reconciliation loop run against fakes in the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass

from ghspot.application.commands.housekeeping import ReclaimHostSpace, parse_size
from ghspot.application.commands.provision import ProvisionRunner
from ghspot.application.commands.retire import RetireRunner
from ghspot.application.reconciliation import CredentialGroup, ReconciliationService
from ghspot.domain.model.runner import Runner
from ghspot.domain.model.target import GitHubTarget
from ghspot.domain.ports.backend import PruneRequest
from ghspot.domain.ports.forge import ForgeClient
from ghspot.infrastructure.config.settings import GitHubSettings, Settings
from ghspot.infrastructure.docker.backend import DockerRunnerBackend
from ghspot.infrastructure.github.auth import (
    GitHubAppTokenProvider,
    StaticTokenProvider,
    TokenProvider,
)
from ghspot.infrastructure.github.client import GitHubClient
from ghspot.infrastructure.persistence.sqlite import (
    SqliteEventLog,
    SqliteQueueSnapshots,
    SqliteRunnerLogs,
    SqliteRunnerRepository,
)
from ghspot.infrastructure.system import SystemClock, UuidGenerator


@dataclass(slots=True)
class Application:
    """Everything wired together, ready to use."""

    settings: Settings
    credentials: dict[str, CredentialGroup]
    """One entry per configured GitHub credential. A pool routes through whichever one its
    `credential` names — see `forge_for` and `retire`, the two ways anything outside the
    reconciler reaches a pool's own forge."""

    backend: DockerRunnerBackend
    runners: SqliteRunnerRepository
    events: SqliteEventLog
    runner_logs: SqliteRunnerLogs
    queue: SqliteQueueSnapshots
    reconciler: ReconciliationService
    housekeeping: ReclaimHostSpace
    clock: SystemClock

    async def aclose(self) -> None:
        for group in self.credentials.values():
            await group.forge.aclose()

    def _credential_name_for(self, pool_name: str) -> str:
        """Which credential a pool uses, from the settings this application was built with.

        Falls back to the default credential for a runner whose pool has since been removed
        from configuration — the same "adopted or reaped from its own container labels" orphan
        case reconciliation already has, now also orphaned from a credential's point of view.
        Retiring such a runner still tears down its container regardless of forge; only the
        best-effort GitHub-side deletion could use the wrong one, and that is already wrapped
        in a swallowed `GhSpotError`.
        """
        for pool in self.settings.pools:
            if pool.spec.name == pool_name:
                return pool.credential
        return "default"

    def forge_for(self, pool_name: str) -> ForgeClient:
        """The forge client the given pool's credential resolves to."""
        return self.credentials[self._credential_name_for(pool_name)].forge

    async def retire(self, runner: Runner, reason: str, *, force: bool = False) -> None:
        """Retire a runner through its own pool's credential."""
        group = self.credentials[self._credential_name_for(runner.pool)]
        await group.retire(runner, reason, force=force)


def build_auth(credential: GitHubSettings, discovery_target: GitHubTarget | None) -> TokenProvider:
    """Choose how to authenticate, from what one credential's configuration provides.

    A GitHub App is used when one is configured, since it is the better credential in every
    respect that matters here. A personal access token remains supported because it is the
    faster thing to set up when trying the project out.
    """
    if not credential.uses_app:
        return StaticTokenProvider(credential.resolve_token())

    assert credential.app_id is not None
    return GitHubAppTokenProvider(
        app_id=credential.app_id,
        private_key=credential.resolve_private_key(),
        installation_id=credential.installation_id,
        base_url=credential.api_url,
        # Falling back to the first pool using this credential lets an operator skip
        # installation_id entirely in the common single-installation case.
        discovery_target=discovery_target,
    )


def build_forge(credential: GitHubSettings, discovery_target: GitHubTarget | None) -> GitHubClient:
    """Just one credential's forge client, with no Docker connection.

    ``ghspot doctor`` checks GitHub and Docker independently, and must still be able to
    report on one when the other is unreachable — that is the situation it exists for.
    """
    return GitHubClient(
        auth=build_auth(credential, discovery_target),
        base_url=credential.api_url,
        timeout_seconds=credential.request_timeout.total_seconds(),
    )


def discovery_target_for(settings: Settings, credential_name: str) -> GitHubTarget | None:
    """The first pool (declaration order) using this credential, for installation discovery."""
    for pool in settings.pools:
        if pool.credential == credential_name:
            return pool.spec.target
    return None


def build(settings: Settings, *, backend: DockerRunnerBackend | None = None) -> Application:
    """Assemble the application from validated settings.

    ``backend`` is injectable so ``ghspot doctor`` can report a broken Docker connection
    rather than failing to construct.
    """
    clock = SystemClock()
    ids = UuidGenerator()

    container_backend = backend or DockerRunnerBackend()
    runners = SqliteRunnerRepository(settings.daemon.state_db)
    events = SqliteEventLog(settings.daemon.state_db)
    runner_logs = SqliteRunnerLogs(settings.daemon.state_db)
    queue = SqliteQueueSnapshots(settings.daemon.state_db)

    credentials: dict[str, CredentialGroup] = {}
    for credential in settings.credentials:
        forge = build_forge(credential, discovery_target_for(settings, credential.name))
        provision = ProvisionRunner(
            forge=forge,
            backend=container_backend,
            runners=runners,
            clock=clock,
            ids=ids,
            events=events,
            host=settings.daemon.host,
        )
        retire = RetireRunner(
            forge=forge,
            backend=container_backend,
            runners=runners,
            clock=clock,
            events=events,
            stop_timeout_seconds=int(settings.daemon.stop_timeout.total_seconds()),
            archive=runner_logs,
        )
        credentials[credential.name] = CredentialGroup(
            forge=forge, provision=provision, retire=retire
        )

    reconciler = ReconciliationService(
        pools=settings.pools,
        credentials=credentials,
        backend=container_backend,
        runners=runners,
        clock=clock,
        events=events,
        capacity=settings.capacity,
        host=settings.daemon.host,
        queue=queue,
    )

    keep = settings.housekeeping
    housekeeping = ReclaimHostSpace(
        backend=container_backend,
        clock=clock,
        every=keep.every,
        enabled=keep.enabled,
        request=PruneRequest(
            containers_older_than=keep.containers_older_than,
            images_older_than=keep.images_older_than,
            volumes=keep.volumes,
            build_cache_older_than=keep.build_cache_older_than,
            keep_build_cache_bytes=(
                parse_size(keep.keep_build_cache) if keep.keep_build_cache else None
            ),
        ),
    )

    return Application(
        settings=settings,
        credentials=credentials,
        backend=container_backend,
        runners=runners,
        events=events,
        runner_logs=runner_logs,
        queue=queue,
        reconciler=reconciler,
        housekeeping=housekeeping,
        clock=clock,
    )


def read_only_store(settings: Settings) -> SqliteRunnerRepository:
    """Just the projection, with no token and no Docker connection.

    Query commands read from here, so an expired token or a stopped Docker daemon does not
    also take away the operator's ability to see what the fleet was doing.
    """
    return SqliteRunnerRepository(settings.daemon.state_db)


def read_only_events(settings: Settings) -> SqliteEventLog:
    """The history, on the same terms: no token, no Docker, read from the file."""
    return SqliteEventLog(settings.daemon.state_db)


def read_only_runner_logs(settings: Settings) -> SqliteRunnerLogs:
    """What retired containers said, on the same read-only terms as the projection."""
    return SqliteRunnerLogs(settings.daemon.state_db)


def read_only_queue(settings: Settings) -> SqliteQueueSnapshots:
    """The last queue reading the daemon wrote down.

    Read-only on the same terms as the rest: `ghspot queue` shows a real queue without ever
    holding a GitHub token, because the daemon already paid for the request and left the
    answer in the projection.
    """
    return SqliteQueueSnapshots(settings.daemon.state_db)
