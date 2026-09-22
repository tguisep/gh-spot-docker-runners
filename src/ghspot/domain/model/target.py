"""The GitHub target a pool of runners serves: one repository, or a whole organization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from ghspot.domain.errors import InvalidOrganizationTargetError, InvalidRepositoryTargetError

# GitHub allows alphanumerics, hyphens, underscores and dots in repository names, and
# alphanumerics with single hyphens in owner names. Being strict here keeps malformed
# configuration from reaching the API client as a path-traversal-shaped string.
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True, slots=True, order=True)
class RepositoryTarget:
    """A single GitHub repository, identified as ``owner/name``."""

    owner: str
    name: str

    def __post_init__(self) -> None:
        if not _OWNER.match(self.owner):
            raise InvalidRepositoryTargetError(f"{self.owner!r} is not a valid GitHub owner")
        if not _NAME.match(self.name) or self.name in {".", ".."}:
            raise InvalidRepositoryTargetError(f"{self.name!r} is not a valid repository name")

    @classmethod
    def parse(cls, value: str) -> Self:
        """Build a target from an ``owner/name`` string."""
        owner, separator, name = value.strip().partition("/")
        if not separator:
            raise InvalidRepositoryTargetError(f"{value!r} is not in 'owner/name' form")
        return cls(owner=owner, name=name)

    @property
    def api_path(self) -> str:
        """The path segment used by the repository-scoped REST endpoints."""
        return f"repos/{self.owner}/{self.name}"

    def __str__(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True, order=True)
class OrganizationTarget:
    """A whole GitHub organization: a pool's runners serve every repository under it.

    Registered separately from any one repository's runners, using GitHub's org-scoped
    endpoints. There is no organization-scoped equivalent of "queued jobs" — that demand
    signal stays per-repository regardless of what a runner is registered against, which is
    why a pool needs to say which repositories it watches (see ``PoolSpec.repositories`` and
    ``discover_repositories``).
    """

    name: str

    def __post_init__(self) -> None:
        if not _OWNER.match(self.name):
            raise InvalidOrganizationTargetError(
                f"{self.name!r} is not a valid GitHub organization"
            )

    @property
    def api_path(self) -> str:
        """The path segment used by the organization-scoped REST endpoints."""
        return f"orgs/{self.name}"

    def __str__(self) -> str:
        return self.name


#: A pool registers its runners against one of these. A repository target always contains a
#: ``/`` (``owner/name``); an organization name never can — which is what lets a single stored
#: string (a SQLite column, a Docker label) round-trip through :func:`parse_target` without a
#: separate column saying which kind it is.
GitHubTarget = RepositoryTarget | OrganizationTarget


def parse_target(value: str) -> GitHubTarget:
    """Parse a stored or configured string back into whichever target it names."""
    return RepositoryTarget.parse(value) if "/" in value else OrganizationTarget(value.strip())
