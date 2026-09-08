"""How urgent a queued job is, decided from the run rather than declared by it.

Two jobs asking for the same labels are interchangeable to the fleet and are not
interchangeable to the people waiting. A merge to the default branch is blocking everyone;
a draft pull request is blocking its author, who has said out loud that it is not finished.

`classify` is the whole rule, and it reads only what the forge already told us about the run.
Nothing is configurable per job on purpose: a workflow that could declare its own importance
would declare the top of the scale, every time, and the ranking would mean nothing within a
week.

## What this does and does not do

It orders the queue **as read**, and nothing else. The daemon does not hand jobs to runners —
it starts runners, and GitHub decides which job each one picks up, in roughly the order they
were queued. So this cannot preempt a running job, cannot jump a draft behind a merge inside
GitHub's own dispatch, and is not a scheduler.

What it does is answer the question an operator actually has when the queue is deep: *is the
backlog work anybody is waiting on, or is it forty draft-PR matrix legs?* Those want
different responses, and a queue sorted only by age cannot tell them apart.

Deliberately not wired into `admission`: making a pool's weight move on its own, according to
what happened to be queued at that instant, is a behaviour change and belongs to its own
decision rather than arriving as a side effect of a display feature.
"""

from __future__ import annotations

from collections.abc import Mapping

from ghspot.domain.model.job import QueuedJob, WorkClass

#: What each class is worth, on a scale whose only meaning is the order it produces.
#:
#: The gaps are deliberate rather than decorative: `default-branch` sits well clear of
#: everything, and `draft` sits below the un-reviewed branch push, because an author who
#: marked their own work draft has told you it can wait.
URGENCY: Mapping[WorkClass, int] = {
    WorkClass.DEFAULT_BRANCH: 10,
    WorkClass.MANUAL: 8,
    WorkClass.PULL_REQUEST: 6,
    WorkClass.BRANCH: 5,
    WorkClass.DRAFT: 4,
    WorkClass.SCHEDULED: 2,
}

#: What an unrecognised class scores. Between a pull request and a branch push: an event this
#: version has never heard of should not be starved, and should not outrank a merge either.
UNKNOWN_URGENCY = 5


def classify(job: QueuedJob) -> WorkClass:
    """Which kind of work this job is.

    Ordered by how much the answer costs to be wrong. A draft is checked before a pull
    request because every draft *is* a pull request; the default branch is checked before
    anything else because a merge landing on it is the case worth getting right.
    """
    if job.event == "schedule":
        return WorkClass.SCHEDULED
    if job.event == "workflow_dispatch":
        return WorkClass.MANUAL
    if job.event == "pull_request" or job.event == "pull_request_target":
        return WorkClass.DRAFT if job.draft else WorkClass.PULL_REQUEST
    if job.on_default_branch:
        return WorkClass.DEFAULT_BRANCH
    return WorkClass.BRANCH


def urgency_of(work_class: WorkClass) -> int:
    return URGENCY.get(work_class, UNKNOWN_URGENCY)


def urgency(job: QueuedJob) -> int:
    """The job's rank, in one call, for callers that do not need the class itself."""
    return urgency_of(classify(job))
