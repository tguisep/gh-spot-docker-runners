"""The dependency rule, enforced.

Ports and adapters only holds if the arrows point one way. Conventions rot; this test
does not. Each layer declares which other layers it is allowed to import from, and every
module in the package is parsed to check it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import ghspot

PACKAGE_ROOT = Path(ghspot.__file__).parent
PACKAGE_NAME = ghspot.__name__

#: layer -> the layers it may import from (besides itself and the package root).
ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "domain": frozenset(),
    "application": frozenset({"domain"}),
    "infrastructure": frozenset({"domain", "application"}),
    "interfaces": frozenset({"domain", "application"}),
}


def _layer_of(module: Path) -> str | None:
    relative = module.relative_to(PACKAGE_ROOT)
    top = relative.parts[0]
    return top if top in ALLOWED_IMPORTS else None


def _imported_layers(module: Path) -> set[str]:
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    layers: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Relative imports stay inside their own layer by construction.
            if node.level > 0 or node.module is None:
                continue
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue

        for name in names:
            parts = name.split(".")
            if parts[0] == PACKAGE_NAME and len(parts) > 1 and parts[1] in ALLOWED_IMPORTS:
                layers.add(parts[1])

    return layers


def _modules() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if _layer_of(p) is not None)


@pytest.mark.parametrize("module", _modules(), ids=lambda p: str(p.relative_to(PACKAGE_ROOT)))
def test_module_respects_the_dependency_rule(module: Path) -> None:
    layer = _layer_of(module)
    assert layer is not None

    permitted = ALLOWED_IMPORTS[layer] | {layer}
    violations = _imported_layers(module) - permitted

    assert not violations, (
        f"{module.relative_to(PACKAGE_ROOT)} is in the '{layer}' layer and may only import from "
        f"{sorted(permitted)}, but it imports from {sorted(violations)}."
    )


def test_the_domain_imports_no_third_party_io_libraries() -> None:
    """The domain must stay pure: no Docker client, no HTTP, no database driver."""
    forbidden = {"docker", "httpx", "requests", "sqlite3", "fastapi", "typer", "uvicorn"}
    offenders: dict[str, set[str]] = {}

    for module in _modules():
        if _layer_of(module) != "domain":
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        found = {
            name.split(".")[0]
            for node in ast.walk(tree)
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
        } & forbidden
        if found:
            offenders[str(module.relative_to(PACKAGE_ROOT))] = found

    assert not offenders, f"I/O libraries reached the domain: {offenders}"
