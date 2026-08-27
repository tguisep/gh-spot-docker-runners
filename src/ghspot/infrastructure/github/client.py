"""The GitHub adapter.

Two things here are load-bearing rather than incidental:

*Conditional requests.* Polling for queued jobs is the daemon's steady-state cost. Every GET
carries the ETag from last time, and a ``304 Not Modified`` does not count against the rate
limit — so an idle repository is nearly free to watch, however short the interval.

*Error translation.* Nothing above this layer sees an HTTP status. Transport failures become
:class:`~ghspot.domain.errors.ForgeError` subclasses at the boundary, which is what lets the
reconciler catch one type and keep going.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ghspot.domain.errors import (
    ForgeError,
    ForgeNotFoundError,
    ForgePermissionError,
    ForgeRateLimitedError,
    ForgeTokenRejectedError,
)
from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.ports.forge import ForgeRunner, JitRegistration
from ghspot.infrastructure.github.auth import StaticTokenProvider, TokenProvider

API_VERSION = "2022-11-28"
DEFAULT_BASE_URL = "https://api.github.com"

#: Runner groups other than the default need an org plan, so repository-scoped runners always
#: land in group 1.
DEFAULT_RUNNER_GROUP_ID = 1

#: How many job listings to fetch at once. Bounded rather than unlimited: a backlog can mean
#: sixty runs, and sixty simultaneous requests is how a daemon earns a secondary rate limit.
_JOB_FETCH_CONCURRENCY = 8

#: How many workflow runs to examine per poll. A backlog deeper than this is already beyond
#: what a single home server will clear, and the next tick picks up where this one stopped.
MAX_RUNS_PER_POLL = 30

_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


@dataclass(slots=True)
class _CachedResponse:
    etag: str
    payload: Any


class GitHubClient:
    """A :class:`~ghspot.domain.ports.forge.ForgeClient` backed by the GitHub REST API."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        *,
        auth: TokenProvider | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
    ) -> None:
        """Either ``token`` (a personal access token) or ``auth`` (any token provider).

        The token is resolved per request rather than baked into the client's headers,
        because a GitHub App installation token expires roughly hourly and would otherwise
        go stale underneath a long-running daemon.
        """
        if auth is None:
            auth = StaticTokenProvider(token or "")
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max(1, max_attempts)
        self._backoff_seconds = max(0.0, backoff_seconds)
        self._cache: dict[str, _CachedResponse] = {}
        self._rate_limit_reset: datetime | None = None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "ghspot",
            },
        )

    def describe_auth(self) -> str:
        """How this client authenticates, without revealing the credential."""
        return self._auth.describe()

    async def aclose(self) -> None:
        closer = getattr(self._auth, "aclose", None)
        if closer is not None:
            await closer()
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- the port ------------------------------------------------------------------

    async def create_jit_registration(
        self,
        repository: RepositoryTarget,
        name: str,
        labels: LabelSet,
        work_folder: str = "_work",
    ) -> JitRegistration:
        payload = await self._request(
            "POST",
            f"/{repository.api_path}/actions/runners/generate-jitconfig",
            json={
                "name": name,
                "runner_group_id": DEFAULT_RUNNER_GROUP_ID,
                "labels": labels.as_list(),
                "work_folder": work_folder,
            },
        )
        if not isinstance(payload, dict):
            raise ForgeError("unexpected response to generate-jitconfig")

        runner = payload.get("runner") or {}
        encoded = payload.get("encoded_jit_config")
        if not encoded or "id" not in runner:
            raise ForgeError("generate-jitconfig returned no runner id or config")

        return JitRegistration(
            github_runner_id=int(runner["id"]),
            name=str(runner.get("name", name)),
            encoded_config=str(encoded),
        )

    async def list_runners(self, repository: RepositoryTarget) -> Sequence[ForgeRunner]:
        items = await self._paginate(f"/{repository.api_path}/actions/runners", key="runners")
        return [_parse_runner(item) for item in items]

    async def delete_runner(self, repository: RepositoryTarget, github_runner_id: int) -> None:
        try:
            await self._request(
                "DELETE", f"/{repository.api_path}/actions/runners/{github_runner_id}"
            )
        except ForgeNotFoundError:
            # Already gone. The port promises this is quiet, because the reconciler calls it
            # on anything that looks stale and must not care who got there first.
            return

    async def list_queued_jobs(self, repository: RepositoryTarget) -> Sequence[QueuedJob]:
        """Jobs waiting for a runner.

        ``in_progress`` runs are examined alongside queued ones: a matrix leg is queued after
        its run has already started, and would otherwise be invisible until the run finished.
        """
        runs: list[dict[str, Any]] = []
        for status in ("queued", "in_progress"):
            runs.extend(
                await self._paginate(
                    f"/{repository.api_path}/actions/runs",
                    key="workflow_runs",
                    params={"status": status},
                    limit=MAX_RUNS_PER_POLL,
                )
            )

        # One request per run, and a busy repository has dozens. Done in sequence this was
        # the whole cost of a tick — measured at over three minutes on a host with two pools
        # and a backlog, against a fifteen second poll interval. The daemon cannot react to a
        # queue it takes minutes to read.
        run_ids = [run["id"] for run in runs if isinstance(run.get("id"), int)]
        limit = asyncio.Semaphore(_JOB_FETCH_CONCURRENCY)

        async def jobs_for(run_id: int) -> list[dict[str, Any]]:
            async with limit:
                return await self._paginate(
                    f"/{repository.api_path}/actions/runs/{run_id}/jobs",
                    key="jobs",
                    params={"filter": "latest"},
                )

        fetched = await asyncio.gather(*(jobs_for(run_id) for run_id in run_ids))

        jobs: list[QueuedJob] = []
        seen: set[int] = set()
        for items in fetched:
            for item in items:
                job = _parse_job(item, repository)
                if job is not None and job.id not in seen:
                    seen.add(job.id)
                    jobs.append(job)

        return jobs

    async def rate_limit_reset_at(self) -> datetime | None:
        return self._rate_limit_reset

    # -- transport -----------------------------------------------------------------

    async def _paginate(
        self,
        path: str,
        *,
        key: str,
        params: Mapping[str, str] | None = None,
        limit: int | None = None,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """Walk a paginated list endpoint, yielding items from the envelope's ``key``."""
        collected: list[dict[str, Any]] = []
        page = 1

        while True:
            query = {**(params or {}), "per_page": str(per_page), "page": str(page)}
            payload = await self._request("GET", path, params=query)
            if not isinstance(payload, dict):
                break

            items = payload.get(key) or []
            collected.extend(item for item in items if isinstance(item, dict))

            total = payload.get("total_count")
            reached_limit = limit is not None and len(collected) >= limit
            exhausted = len(items) < per_page or (
                isinstance(total, int) and len(collected) >= total
            )
            if reached_limit or exhausted:
                break
            page += 1

        return collected[:limit] if limit is not None else collected

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        cache_key = f"{method} {path} {sorted((params or {}).items())}"
        headers: dict[str, str] = {"Authorization": f"Bearer {await self._auth.token()}"}
        cached = self._cache.get(cache_key) if method == "GET" else None
        if cached is not None:
            headers["If-None-Match"] = cached.etag

        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.request(
                    method, path, params=params, json=json, headers=headers
                )
            except httpx.HTTPError as error:
                last_error = ForgeError(f"{method} {path} failed: {error}")
                await self._backoff(attempt)
                continue

            self._note_rate_limit(response)

            if response.status_code == 304 and cached is not None:
                return cached.payload

            if response.status_code in _RETRYABLE_STATUS:
                last_error = ForgeError(f"{method} {path} returned {response.status_code}")
                await self._backoff(attempt)
                continue

            if response.status_code >= 400:
                raise self._translate(response, method, path)

            payload = _decode(response)
            etag = response.headers.get("ETag")
            if method == "GET" and etag:
                self._cache[cache_key] = _CachedResponse(etag=etag, payload=payload)
            return payload

        raise last_error or ForgeError(f"{method} {path} failed")

    async def _backoff(self, attempt: int) -> None:
        """Wait before the next attempt, but never after the last one."""
        if attempt >= self._max_attempts - 1 or self._backoff_seconds == 0:
            return
        await asyncio.sleep(min(self._backoff_seconds * 2.0**attempt, 8.0))

    def _note_rate_limit(self, response: httpx.Response) -> None:
        reset = response.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            self._rate_limit_reset = datetime.fromtimestamp(int(reset), tz=UTC)

    def _translate(self, response: httpx.Response, method: str, path: str) -> ForgeError:
        status = response.status_code
        detail = _message(response)
        where = f"{method} {path}"

        if status in {401, 403}:
            remaining = response.headers.get("X-RateLimit-Remaining")
            retry_after = response.headers.get("Retry-After")
            if remaining == "0" or retry_after:
                return ForgeRateLimitedError(
                    f"{where}: rate limited ({detail})",
                    retry_after_seconds=float(retry_after) if retry_after else None,
                )
            if status == 401:
                return ForgeTokenRejectedError(f"{where}: the token was rejected ({detail})")
            return ForgePermissionError(
                f"{where}: forbidden ({detail}). The token likely lacks "
                "'Administration: read & write' on this repository."
            )
        if status == 404:
            return ForgeNotFoundError(f"{where}: not found ({detail})")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            return ForgeRateLimitedError(
                f"{where}: rate limited ({detail})",
                retry_after_seconds=float(retry_after) if retry_after else None,
            )
        return ForgeError(f"{where}: returned {status} ({detail})")


# -- parsing -----------------------------------------------------------------------


def _decode(response: httpx.Response) -> Any:
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as error:
        raise ForgeError(f"could not decode the response body: {error}") from error


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "message" in body:
        return str(body["message"])
    return response.text[:200]


def _parse_runner(item: Mapping[str, Any]) -> ForgeRunner:
    raw_labels = [
        str(label["name"])
        for label in item.get("labels", [])
        if isinstance(label, dict) and label.get("name")
    ]
    return ForgeRunner(
        id=int(item["id"]),
        name=str(item.get("name", "")),
        status=str(item.get("status", "offline")),
        busy=bool(item.get("busy", False)),
        # A runner GitHub reports with no labels cannot match anything; a placeholder keeps
        # the value object's invariant without pretending the runner is useful.
        labels=LabelSet.from_iterable(raw_labels or ["unlabelled"]),
    )


def _parse_job(item: Mapping[str, Any], repository: RepositoryTarget) -> QueuedJob | None:
    if item.get("status") != "queued":
        return None

    job_id = item.get("id")
    run_id = item.get("run_id")
    if not isinstance(job_id, int) or not isinstance(run_id, int):
        return None

    raw_labels = [str(label) for label in item.get("labels", []) if str(label).strip()]
    if not raw_labels:
        # A job with no labels wants a GitHub-hosted runner and is none of our business.
        return None

    return QueuedJob(
        id=job_id,
        run_id=run_id,
        repository=repository,
        labels=LabelSet.from_iterable(raw_labels),
        queued_at=_parse_time(item.get("started_at") or item.get("created_at")),
        workflow_name=str(item.get("workflow_name") or ""),
        job_name=str(item.get("name") or ""),
    )


def _parse_time(value: object) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
