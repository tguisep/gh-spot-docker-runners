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

#: Open pull requests read in one page when working out which runs are drafts. A repository
#: with more open pull requests than this, *and* queued jobs on the ones past the cut, sees
#: those classified as ordinary pull requests — which is the safe direction to be wrong in.
_OPEN_PULLS_PER_POLL = 100


@dataclass(slots=True)
class _CachedResponse:
    etag: str
    payload: Any


@dataclass(frozen=True, slots=True)
class _RunContext:
    """What a poll learned about the repository, to classify its runs with."""

    default_branch: str = ""
    draft_branches: frozenset[str] = frozenset()


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
        # Set once the pull request listing is refused, so a token without
        # `Pull requests: read` costs one 403 rather than one per tick forever.
        self._drafts_unavailable = False
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
        by_run = {run["id"]: run for run in runs if isinstance(run.get("id"), int)}
        limit = asyncio.Semaphore(_JOB_FETCH_CONCURRENCY)

        async def jobs_for(run_id: int) -> tuple[int, list[dict[str, Any]]]:
            async with limit:
                return run_id, await self._paginate(
                    f"/{repository.api_path}/actions/runs/{run_id}/jobs",
                    key="jobs",
                    params={"filter": "latest"},
                )

        fetched = await asyncio.gather(*(jobs_for(run_id) for run_id in by_run))

        # Paired with their run before anything else: the job says what it needs, the run says
        # who is waiting on it, and the classification wants both.
        waiting: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        seen: set[int] = set()
        for run_id, items in fetched:
            for item in items:
                job_id = item.get("id")
                if _is_waiting(item) and isinstance(job_id, int) and job_id not in seen:
                    seen.add(job_id)
                    waiting.append((item, by_run[run_id]))

        if not waiting:
            # Nothing queued, so nothing to classify. The two lookups below are skipped
            # entirely on a quiet repository, which is nearly all of the time.
            return []

        context = await self._run_context(repository, [run for _, run in waiting])
        return [_parse_job(item, run, repository, context) for item, run in waiting]

    async def _run_context(
        self, repository: RepositoryTarget, runs: Sequence[Mapping[str, Any]]
    ) -> _RunContext:
        """What the runs themselves do not say: which branch is default, and which are drafts.

        Two conditional GETs at most, and only when something is actually queued. Both are
        ETag-cached like every other read, so a repository whose default branch and open pull
        requests have not changed pays a `304` and nothing against the rate limit.

        Neither is allowed to fail the poll. Classification is a nicety on top of the queue;
        losing it costs a column, and raising here would cost the fleet its demand signal.
        """
        default_branch = await self._default_branch(repository)
        wants_drafts = any(str(run.get("event", "")).startswith("pull_request") for run in runs)
        drafts = await self._draft_branches(repository) if wants_drafts else frozenset()
        return _RunContext(default_branch=default_branch, draft_branches=drafts)

    async def _default_branch(self, repository: RepositoryTarget) -> str:
        try:
            payload = await self._request("GET", f"/{repository.api_path}")
        except ForgeError:
            return ""
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("default_branch") or "")

    async def _draft_branches(self, repository: RepositoryTarget) -> frozenset[str]:
        """Head refs of the open pull requests marked draft.

        Matched by branch rather than by number because that is what a workflow run carries:
        `pull_requests` on a run is empty for anything from a fork, so the number is not
        reliably there to match on.

        This is the one read that needs a permission the daemon does not otherwise ask for
        (`Pull requests: read`). Without it the call is refused once, remembered, and never
        made again — drafts then read as ordinary pull requests, which is a rank too high
        rather than work quietly demoted.
        """
        if self._drafts_unavailable:
            return frozenset()

        try:
            payload = await self._request(
                "GET",
                f"/{repository.api_path}/pulls",
                params={"state": "open", "per_page": str(_OPEN_PULLS_PER_POLL)},
            )
        except (ForgePermissionError, ForgeNotFoundError):
            self._drafts_unavailable = True
            return frozenset()
        except ForgeError:
            # Transient. Not remembered, so the next tick tries again.
            return frozenset()

        if not isinstance(payload, list):
            return frozenset()
        return frozenset(
            str(head.get("ref"))
            for item in payload
            if isinstance(item, dict) and item.get("draft")
            for head in [item.get("head") or {}]
            if isinstance(head, dict) and head.get("ref")
        )

    async def find_job_for_runner(
        self, repository: RepositoryTarget, runner_name: str, limit: int = MAX_RUNS_PER_POLL
    ) -> int | None:
        """Search recent runs for the job this runner took.

        Newest first, stopping at the first match: the runner name is unique per registration,
        so one hit is the answer and there is no reason to read the rest of the history.

        Bounded by ``limit`` runs. An unbounded walk of a busy repository is how a page nobody
        was watching spends the whole hourly budget.
        """
        if not runner_name:
            return None

        runs = await self._paginate(
            f"/{repository.api_path}/actions/runs",
            key="workflow_runs",
            limit=limit,
        )
        run_ids = [run["id"] for run in runs if isinstance(run.get("id"), int)]

        # Concurrent for the same reason list_queued_jobs is: done in sequence, thirty runs
        # is thirty round trips and the page appears to hang.
        gate = asyncio.Semaphore(_JOB_FETCH_CONCURRENCY)

        async def jobs_for(run_id: int) -> list[dict[str, Any]]:
            async with gate:
                return await self._paginate(
                    f"/{repository.api_path}/actions/runs/{run_id}/jobs",
                    key="jobs",
                    params={"filter": "latest"},
                )

        for items in await asyncio.gather(*(jobs_for(run_id) for run_id in run_ids)):
            for item in items:
                if item.get("runner_name") == runner_name and isinstance(item.get("id"), int):
                    return int(item["id"])
        return None

    async def job_logs(
        self, repository: RepositoryTarget, job_id: int, tail: int = 500
    ) -> str | None:
        """Download the forge's log for one job, or ``None`` when it has none yet.

        Two requests, deliberately not one. GitHub answers 302 with a signed URL on its blob
        store, and that URL is not GitHub: following the redirect with the Authorization
        header still attached would hand a credential that can register runners to a
        different host. So the redirect is read, and fetched with a clean client.

        A job still running answers 404 — the blob is written when the job finishes. That is
        not an error, it is the normal state of a job in progress, so it returns ``None``.
        """
        path = f"/repos/{repository.owner}/{repository.name}/actions/jobs/{job_id}/logs"
        headers = {"Authorization": f"Bearer {await self._auth.token()}"}

        try:
            response = await self._client.request(
                "GET", path, headers=headers, follow_redirects=False
            )
        except httpx.HTTPError as error:
            raise ForgeError(f"GET {path} failed: {error}") from error

        self._note_rate_limit(response)

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise self._translate(response, "GET", path)

        location = response.headers.get("Location")
        if location is None:
            # Not a redirect: some deployments answer with the body directly.
            return _last_lines(response.text, tail)

        try:
            async with httpx.AsyncClient(timeout=self._client.timeout) as plain:
                downloaded = await plain.get(location, follow_redirects=True)
                downloaded.raise_for_status()
        except httpx.HTTPError as error:
            raise ForgeError(f"downloading job {job_id} logs failed: {error}") from error

        return _last_lines(downloaded.text, tail)

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


def _last_lines(text: str, tail: int) -> str:
    """The end of a log, which is the part anyone is looking at.

    A completed job's log runs to megabytes; sending all of it to a terminal or a browser
    helps nobody. The byte order mark GitHub prefixes is dropped with it.
    """
    lines = text.lstrip("\ufeff").splitlines()
    if tail > 0 and len(lines) > tail:
        lines = lines[-tail:]
    return "\n".join(lines)


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


def _is_waiting(item: Mapping[str, Any]) -> bool:
    """Whether this job listing is one the daemon is expected to serve.

    Split out from parsing so a poll can decide whether anything is queued *before* paying
    for the two lookups that classify it.
    """
    if item.get("status") != "queued":
        return False
    if not isinstance(item.get("id"), int) or not isinstance(item.get("run_id"), int):
        return False
    # A job with no labels wants a GitHub-hosted runner and is none of our business.
    return any(str(label).strip() for label in item.get("labels", []))


def _parse_job(
    item: Mapping[str, Any],
    run: Mapping[str, Any],
    repository: RepositoryTarget,
    context: _RunContext,
) -> QueuedJob:
    """One queued job, with what its run says about who is waiting on it.

    Only called for items `_is_waiting` accepted, so the fields it needs are known present.
    """
    raw_labels = [str(label) for label in item.get("labels", []) if str(label).strip()]
    branch = str(run.get("head_branch") or "")

    return QueuedJob(
        id=int(item["id"]),
        run_id=int(item["run_id"]),
        repository=repository,
        labels=LabelSet.from_iterable(raw_labels),
        queued_at=_parse_time(item.get("started_at") or item.get("created_at")),
        workflow_name=str(item.get("workflow_name") or run.get("name") or ""),
        job_name=str(item.get("name") or ""),
        # The forge's own link, never one assembled here: an Enterprise install serves its
        # pages from a different host than its API, and a built URL would 404 there.
        url=str(item.get("html_url") or run.get("html_url") or ""),
        event=str(run.get("event") or ""),
        branch=branch,
        on_default_branch=bool(branch) and branch == context.default_branch,
        draft=branch in context.draft_branches,
    )


def _parse_time(value: object) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
