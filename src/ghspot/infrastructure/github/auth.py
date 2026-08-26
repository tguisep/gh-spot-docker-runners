"""How the daemon authenticates to GitHub.

Two modes, behind one interface. A personal access token is a fixed string; a GitHub App
mints a fresh installation token roughly every hour. The client asks for a token per request
rather than setting one header at construction, because the second kind expires underneath it.

The App flow, in full:

1. Sign a short-lived JWT with the app's private key — this proves *the app* is calling.
2. Exchange it for an installation access token — this proves *this installation* is calling,
   scoped to the repositories the installation covers.
3. Use that token until shortly before it expires, then repeat from step 1.

The private key never leaves this process, and the installation token it produces is already
short-lived. Neither ever enters a runner container: containers get a just-in-time config
blob and nothing else, whichever mode is in use.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
import jwt

from ghspot.domain.errors import ForgeAuthError, ForgeError
from ghspot.domain.model.target import RepositoryTarget

#: GitHub rejects a JWT whose lifetime exceeds ten minutes. Nine leaves room for drift.
JWT_LIFETIME = timedelta(minutes=9)

#: GitHub backdates `iat` in its own examples, to tolerate a host clock running fast.
JWT_BACKDATE = timedelta(seconds=60)

#: Refresh this long before the installation token actually expires, so a request in flight
#: at the boundary does not fail on a token that died between check and send.
REFRESH_MARGIN = timedelta(minutes=5)


class TokenProvider(Protocol):
    """Supplies the bearer token for a GitHub request."""

    async def token(self) -> str:
        """A currently valid token, minting or refreshing one if needed."""
        ...

    def describe(self) -> str:
        """A short, credential-free description for logs and ``ghspot doctor``."""
        ...


@dataclass(frozen=True, slots=True)
class StaticTokenProvider:
    """A personal access token: the same string every time."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ForgeAuthError("no GitHub token was provided")

    async def token(self) -> str:
        return self.value

    def describe(self) -> str:
        return "personal access token"

    def __repr__(self) -> str:
        return "StaticTokenProvider(value=***)"


@dataclass(slots=True)
class _Installation:
    token: str
    expires_at: datetime


class GitHubAppTokenProvider:
    """A GitHub App installation: a fresh token roughly every hour.

    Preferred over a personal access token where possible. The rate limit belongs to the
    installation rather than to a person, the permissions are the app's rather than
    everything the person can reach, and a leaked installation token expires on its own.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        *,
        installation_id: int | None = None,
        base_url: str = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
        discovery_repository: RepositoryTarget | None = None,
    ) -> None:
        if not app_id:
            raise ForgeAuthError("a GitHub App needs an app_id")
        if not private_key.strip():
            raise ForgeAuthError("a GitHub App needs a private key")

        self._app_id = str(app_id)
        self._private_key = private_key
        self._installation_id = installation_id
        self._discovery_repository = discovery_repository
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=20.0)
        self._current: _Installation | None = None
        # Two pools reconciling at once must not each mint a token; the first one to arrive
        # does the work and the rest use its result.
        self._lock = asyncio.Lock()
        self._validate_key()

    async def token(self) -> str:
        async with self._lock:
            if self._current is not None and not self._due_for_refresh(self._current):
                return self._current.token
            self._current = await self._mint()
            return self._current.token

    def describe(self) -> str:
        where = self._installation_id or "auto-discovered"
        return f"GitHub App {self._app_id} (installation {where})"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def expires_at(self) -> datetime | None:
        """When the cached installation token expires, for ``ghspot doctor``."""
        return self._current.expires_at if self._current else None

    # -- internals -----------------------------------------------------------------

    def _validate_key(self) -> None:
        """Fail at construction rather than on the first API call.

        A malformed key is a setup mistake, and it should surface from `ghspot doctor` with
        the reason rather than as an opaque 401 an hour later.
        """
        try:
            self._app_jwt()
        except (ValueError, TypeError, jwt.PyJWTError) as error:
            raise ForgeAuthError(
                f"the GitHub App private key could not be used to sign: {error}. "
                "It should be the unmodified PEM downloaded from the app's settings page."
            ) from error

    def _app_jwt(self) -> str:
        """A short-lived assertion that we hold the app's private key."""
        now = int(time.time())
        payload = {
            "iat": now - int(JWT_BACKDATE.total_seconds()),
            "exp": now + int(JWT_LIFETIME.total_seconds()),
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def _due_for_refresh(self, installation: _Installation) -> bool:
        return datetime.now(UTC) >= installation.expires_at - REFRESH_MARGIN

    async def _mint(self) -> _Installation:
        assertion = self._app_jwt()
        installation_id = self._installation_id or await self._discover(assertion)

        payload = await self._call(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            assertion,
            expected=201,
        )
        token = payload.get("token")
        if not token:
            raise ForgeAuthError("the installation token response contained no token")

        return _Installation(
            token=str(token),
            expires_at=_parse_expiry(payload.get("expires_at")),
        )

    async def _discover(self, assertion: str) -> int:
        """Find the installation id, so it need not be configured by hand.

        Cached for the process: an app is installed once and the id does not change.
        """
        if self._discovery_repository is not None:
            payload = await self._call(
                "GET", f"/{self._discovery_repository.api_path}/installation", assertion
            )
            found = payload.get("id")
            if isinstance(found, int):
                self._installation_id = found
                return found
            raise ForgeAuthError(
                f"the app is not installed on {self._discovery_repository}. "
                "Install it there, or set [github].installation_id."
            )

        payload = await self._call("GET", "/app/installations", assertion, envelope=False)
        installations = payload if isinstance(payload, list) else []
        if not installations:
            raise ForgeAuthError(
                "this GitHub App has no installations. Install it on your account first."
            )
        if len(installations) > 1:
            ids = ", ".join(str(item.get("id")) for item in installations)
            raise ForgeAuthError(
                f"this app has several installations ({ids}). "
                "Set [github].installation_id to choose one."
            )
        found = installations[0].get("id")
        if not isinstance(found, int):
            raise ForgeAuthError("could not read the installation id from GitHub's response")
        self._installation_id = found
        return found

    async def _call(
        self,
        method: str,
        path: str,
        assertion: str,
        *,
        expected: int = 200,
        envelope: bool = True,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {assertion}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "ghspot",
                },
            )
        except httpx.HTTPError as error:
            raise ForgeError(f"could not reach GitHub for app authentication: {error}") from error

        if response.status_code in {401, 403}:
            raise ForgeAuthError(
                f"GitHub rejected the app assertion ({_detail(response)}). "
                "Check that app_id matches the private key, and that the host clock is correct."
            )
        if response.status_code == 404:
            raise ForgeAuthError(
                f"{path} was not found ({_detail(response)}). "
                "Check the app id and that the app is installed."
            )
        if response.status_code not in {expected, 200, 201}:
            raise ForgeError(f"{method} {path} returned {response.status_code}")

        try:
            payload = response.json()
        except ValueError as error:
            raise ForgeError(f"could not decode GitHub's response: {error}") from error

        if envelope and not isinstance(payload, dict):
            raise ForgeError(f"unexpected response shape from {path}")
        return payload


def load_private_key(path: Path) -> str:
    """Read a GitHub App private key from a PEM file, with a useful error if it is not one."""
    resolved = path.expanduser()
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as error:
        raise ForgeAuthError(f"could not read the private key at {resolved}: {error}") from error

    if "PRIVATE KEY" not in text:
        raise ForgeAuthError(
            f"{resolved} does not look like a PEM private key. Download it from the "
            "app's settings page under 'Private keys'."
        )
    return text


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and "message" in body:
        return str(body["message"])
    return f"HTTP {response.status_code}"


def _parse_expiry(value: object) -> datetime:
    """When the installation token dies.

    A missing or unparseable value is treated as the shortest plausible lifetime rather than
    the longest: refreshing early is free, and using a dead token is not.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC) + REFRESH_MARGIN
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC) + REFRESH_MARGIN
