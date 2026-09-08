"""The auto-priority classifier.

Every row here is a claim about who is waiting on a build. The rule reads only what the forge
already said about the run, so each case is the shape of an API payload turned into a rank.
"""

from __future__ import annotations

import pytest

from ghspot.domain.model.job import QueuedJob, WorkClass
from ghspot.domain.policy.priority import URGENCY, classify, urgency, urgency_of
from tests.unit.conftest import make_job


def job(**context: object) -> QueuedJob:
    """A queued job with the run context the classifier reads."""
    return make_job(1, **context)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        # A merge landing on the shared branch. The build nobody can route around.
        ({"event": "push", "branch": "main", "on_default_branch": True}, WorkClass.DEFAULT_BRANCH),
        # Somebody pressed a button and is watching the page.
        ({"event": "workflow_dispatch", "branch": "main"}, WorkClass.MANUAL),
        ({"event": "pull_request", "branch": "feat/x"}, WorkClass.PULL_REQUEST),
        # The author has said out loud that it is not finished.
        ({"event": "pull_request", "branch": "feat/x", "draft": True}, WorkClass.DRAFT),
        # A fork's pull request arrives under a different event and ranks the same.
        ({"event": "pull_request_target", "branch": "feat/x"}, WorkClass.PULL_REQUEST),
        ({"event": "push", "branch": "feat/x"}, WorkClass.BRANCH),
        # A tag push: not the default branch, nobody blocked on it right now.
        ({"event": "push", "branch": "v1.2.3"}, WorkClass.BRANCH),
        ({"event": "schedule", "branch": "main", "on_default_branch": True}, WorkClass.SCHEDULED),
    ],
)
def test_the_run_decides_the_class(context: dict[str, object], expected: WorkClass) -> None:
    assert classify(job(**context)) is expected


def test_a_scheduled_run_on_the_default_branch_is_still_a_nightly() -> None:
    """`schedule` fires on the default branch, so the branch check must not get there first —
    a cron job running ten minutes late is still a cron job, and outranking a merge with one
    would invert the whole scale."""
    nightly = job(event="schedule", branch="main", on_default_branch=True)

    assert classify(nightly) is WorkClass.SCHEDULED
    assert urgency(nightly) < urgency(job(event="push", branch="main", on_default_branch=True))


def test_the_scale_puts_a_merge_above_a_review_above_a_draft() -> None:
    """The ordering the whole feature exists for, asserted as an ordering rather than as
    three magic numbers — the values may be retuned, the sequence may not."""
    merge = urgency_of(WorkClass.DEFAULT_BRANCH)
    review = urgency_of(WorkClass.PULL_REQUEST)
    draft = urgency_of(WorkClass.DRAFT)

    assert merge > review > draft
    assert (merge, review, draft) == (10, 6, 4)


def test_a_draft_ranks_below_an_unreviewed_branch_push() -> None:
    """An author who marked their own work draft has told you it can wait; a branch push has
    said nothing either way."""
    assert urgency_of(WorkClass.DRAFT) < urgency_of(WorkClass.BRANCH)


def test_every_class_has_a_weight() -> None:
    """A class added without a weight would silently fall to the unknown default, which is
    exactly the sort of quiet wrong answer a ranking cannot afford."""
    assert set(URGENCY) == set(WorkClass)


def test_an_unclassifiable_job_is_neither_starved_nor_promoted() -> None:
    """An event this version has never heard of — a new GitHub trigger — lands between a pull
    request and a branch push rather than at either end."""
    unknown = job(event="merge_group", branch="gh-readonly-queue/main/x")

    assert urgency(unknown) == urgency_of(WorkClass.BRANCH)


def test_a_job_with_no_run_context_at_all_still_classifies() -> None:
    """What a job parsed from an older snapshot, or a forge that answered thinly, looks like.
    It must rank somewhere rather than raise."""
    assert classify(make_job(1)) is WorkClass.BRANCH
