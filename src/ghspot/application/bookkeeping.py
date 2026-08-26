"""The labels the daemon stamps onto its containers.

These are how the fleet is rediscovered. After a restart — or a crash — the daemon has no
memory it can trust, so it asks Docker which containers carry its namespace and reads the
correlation back out of them. Losing the database costs history; losing these would lose
the fleet.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ghspot.domain.model.runner import Runner, RunnerId

NAMESPACE = "io.ghspot"

MANAGED = f"{NAMESPACE}.managed"
RUNNER_ID = f"{NAMESPACE}.runner-id"
POOL = f"{NAMESPACE}.pool"
REPOSITORY = f"{NAMESPACE}.repository"
GITHUB_RUNNER_ID = f"{NAMESPACE}.github-runner-id"
CREATED_AT = f"{NAMESPACE}.created-at"

#: The selector that finds every container this daemon owns, and nothing else.
OWNED_SELECTOR: Mapping[str, str] = {MANAGED: "true"}


def labels_for(runner: Runner) -> dict[str, str]:
    """The bookkeeping stamped onto a runner's container at creation."""
    labels = {
        MANAGED: "true",
        RUNNER_ID: str(runner.id),
        POOL: runner.pool,
        REPOSITORY: str(runner.repository),
        CREATED_AT: runner.created_at.isoformat(),
    }
    if runner.github_runner_id is not None:
        labels[GITHUB_RUNNER_ID] = str(runner.github_runner_id)
    return labels


def runner_id_from(labels: Mapping[str, str]) -> RunnerId | None:
    value = labels.get(RUNNER_ID)
    return RunnerId(value) if value else None


def github_runner_id_from(labels: Mapping[str, str]) -> int | None:
    value = labels.get(GITHUB_RUNNER_ID)
    if value is None or not value.lstrip("-").isdigit():
        return None
    return int(value)


def pool_from(labels: Mapping[str, str]) -> str | None:
    return labels.get(POOL)


def created_at_from(labels: Mapping[str, str]) -> datetime | None:
    value = labels.get(CREATED_AT)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
