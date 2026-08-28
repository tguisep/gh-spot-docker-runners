"""Serving the built dashboard alongside the API.

The dashboard is optional in the strongest sense: the daemon does not need it, does not
build it, and starts normally when it is absent. This module finds it if it is there.

It is served under ``/ui`` rather than ``/``. The dashboard's own routes are named after the
same things the API's are — ``/runners``, ``/pools`` — so mounting it at the root would have
the two shadow each other, and which one won would depend on registration order.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

MOUNT = "/ui"

PACKAGED = Path("/usr/share/ghspot/web")
"""Where the .deb installs it."""

IN_TREE = Path(__file__).resolve().parents[4] / "web" / "dist"
"""Where `npm run build` leaves it in a checkout, so a developer needs no install step."""


def find_root(override: str | None = None) -> Path | None:
    """The built dashboard, or ``None`` when it was never built.

    A directory without an ``index.html`` is not a dashboard, so a half-finished build reads
    as "not built" rather than as a site where every page is a 404.

    An explicit location — the argument, or ``GHSPOT_WEB_ROOT`` — is the whole answer, right
    or wrong. Falling back from it would mean a typo in the variable silently served some
    other directory, and the operator would be looking at a dashboard they did not point at.
    """
    explicit = override or os.environ.get("GHSPOT_WEB_ROOT")
    if explicit:
        named = Path(explicit).expanduser()
        return named if (named / "index.html").is_file() else None

    for candidate in (PACKAGED, IN_TREE):
        if (candidate / "index.html").is_file():
            return candidate
    return None


class SinglePageFiles(StaticFiles):
    """Static files that fall back to ``index.html`` for unknown paths.

    The dashboard routes in the browser, so ``/ui/runners`` is a real page but not a real
    file. Without this, opening one directly — or refreshing it — is a 404. Only genuine
    misses fall back; a missing asset stays a 404, because silently answering a stylesheet
    request with HTML produces a blank page and no clue why.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404 or path.startswith("assets/"):
                raise
            return await super().get_response("index.html", scope)


def mount(api: FastAPI, override: str | None = None) -> Path | None:
    """Attach the dashboard if it was built. Returns where it was found, or ``None``.

    Called after every API route is registered, so nothing here can shadow one.
    """
    root = find_root(override)
    if root is None:
        return None

    api.mount(MOUNT, SinglePageFiles(directory=root, html=True), name="dashboard")

    @api.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url=f"{MOUNT}/")

    return root
