"""The GitHub adapter, against a mocked transport.

The behaviour worth pinning down here is not "it can parse JSON" but the two things the
daemon depends on: conditional requests keeping a poll cheap, and every HTTP failure arriving
above this layer as a domain error.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from ghspot.domain.errors import (
    ForgeAuthError,
    ForgeError,
    ForgeNotFoundError,
    ForgeRateLimitedError,
)
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.target import RepositoryTarget
from ghspot.infrastructure.github.client import GitHubClient

REPO = RepositoryTarget("tguisep", "gh-spot-docker-runners")
BASE = "https://api.github.com"
RUNNERS_URL = f"{BASE}/repos/tguisep/gh-spot-docker-runners/actions/runners"
JIT_URL = f"{RUNNERS_URL}/generate-jitconfig"
RUNS_URL = f"{BASE}/repos/tguisep/gh-spot-docker-runners/actions/runs"


@pytest.fixture
async def client() -> GitHubClient:
    return GitHubClient(token="ghp_test", max_attempts=1, backoff_seconds=0)


def _runner(
    runner_id: int, name: str, status: str = "online", busy: bool = False
) -> dict[str, object]:
    return {
        "id": runner_id,
        "name": name,
        "status": status,
        "busy": busy,
        "labels": [{"name": "self-hosted"}, {"name": "linux"}],
    }


# ---------------------------------------------------------------- registration


@respx.mock
async def test_minting_a_jit_config_sends_the_labels_and_returns_the_blob(
    client: GitHubClient,
) -> None:
    route = respx.post(JIT_URL).mock(
        return_value=httpx.Response(
            201,
            json={
                "runner": {"id": 42, "name": "ghspot-default-abc"},
                "encoded_jit_config": "BASE64BLOB",
            },
        )
    )

    registration = await client.create_jit_registration(
        REPO, "ghspot-default-abc", LabelSet.of("self-hosted", "linux")
    )

    assert registration.github_runner_id == 42
    assert registration.encoded_config == "BASE64BLOB"
    body = route.calls.last.request.content.decode()
    assert '"labels":["self-hosted","linux"]' in body.replace(" ", "")
    assert '"runner_group_id":1' in body.replace(" ", "")


async def test_the_registration_keeps_its_blob_out_of_reprs(client: GitHubClient) -> None:
    """A traceback in the daemon logs must not print a usable credential."""
    with respx.mock:
        respx.post(JIT_URL).mock(
            return_value=httpx.Response(
                201,
                json={"runner": {"id": 42, "name": "n"}, "encoded_jit_config": "SECRET"},
            )
        )
        registration = await client.create_jit_registration(REPO, "n", LabelSet.of("linux"))

    assert "SECRET" not in repr(registration)
    assert "42" in repr(registration)


@respx.mock
async def test_a_malformed_jit_response_is_rejected(client: GitHubClient) -> None:
    respx.post(JIT_URL).mock(return_value=httpx.Response(201, json={"runner": {"id": 1}}))

    with pytest.raises(ForgeError, match="no runner id or config"):
        await client.create_jit_registration(REPO, "n", LabelSet.of("linux"))


# ---------------------------------------------------------------- listing


@respx.mock
async def test_listing_runners_parses_status_and_labels(client: GitHubClient) -> None:
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 2,
                "runners": [_runner(1, "a"), _runner(2, "b", status="offline", busy=False)],
            },
        )
    )

    runners = await client.list_runners(REPO)

    assert [r.id for r in runners] == [1, 2]
    assert runners[0].is_online and not runners[1].is_online
    assert "self-hosted" in runners[0].labels


@respx.mock
async def test_listing_runners_follows_pagination(client: GitHubClient) -> None:
    page_one = [_runner(n, f"r{n}") for n in range(100)]
    respx.get(RUNNERS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"total_count": 101, "runners": page_one}),
            httpx.Response(200, json={"total_count": 101, "runners": [_runner(100, "r100")]}),
        ]
    )

    assert len(await client.list_runners(REPO)) == 101


@respx.mock
async def test_a_runner_github_reports_without_labels_still_parses(
    client: GitHubClient,
) -> None:
    """LabelSet refuses to be empty; the adapter must not raise on GitHub's edge cases."""
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"total_count": 1, "runners": [{"id": 7, "name": "x", "status": "offline"}]},
        )
    )

    runners = await client.list_runners(REPO)

    assert runners[0].id == 7
    assert len(runners[0].labels) == 1


# ---------------------------------------------------------------- deletion


@respx.mock
async def test_deleting_a_runner_that_is_already_gone_is_quiet(client: GitHubClient) -> None:
    """The reconciler deletes anything stale; racing another actor must not be an error."""
    respx.delete(f"{RUNNERS_URL}/42").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    await client.delete_runner(REPO, 42)


@respx.mock
async def test_deleting_a_runner_succeeds_on_204(client: GitHubClient) -> None:
    route = respx.delete(f"{RUNNERS_URL}/42").mock(return_value=httpx.Response(204))

    await client.delete_runner(REPO, 42)

    assert route.called


# ---------------------------------------------------------------- queued jobs


@respx.mock
async def test_queued_jobs_are_gathered_from_queued_and_in_progress_runs(
    client: GitHubClient,
) -> None:
    """A matrix leg queues after its run has started, so in_progress runs must be scanned."""
    respx.get(RUNS_URL, params={"status": "queued"}).mock(
        return_value=httpx.Response(200, json={"total_count": 1, "workflow_runs": [{"id": 1}]})
    )
    respx.get(RUNS_URL, params={"status": "in_progress"}).mock(
        return_value=httpx.Response(200, json={"total_count": 1, "workflow_runs": [{"id": 2}]})
    )
    respx.get(f"{RUNS_URL}/1/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 2,
                "jobs": [
                    {
                        "id": 10,
                        "run_id": 1,
                        "status": "queued",
                        "name": "build",
                        "labels": ["self-hosted", "linux"],
                        "created_at": "2026-08-26T12:00:00Z",
                    },
                    {"id": 11, "run_id": 1, "status": "completed", "labels": ["self-hosted"]},
                ],
            },
        )
    )
    respx.get(f"{RUNS_URL}/2/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "jobs": [
                    {
                        "id": 20,
                        "run_id": 2,
                        "status": "queued",
                        "name": "test",
                        "labels": ["self-hosted", "linux"],
                        "created_at": "2026-08-26T12:01:00Z",
                    }
                ],
            },
        )
    )

    jobs = await client.list_queued_jobs(REPO)

    assert sorted(job.id for job in jobs) == [10, 20]
    assert jobs[0].queued_at == datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    assert jobs[0].repository == REPO


@respx.mock
async def test_jobs_targeting_github_hosted_runners_are_ignored(client: GitHubClient) -> None:
    respx.get(RUNS_URL, params={"status": "queued"}).mock(
        return_value=httpx.Response(200, json={"total_count": 1, "workflow_runs": [{"id": 1}]})
    )
    respx.get(RUNS_URL, params={"status": "in_progress"}).mock(
        return_value=httpx.Response(200, json={"total_count": 0, "workflow_runs": []})
    )
    respx.get(f"{RUNS_URL}/1/jobs").mock(
        return_value=httpx.Response(
            200,
            json={"total_count": 1, "jobs": [{"id": 10, "run_id": 1, "status": "queued"}]},
        )
    )

    assert await client.list_queued_jobs(REPO) == []


# ---------------------------------------------------------------- conditional requests


@respx.mock
async def test_an_unchanged_list_is_served_from_the_etag_cache(client: GitHubClient) -> None:
    """The steady-state cost of polling. A 304 doesn't count against the rate limit."""
    route = respx.get(RUNNERS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={"total_count": 1, "runners": [_runner(1, "a")]},
                headers={"ETag": 'W/"abc"'},
            ),
            httpx.Response(304),
        ]
    )

    first = await client.list_runners(REPO)
    second = await client.list_runners(REPO)

    assert [r.id for r in first] == [r.id for r in second] == [1]
    assert route.calls[1].request.headers["If-None-Match"] == 'W/"abc"'


# ---------------------------------------------------------------- error translation


@respx.mock
async def test_a_rejected_token_is_an_auth_error(client: GitHubClient) -> None:
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )

    with pytest.raises(ForgeAuthError, match="rejected"):
        await client.list_runners(REPO)


@respx.mock
async def test_a_missing_scope_says_which_permission_is_missing(client: GitHubClient) -> None:
    """The most likely misconfiguration deserves an error that names the fix."""
    respx.post(JIT_URL).mock(
        return_value=httpx.Response(
            403,
            json={"message": "Resource not accessible"},
            headers={"X-RateLimit-Remaining": "10"},
        )
    )

    with pytest.raises(ForgeAuthError, match="Administration: read & write"):
        await client.create_jit_registration(REPO, "n", LabelSet.of("linux"))


@respx.mock
async def test_an_exhausted_rate_limit_is_distinguished_from_a_permission_problem(
    client: GitHubClient,
) -> None:
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1788000000"},
        )
    )

    with pytest.raises(ForgeRateLimitedError):
        await client.list_runners(REPO)

    assert await client.rate_limit_reset_at() == datetime.fromtimestamp(1788000000, tz=UTC)


@respx.mock
async def test_a_secondary_rate_limit_carries_the_retry_advice(client: GitHubClient) -> None:
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(
            429, json={"message": "slow down"}, headers={"Retry-After": "60"}
        )
    )

    with pytest.raises(ForgeRateLimitedError) as caught:
        await client.list_runners(REPO)

    assert caught.value.retry_after_seconds == 60.0


@respx.mock
async def test_a_missing_repository_is_a_not_found_error(client: GitHubClient) -> None:
    respx.get(RUNNERS_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

    with pytest.raises(ForgeNotFoundError):
        await client.list_runners(REPO)


@respx.mock
async def test_a_network_failure_becomes_a_domain_error() -> None:
    """Nothing above this layer should ever have to catch an httpx exception."""
    respx.get(RUNNERS_URL).mock(side_effect=httpx.ConnectError("no route to host"))

    async with GitHubClient(token="t", max_attempts=1, backoff_seconds=0) as client:
        with pytest.raises(ForgeError, match="no route to host"):
            await client.list_runners(REPO)


@respx.mock
async def test_a_server_error_is_retried_then_surfaces(client: GitHubClient) -> None:
    route = respx.get(RUNNERS_URL).mock(return_value=httpx.Response(502, text="bad gateway"))

    with pytest.raises(ForgeError, match="502"):
        await client.list_runners(REPO)

    assert route.call_count == 1  # max_attempts=1 on this fixture


@respx.mock
async def test_a_transient_server_error_is_retried_and_then_succeeds() -> None:
    respx.get(RUNNERS_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"total_count": 1, "runners": [_runner(1, "a")]}),
        ]
    )

    async with GitHubClient(token="t", max_attempts=3, backoff_seconds=0) as client:
        assert len(await client.list_runners(REPO)) == 1


def test_a_client_without_a_token_is_refused() -> None:
    with pytest.raises(ForgeAuthError, match="no GitHub token"):
        GitHubClient(token="")


# ---------------------------------------------------------------- job logs


JOB_LOGS_URL = f"{BASE}/repos/tguisep/gh-spot-docker-runners/actions/jobs/4242/logs"
BLOB_URL = "https://productionresultssa.blob.core.windows.net/actions-results/4242.txt"


@respx.mock
async def test_a_job_still_running_has_no_log_rather_than_an_error(
    client: GitHubClient,
) -> None:
    """GitHub answers 404 until the job finishes. That is its normal state, not a fault."""
    respx.get(JOB_LOGS_URL).mock(return_value=httpx.Response(404))

    assert await client.job_logs(REPO, 4242) is None


@respx.mock
async def test_the_credential_is_not_handed_to_the_blob_store(client: GitHubClient) -> None:
    """The redirect target is not GitHub. Following it with the Authorization header still
    attached would give a different host a token that can register runners."""
    respx.get(JOB_LOGS_URL).mock(return_value=httpx.Response(302, headers={"Location": BLOB_URL}))
    blob = respx.get(BLOB_URL).mock(return_value=httpx.Response(200, text="line one\n"))

    await client.job_logs(REPO, 4242)

    assert blob.called
    assert "Authorization" not in blob.calls.last.request.headers


@respx.mock
async def test_only_the_end_of_a_long_log_is_returned(client: GitHubClient) -> None:
    """A completed job's log runs to megabytes; the end is the part anyone is looking at."""
    respx.get(JOB_LOGS_URL).mock(return_value=httpx.Response(302, headers={"Location": BLOB_URL}))
    body = "\n".join(f"line {number}" for number in range(1000))
    respx.get(BLOB_URL).mock(return_value=httpx.Response(200, text=body))

    lines = (await client.job_logs(REPO, 4242, tail=10) or "").splitlines()

    assert len(lines) == 10
    assert lines[-1] == "line 999"


@respx.mock
async def test_the_byte_order_mark_github_sends_is_dropped(client: GitHubClient) -> None:
    respx.get(JOB_LOGS_URL).mock(return_value=httpx.Response(302, headers={"Location": BLOB_URL}))
    respx.get(BLOB_URL).mock(return_value=httpx.Response(200, text="﻿2026-08-28 hello"))

    assert (await client.job_logs(REPO, 4242) or "").startswith("2026-08-28")
