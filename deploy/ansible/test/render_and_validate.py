#!/usr/bin/env python3
"""Render the role's templates and load the result with ghspot's own parser.

The role restates the daemon's configuration schema in a Jinja template. Nothing fails when
those two drift: the role keeps rendering a file the daemon quietly ignores, and the drift
surfaces months later as a setting that does nothing. This is the check that turns that into
a red build.

Rendered with Ansible itself, not a stand-in Jinja environment, so the filters the template
actually uses are the ones under test.

    uv run python deploy/ansible/test/render_and_validate.py

Needs `ansible` on PATH and `ghspot` importable.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "deploy/ansible/roles/ghspot/templates"
VARS = Path(__file__).resolve().parent / "vars"

sys.path.insert(0, str(ROOT / "src"))
from ghspot.infrastructure.config.settings import Settings, host_cores, load  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def render(template: str, variables: Path, destination: Path) -> None:
    """Render one template through Ansible, as the role itself would."""
    result = subprocess.run(
        [
            "ansible",
            "localhost",
            "-c",
            "local",
            "-m",
            "ansible.builtin.template",
            "-a",
            f"src={TEMPLATES / template} dest={destination}",
            "-e",
            f"@{variables}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if not destination.exists():
        sys.exit(f"rendering {template} with {variables.name} failed:\n{result.stdout}")


def settings_for(variables: Path) -> Settings:
    with tempfile.TemporaryDirectory() as directory:
        rendered = Path(directory) / "config.toml"
        render("config.toml.j2", variables, rendered)
        return load(rendered)


def test_minimal() -> None:
    """Defaults the template fills in are the defaults the daemon would have chosen."""
    settings = settings_for(VARS / "minimal.yml")

    # Not None: max_containers defaults to the machine's core count, so "the template added
    # nothing" is now "the template left the daemon's own default alone" — an assertion that
    # still fails if the role starts inventing a ceiling of its own.
    check(
        settings.capacity.max_containers == host_cores(),
        "minimal: the template set a capacity limit of its own",
    )
    check(len(settings.pools) == 1, "minimal: expected one pool")
    pool = settings.pools[0]
    check(pool.spec.name == "default", "minimal: pool name lost")
    check(str(pool.spec.repository) == "tguisep/my-project", "minimal: repository lost")
    check(pool.template.image == "ghspot/runner:ubuntu-24.04", "minimal: image lost")
    check(pool.template.gpus is None, "minimal: a pool asked for a GPU it never wanted")
    check(pool.spec.requires_labels is None, "minimal: unexpected requires_labels")
    check(pool.spec.pm.value == "dynamic", "minimal: a pool is not managed dynamically")
    check(pool.spec.priority == 1, "minimal: a pool weighs more than the default")
    check(not pool.template.mount_docker_socket, "minimal: socket mounted by default")


def test_everything_round_trips() -> None:
    """Every key the template can emit survives the daemon reading it back."""
    settings = settings_for(VARS / "full.yml")

    check(settings.github.api_url == "https://github.example.com/api/v3", "full: api_url lost")
    check(settings.github.app_id == "123456", "full: app_id lost")
    check(settings.daemon.poll_interval == timedelta(seconds=30), "full: poll_interval lost")
    check(str(settings.daemon.state_db) == "/srv/ghspot/state.db", "full: state_db lost")
    check(settings.daemon.api_bind == "127.0.0.1:8770", "full: api_bind lost")

    limits = settings.capacity
    check(limits.max_containers == 8, "full: capacity.max_containers lost")
    check(limits.max_cpus == 12.0, "full: capacity.max_cpus lost")
    check(limits.max_memory_bytes == 24 * 1024**3, "full: capacity.max_memory lost")
    check(limits.cpu_high_water == 85, "full: capacity.cpu_high_water lost")
    check(limits.memory_high_water == 90, "full: capacity.memory_high_water lost")

    keep = settings.housekeeping
    check(keep.every == timedelta(hours=6), "full: housekeeping.every lost")
    check(keep.images_older_than == timedelta(hours=48), "full: images_older_than lost")
    check(keep.volumes is False, "full: housekeeping.volumes lost")
    check(keep.keep_build_cache == "5g", "full: keep_build_cache lost")

    pools = {pool.spec.name: pool for pool in settings.pools}
    check(set(pools) == {"ubuntu", "gpu", "rhel"}, f"full: pools are {sorted(pools)}")

    ubuntu = pools["ubuntu"]
    check(ubuntu.spec.pm.value == "dynamic", f"full: pm is {ubuntu.spec.pm}")
    check(ubuntu.spec.max_idle == 4, f"full: max_idle is {ubuntu.spec.max_idle}")
    check(ubuntu.spec.min_idle == 2, "full: min_idle lost")
    check(ubuntu.spec.max_runners == 6, "full: max_runners lost")
    check(ubuntu.spec.idle_timeout == timedelta(minutes=20), "full: idle_timeout lost")
    check(ubuntu.spec.max_job_duration == timedelta(hours=4), "full: max_job_duration lost")
    check(ubuntu.spec.max_launch_per_tick == 3, "full: max_launch_per_tick lost")
    check(ubuntu.template.cpus == 4.0, "full: cpus lost")
    check(ubuntu.template.memory == "8g", "full: memory lost")
    check(ubuntu.template.network == "ghspot-net", "full: network lost")
    check(
        dict(ubuntu.template.volumes) == {"/srv/cache": "/home/runner/.cache"},
        "full: volumes lost",
    )
    check(
        ubuntu.spec.labels.as_list() == ["self-hosted", "linux", "x64", "ubuntu-24.04"],
        "full: labels lost or reordered",
    )

    gpu = pools["gpu"]
    check(gpu.spec.pm.value == "ondemand", f"full: gpu pm is {gpu.spec.pm}")
    check(gpu.spec.priority == 10, f"full: priority is {gpu.spec.priority}")
    check(gpu.template.gpus == "all", f"full: gpus is {gpu.template.gpus!r}, expected 'all'")
    required = gpu.spec.requires_labels
    check(required is not None and required.as_list() == ["gpu-a100"], "full: requires_labels lost")

    rhel = pools["rhel"]
    check(rhel.template.gpus == ("0", "1"), f"full: gpu ids are {rhel.template.gpus!r}")
    check(str(rhel.spec.repository) == "tguisep/other-project", "full: second repository lost")


def test_housekeeping_can_be_turned_off() -> None:
    """`never` and omitted keys are read as 'do not', not as a parse error."""
    settings = settings_for(VARS / "housekeeping-off.yml")
    keep = settings.housekeeping

    check(keep.enabled is False, "off: housekeeping still enabled")
    check(keep.containers_older_than is None, "off: containers sweep still set")
    check(keep.images_older_than is None, "off: images sweep still set")
    check(keep.build_cache_older_than is None, "off: build cache sweep still set")
    check(keep.keep_build_cache is None, "off: keep_build_cache still set")


def test_the_credential_file_takes_both_forms() -> None:
    """A token, and an App key with the newlines systemd's EnvironmentFile cannot hold."""
    with tempfile.TemporaryDirectory() as directory:
        token_vars = Path(directory) / "token.yml"
        token_vars.write_text(
            "ghspot_github_token: github_pat_example\n"
            "ghspot_github_app_id: ''\n"
            "ghspot_github_app_private_key: ''\n"
        )
        rendered = Path(directory) / "env"
        render("env.j2", token_vars, rendered)
        body = rendered.read_text()
        check("GHSPOT_GITHUB_TOKEN=github_pat_example" in body, "env: token not written")
        check("APP_ID" not in body, "env: app variables written alongside a token")

        app_vars = Path(directory) / "app.yml"
        app_vars.write_text(
            "ghspot_github_token: ''\n"
            "ghspot_github_app_id: '123456'\n"
            'ghspot_github_app_private_key: "-----BEGIN PRIVATE KEY-----\\nSECOND\\n"\n'
        )
        rendered_app = Path(directory) / "env-app"
        render("env.j2", app_vars, rendered_app)
        body = rendered_app.read_text()
        check("GHSPOT_GITHUB_APP_ID=123456" in body, "env: app id not written")
        check("GHSPOT_WEB_ROOT" not in body, "env: a web root nobody asked for")
        check("\\n" in body, "env: newlines not escaped for systemd's EnvironmentFile")
        check("GHSPOT_GITHUB_TOKEN" not in body, "env: token written alongside an App")


def test_pools_can_be_rendered_one_file_each() -> None:
    """Directory mode: the main file carries only `include`, and each pool is its own file.

    Rendered the way the role renders them, then loaded together — which is the only way to
    find out that the two templates still agree on the schema.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pools_d = root / "pools.d"
        pools_d.mkdir()

        variables = root / "vars.yml"
        variables.write_text(
            (VARS / "full.yml").read_text()
            + f"\nghspot_pools_in_directory: true\nghspot_pools_directory: {pools_d}\n"
        )

        main = root / "config.toml"
        render("config.toml.j2", variables, main)
        body = main.read_text()
        check("include = " in body, "directory: the main file has no include")
        check("[[pool]]" not in body, "directory: pools were written inline as well")
        # A global table belongs to the host, not to wherever the pools happen to be
        # written. Merging the two features put it inside the pool-layout branch once.
        check("[capacity]" in body, "directory: the host's capacity limits were lost")

        # The role loops the template over each pool; here that loop is the test's.
        for name in ("ubuntu", "gpu", "rhel"):
            one = root / f"{name}.yml"
            one.write_text(
                (VARS / "full.yml").read_text()
                + f"\npool: \"{{{{ ghspot_pools | selectattr('name', 'equalto', '{name}') "
                '| first }}"\n'
            )
            render("pool.toml.j2", one, pools_d / f"{name}.toml")

        settings = load(main)
        found = {pool.spec.name for pool in settings.pools}
        check(found == {"ubuntu", "gpu", "rhel"}, f"directory: pools are {sorted(found)}")

        # Compared against the inline form key by key, because two templates for one schema
        # is two chances to drift — and the drift is silent: a pool file missing `pm` still
        # loads, and the pool quietly runs in a mode nobody chose.
        inline = {pool.spec.name: pool for pool in settings_for(VARS / "full.yml").pools}
        from_files = {pool.spec.name: pool for pool in settings.pools}

        for name, expected in inline.items():
            got = from_files[name]
            for attribute in (
                "pm",
                "min_idle",
                "max_idle",
                "max_runners",
                "idle_timeout",
                "max_job_duration",
                "max_launch_per_tick",
                "priority",
                "requires_labels",
            ):
                if not hasattr(expected.spec, attribute):
                    continue
                check(
                    getattr(got.spec, attribute) == getattr(expected.spec, attribute),
                    f"directory: {name}.{attribute} is {getattr(got.spec, attribute)!r}, "
                    f"but the inline form gives {getattr(expected.spec, attribute)!r}",
                )
            check(
                got.template == expected.template,
                f"directory: {name}'s container differs from the inline form",
            )


def test_the_dashboard_location_is_only_written_when_set() -> None:
    """The daemon finds the packaged dashboard on its own; the variable is the override."""
    with tempfile.TemporaryDirectory() as directory:
        variables = Path(directory) / "web.yml"
        variables.write_text(
            "ghspot_github_token: t\n"
            "ghspot_github_app_id: ''\n"
            "ghspot_github_app_private_key: ''\n"
            "ghspot_web_root: /srv/ghspot/web\n"
        )
        rendered = Path(directory) / "env"
        render("env.j2", variables, rendered)

        check(
            "GHSPOT_WEB_ROOT=/srv/ghspot/web" in rendered.read_text(),
            "env: web root lost",
        )


def main() -> int:
    for test in (
        test_minimal,
        test_everything_round_trips,
        test_housekeeping_can_be_turned_off,
        test_the_credential_file_takes_both_forms,
        test_pools_can_be_rendered_one_file_each,
        test_the_dashboard_location_is_only_written_when_set,
    ):
        test()
        print(f"  {'FAIL' if failures else 'ok  '}  {test.__name__}")
        if failures:
            for failure in failures:
                print(f"        {failure}")
            return 1
    print("the role renders configuration the daemon accepts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
