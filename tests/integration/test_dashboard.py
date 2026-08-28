"""Serving the built dashboard alongside the API.

What matters here is that the dashboard cannot break the API: it mounts under its own path,
its client-side routes resolve, and its absence is normal rather than fatal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ghspot.interfaces.api import dashboard


@pytest.fixture
def built(tmp_path: Path) -> Path:
    """A directory shaped like `npm run build` output."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    return tmp_path


def serve(root: Path | None) -> TestClient:
    api = FastAPI()

    @api.get("/pools")
    async def pools() -> list[str]:
        return ["default"]

    dashboard.mount(api, override=str(root) if root else "/nonexistent")
    return TestClient(api)


# ---------------------------------------------------------------- finding it


def test_a_directory_without_an_index_is_not_a_dashboard(tmp_path: Path) -> None:
    """A half-finished build must read as 'not built', not as a site where every page 404s."""
    (tmp_path / "assets").mkdir()

    assert dashboard.find_root(str(tmp_path)) is None


def test_an_explicit_root_wins(built: Path) -> None:
    assert dashboard.find_root(str(built)) == built


def test_nothing_is_mounted_when_it_was_never_built() -> None:
    client = serve(None)

    assert client.get("/ui/").status_code == 404
    # The API is untouched, which is the point: a missing dashboard is not an outage.
    assert client.get("/pools").json() == ["default"]


# ---------------------------------------------------------------- serving it


def test_the_dashboard_is_served_under_its_own_path(built: Path) -> None:
    client = serve(built)

    page = client.get("/ui/")

    assert page.status_code == 200
    assert "id=root" in page.text


def test_the_root_redirects_to_it(built: Path) -> None:
    client = serve(built)

    answer = client.get("/", follow_redirects=False)

    assert answer.status_code == 307
    assert answer.headers["location"] == "/ui/"


def test_a_client_side_route_resolves_to_the_page(built: Path) -> None:
    """`/ui/runners` is a real page and not a real file. Refreshing it must not 404."""
    client = serve(built)

    page = client.get("/ui/runners")

    assert page.status_code == 200
    assert "id=root" in page.text


def test_a_missing_asset_stays_a_404(built: Path) -> None:
    """Answering a stylesheet request with HTML gives a blank page and no clue why."""
    client = serve(built)

    assert client.get("/ui/assets/gone.js").status_code == 404


def test_the_api_is_not_shadowed_by_the_dashboard(built: Path) -> None:
    """The dashboard routes on the same names the API uses; registration order decides."""
    client = serve(built)

    assert client.get("/pools").json() == ["default"]
