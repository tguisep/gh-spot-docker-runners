"""GitHub App authentication.

A real RSA keypair is generated per session so the JWT is actually signed and actually
verified — a test that stubs the signing step would not catch a malformed key, which is the
most likely thing to go wrong in this path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ghspot.domain.errors import ForgeAuthError, ForgeError
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.target import RepositoryTarget
from ghspot.infrastructure.github.auth import (
    REFRESH_MARGIN,
    GitHubAppTokenProvider,
    StaticTokenProvider,
    load_private_key,
)
from ghspot.infrastructure.github.client import GitHubClient

BASE = "https://api.github.com"
REPO = RepositoryTarget("tguisep", "gh-spot-docker-runners")
TOKEN_URL = f"{BASE}/app/installations/42/access_tokens"


@pytest.fixture(scope="session")
def keypair() -> tuple[str, str]:
    """A real 2048-bit RSA key, as PEM private and public."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


@pytest.fixture
def private_key(keypair: tuple[str, str]) -> str:
    return keypair[0]


def token_response(token: str = "ghs_installation", minutes: int = 60) -> httpx.Response:
    expires = datetime.now(UTC) + timedelta(minutes=minutes)
    return httpx.Response(
        201,
        json={"token": token, "expires_at": expires.isoformat().replace("+00:00", "Z")},
    )


# ---------------------------------------------------------------- the exchange


@respx.mock
async def test_the_app_exchanges_a_signed_jwt_for_an_installation_token(
    keypair: tuple[str, str],
) -> None:
    private, public = keypair
    route = respx.post(TOKEN_URL).mock(return_value=token_response())
    provider = GitHubAppTokenProvider(app_id="123456", private_key=private, installation_id=42)

    assert await provider.token() == "ghs_installation"

    # The assertion GitHub received must verify against the app's public key, and say who
    # it is from. Anything less and this test would pass on a broken signer.
    assertion = route.calls.last.request.headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(assertion, public, algorithms=["RS256"], options={"verify_exp": True})
    assert claims["iss"] == "123456"
    assert claims["exp"] - claims["iat"] <= 600  # GitHub rejects anything longer


@respx.mock
async def test_the_token_is_reused_until_it_nears_expiry(private_key: str) -> None:
    """One exchange per hour, not one per request — the daemon makes many."""
    route = respx.post(TOKEN_URL).mock(return_value=token_response(minutes=60))
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    for _ in range(5):
        assert await provider.token() == "ghs_installation"

    assert route.call_count == 1


@respx.mock
async def test_a_token_near_expiry_is_refreshed_early(private_key: str) -> None:
    """Refreshing at the boundary would fail a request already in flight."""
    respx.post(TOKEN_URL).mock(
        side_effect=[
            token_response("first", minutes=int(REFRESH_MARGIN.total_seconds() // 60) - 1),
            token_response("second", minutes=60),
        ]
    )
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    assert await provider.token() == "first"
    assert await provider.token() == "second"


@respx.mock
async def test_concurrent_callers_mint_only_one_token(private_key: str) -> None:
    """Several pools reconcile at once; they must not each start an exchange."""
    import asyncio

    route = respx.post(TOKEN_URL).mock(return_value=token_response())
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    results = await asyncio.gather(*(provider.token() for _ in range(8)))

    assert set(results) == {"ghs_installation"}
    assert route.call_count == 1


@respx.mock
async def test_a_response_without_an_expiry_is_treated_as_short_lived(
    private_key: str,
) -> None:
    """Refreshing early is free; using a dead token is not."""
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "t"}))
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    await provider.token()

    assert provider.expires_at is not None
    assert provider.expires_at <= datetime.now(UTC) + REFRESH_MARGIN + timedelta(seconds=5)


# ---------------------------------------------------------------- discovery


@respx.mock
async def test_the_installation_is_discovered_from_the_first_repository(
    private_key: str,
) -> None:
    """So an operator need not go hunting for an installation id."""
    respx.get(f"{BASE}/repos/tguisep/gh-spot-docker-runners/installation").mock(
        return_value=httpx.Response(200, json={"id": 42})
    )
    respx.post(TOKEN_URL).mock(return_value=token_response())
    provider = GitHubAppTokenProvider(
        app_id="1", private_key=private_key, discovery_repository=REPO
    )

    assert await provider.token() == "ghs_installation"
    assert "installation 42" in provider.describe()


@respx.mock
async def test_an_app_not_installed_on_the_repository_says_so(private_key: str) -> None:
    respx.get(f"{BASE}/repos/tguisep/gh-spot-docker-runners/installation").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    provider = GitHubAppTokenProvider(
        app_id="1", private_key=private_key, discovery_repository=REPO
    )

    with pytest.raises(ForgeAuthError, match="app is installed"):
        await provider.token()


@respx.mock
async def test_a_single_installation_is_found_without_a_repository(private_key: str) -> None:
    respx.get(f"{BASE}/app/installations").mock(return_value=httpx.Response(200, json=[{"id": 42}]))
    respx.post(TOKEN_URL).mock(return_value=token_response())
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key)

    assert await provider.token() == "ghs_installation"


@respx.mock
async def test_several_installations_require_an_explicit_choice(private_key: str) -> None:
    """Guessing which one to use would silently start runners on the wrong account."""
    respx.get(f"{BASE}/app/installations").mock(
        return_value=httpx.Response(200, json=[{"id": 42}, {"id": 43}])
    )
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key)

    with pytest.raises(ForgeAuthError, match="installation_id"):
        await provider.token()


@respx.mock
async def test_an_app_with_no_installations_says_so(private_key: str) -> None:
    respx.get(f"{BASE}/app/installations").mock(return_value=httpx.Response(200, json=[]))
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key)

    with pytest.raises(ForgeAuthError, match="no installations"):
        await provider.token()


# ---------------------------------------------------------------- setup mistakes


def test_a_key_that_cannot_sign_fails_at_construction() -> None:
    """A setup mistake should surface from `ghspot doctor`, not as a 401 an hour later."""
    with pytest.raises(ForgeAuthError, match="could not be used to sign"):
        GitHubAppTokenProvider(app_id="1", private_key="-----BEGIN PRIVATE KEY-----\nnope\n")


@pytest.mark.parametrize(
    ("app_id", "key", "expected"),
    [("", "x", "app_id"), ("1", "   ", "private key")],
)
def test_missing_app_credentials_are_refused(app_id: str, key: str, expected: str) -> None:
    with pytest.raises(ForgeAuthError, match=expected):
        GitHubAppTokenProvider(app_id=app_id, private_key=key)


@respx.mock
async def test_a_rejected_assertion_points_at_the_likely_causes(private_key: str) -> None:
    """Wrong app id and a skewed clock are the two things that actually cause this."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"message": "A JSON web token could not be decoded"})
    )
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    with pytest.raises(ForgeAuthError, match="clock"):
        await provider.token()


@respx.mock
async def test_a_network_failure_is_a_domain_error(private_key: str) -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("no route"))
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    with pytest.raises(ForgeError, match="no route"):
        await provider.token()


def test_loading_a_key_that_is_not_a_pem_says_where_to_get_one(tmp_path: Path) -> None:
    path = tmp_path / "app.pem"
    path.write_text("this is not a key")

    with pytest.raises(ForgeAuthError, match="Private keys"):
        load_private_key(path)


def test_loading_a_missing_key_reports_the_path(tmp_path: Path) -> None:
    with pytest.raises(ForgeAuthError, match="could not read"):
        load_private_key(tmp_path / "absent.pem")


def test_a_valid_pem_loads(tmp_path: Path, private_key: str) -> None:
    path = tmp_path / "app.pem"
    path.write_text(private_key)

    assert "PRIVATE KEY" in load_private_key(path)


# ---------------------------------------------------------------- credential hygiene


def test_neither_provider_reveals_its_credential(private_key: str) -> None:
    """A traceback in the journal must not print something usable."""
    static = StaticTokenProvider("ghp_secret_value")
    app = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    assert "ghp_secret_value" not in repr(static)
    assert "PRIVATE KEY" not in repr(app)
    assert "PRIVATE KEY" not in app.describe()
    assert static.describe() == "personal access token"


def test_an_empty_static_token_is_refused() -> None:
    with pytest.raises(ForgeAuthError, match="no GitHub token"):
        StaticTokenProvider("")


# ---------------------------------------------------------------- through the client


@respx.mock
async def test_the_client_authenticates_every_request_with_the_app_token(
    private_key: str,
) -> None:
    """The header is set per request, because an installation token expires under us."""
    respx.post(TOKEN_URL).mock(return_value=token_response("ghs_live"))
    runners = respx.get(f"{BASE}/repos/tguisep/gh-spot-docker-runners/actions/runners").mock(
        return_value=httpx.Response(200, json={"total_count": 0, "runners": []})
    )
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    async with GitHubClient(auth=provider, backoff_seconds=0) as client:
        await client.list_runners(REPO)
        assert client.describe_auth().startswith("GitHub App")

    assert runners.calls.last.request.headers["Authorization"] == "Bearer ghs_live"


@respx.mock
async def test_a_personal_access_token_still_works_unchanged() -> None:
    """The simpler mode must not regress: it is how most people will start."""
    route = respx.get(f"{BASE}/repos/tguisep/gh-spot-docker-runners/actions/runners").mock(
        return_value=httpx.Response(200, json={"total_count": 0, "runners": []})
    )

    async with GitHubClient(token="ghp_classic", backoff_seconds=0) as client:
        await client.list_runners(REPO)
        assert client.describe_auth() == "personal access token"

    assert route.calls.last.request.headers["Authorization"] == "Bearer ghp_classic"


@respx.mock
async def test_a_jit_config_can_be_minted_with_app_auth(private_key: str) -> None:
    """The one call that matters: App credentials must be able to register a runner."""
    respx.post(TOKEN_URL).mock(return_value=token_response())
    respx.post(
        f"{BASE}/repos/tguisep/gh-spot-docker-runners/actions/runners/generate-jitconfig"
    ).mock(
        return_value=httpx.Response(
            201, json={"runner": {"id": 7, "name": "n"}, "encoded_jit_config": "BLOB"}
        )
    )
    provider = GitHubAppTokenProvider(app_id="1", private_key=private_key, installation_id=42)

    async with GitHubClient(auth=provider, backoff_seconds=0) as client:
        registration = await client.create_jit_registration(REPO, "n", LabelSet.of("linux"))

    assert registration.github_runner_id == 7
    assert registration.encoded_config == "BLOB"
