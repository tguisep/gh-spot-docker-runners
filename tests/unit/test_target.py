from __future__ import annotations

import pytest

from ghspot.domain.errors import InvalidRepositoryTargetError
from ghspot.domain.model.target import RepositoryTarget


def test_parses_owner_and_name() -> None:
    target = RepositoryTarget.parse("tguisep/gh-spot-docker-runners")
    assert target.owner == "tguisep"
    assert target.name == "gh-spot-docker-runners"
    assert str(target) == "tguisep/gh-spot-docker-runners"
    assert target.api_path == "repos/tguisep/gh-spot-docker-runners"


def test_surrounding_whitespace_is_tolerated() -> None:
    assert RepositoryTarget.parse("  owner/repo  ") == RepositoryTarget("owner", "repo")


@pytest.mark.parametrize(
    "value",
    [
        "no-slash",
        "/repo",
        "owner/",
        "owner/../etc",
        "-leading-hyphen/repo",
        "owner--double/repo",
        "own er/repo",
        "owner/re po",
        "owner/.",
        "owner/..",
        "a" * 40 + "/repo",
    ],
)
def test_malformed_references_are_refused(value: str) -> None:
    """Configuration mistakes must fail here, not as a strange URL at the API boundary."""
    with pytest.raises(InvalidRepositoryTargetError):
        RepositoryTarget.parse(value)


def test_targets_are_hashable_and_comparable() -> None:
    a = RepositoryTarget.parse("owner/a")
    b = RepositoryTarget.parse("owner/b")
    assert len({a, b, RepositoryTarget.parse("owner/a")}) == 2
    assert sorted([b, a]) == [a, b]
