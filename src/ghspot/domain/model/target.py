"""The repository a pool of runners serves."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from ghspot.domain.errors import InvalidRepositoryTargetError

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
