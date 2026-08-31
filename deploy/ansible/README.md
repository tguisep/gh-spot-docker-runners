# Deploying with Ansible

An Ansible role that installs ghspot on a host: the package, the configuration, the
credential, the runner images, and the service.

## Quick start

```bash
cd deploy/ansible
cp inventory/hosts.example.ini inventory/hosts.ini
cp inventory/group_vars/runners/main.example.yml inventory/group_vars/runners/main.yml

# The credential goes in the vault, and nowhere else.
ansible-vault create inventory/group_vars/runners/vault.yml

$EDITOR inventory/hosts.ini inventory/group_vars/runners/main.yml
ansible-playbook -i inventory/hosts.ini playbook.yml --ask-vault-pass
```

The run ends by calling `ghspot doctor` on the host and failing if it is unhappy — so a
green run means the daemon can actually do its job, not merely that files were copied.

## What it does

1. Refuses to start if the configuration is incoherent, before touching the host.
2. Installs Docker, if you ask it to. It does not by default: a host that runs runners
   usually has Docker already, and installing it under someone is rude.
3. Installs the `.deb` from a GitHub release — or one you built, via `ghspot_deb_local`.
4. Renders `/etc/ghspot/config.toml` and `/etc/ghspot/env` (`0640`, `root:ghspot`).
5. Builds the runner images with `ghspot image build`, from the sources the package
   installs, skipping any already present.
6. Enables and starts the service, then runs `ghspot doctor`.

## Variables worth knowing

| Variable | Default | |
|---|---|---|
| `ghspot_pools` | `[]` | **Required.** The pools this host serves |
| `ghspot_github_token` | `""` | A personal access token — or use the App variables |
| `ghspot_github_app_id` / `_private_key` | `""` | A GitHub App instead |
| `ghspot_version` | `latest` | Pin a release, e.g. `0.2.0` |
| `ghspot_deb_local` | `""` | Install a locally built package instead of a release |
| `ghspot_images` | `[ubuntu-24.04]` | Which runner images to build |
| `ghspot_install_docker` | `false` | Install Docker as well |
| `ghspot_service_state` | `started` | `stopped` to configure without running |
| `ghspot_api_bind` | `""` | Serve the API and the dashboard, e.g. `127.0.0.1:8770` |
| `ghspot_web_root` | `""` | Where the dashboard was built, when not the packaged location |

Everything else is in [`roles/ghspot/defaults/main.yml`](roles/ghspot/defaults/main.yml).

## Credentials

Exactly one of a token or an App, and the role refuses both or neither rather than picking.

They belong in `ansible-vault`. The task that writes `/etc/ghspot/env` sets `no_log`, so the
credential does not appear in output even at `-vvv` — the point of that file is that it holds
a secret, and Ansible logs template contents by default.

Setting either up, with the exact permissions:
[`docs/authentication.md`](../../docs/authentication.md).

## Tests

```bash
uvx --from ansible-lint ansible-lint deploy/ansible
uv run python deploy/ansible/test/render_and_validate.py
```

The second is the one that matters. The role restates the daemon's configuration schema in a
Jinja template, and **nothing fails when the two drift** — the role keeps rendering a file the
daemon quietly ignores, and it surfaces months later as a setting that does nothing.

So it renders the templates with Ansible itself, using the filters the template actually
uses, and loads the result with `ghspot`'s own parser. Fixtures in `test/vars/` cover a
minimal pool, every key the template can emit, and housekeeping turned off — the last
because `never` and omitted keys take different paths.

Confirmed to fail on real drift: dropping `requires_labels` from the template reports
`full: requires_labels lost`, and renaming `keep_build_cache` reports
`full: keep_build_cache lost`.

### Molecule

```bash
cd roles/ghspot
uvx --from molecule --with 'molecule-plugins[docker]' --with ansible-core --with docker \
  molecule test
```

Converges the role against a container that actually runs systemd, converges it **again** and
fails if anything changed, then asserts what it was supposed to have done: the package
installed, the service account in the `docker` group, the unit systemd parsed pointing at the
installed binary and reading `/etc/ghspot/env`, and the daemon accepting the configuration the
role rendered.

It installs the **real release package**, so the path under test is the documented one. It
does not start the daemon or build runner images: starting it needs a real repository and
credential, and four images take longer than the rest of CI together. Both are covered
elsewhere — the packaging workflow installs the `.deb` on a clean system, and the
runner-images workflow builds every variant.

Finding it made along the way: the role was **not idempotent**. It downloaded the package to
`/tmp` and deleted it, so every run fetched 35 MB again and reported a change. Packages are
now cached under `/var/cache/ghspot`, named for their version.

CI runs all of this on any change to `deploy/ansible/` **or to the daemon's configuration
module**, so a key added on one side without the other is caught by whichever moved first.

## GPUs

Set `gpus` on a pool's container, the same way the daemon takes it:

```yaml
ghspot_pools:
  - name: gpu
    repository: you/your-project
    labels: [self-hosted, linux, x64, gpu-a100]
    # Without this a plain CPU job lands here and burns the GPU: label matching is a
    # subset rule, so extra labels on a pool are ignored by jobs that never asked.
    requires_labels: [gpu-a100]
    max_runners: 1
    container:
      image: ghspot/runner:ubuntu-24.04
      gpus: all          # or a count: 1  —  or ids: ["0", "1"]
```

The role does **not** install the NVIDIA Container Toolkit. That is driver territory and
varies too much per host to do behind someone's back. Install it first — see
[GPUs in the operations guide](../../docs/operations.md#gpus) — and the run will confirm it,
since `ghspot doctor` fails the play when a pool asks for GPUs the host cannot provide.

Give GPU work its own pool, and set `max_runners` to the number of GPUs you have: two runners
sharing one GPU both run, and both are slower than either alone.

## The dashboard

The package carries a web dashboard covering the same ground as the CLI. The role installs
nothing extra for it — it rides in the `.deb` — but it is only *served* when the API is
bound:

```yaml
ghspot_api_bind: "127.0.0.1:8770"
```

Then it is at `http://127.0.0.1:8770/ui`, on the host. Neither it nor the API has any
authentication, which is why the default is unset and why the address above is loopback.
Reach it over a tunnel:

```bash
ssh -L 8770:localhost:8770 the-host      # then open http://localhost:8770/ui
```

A run reports which of three states the host ended in, so a deploy says what happened
instead of leaving a URL to guess at:

| After a run | What it means |
|---|---|
| `dashboard: http://…/ui` | Bound and installed |
| `the API is bound … but no dashboard is installed` | The package was built without `npm`. The API itself is unaffected |
| `a dashboard is installed but ghspot_api_bind is unset` | Nothing is served; set the bind address |

`ghspot_web_root` overrides where the daemon looks, for a build kept somewhere other than the
packaged location. Leave it empty otherwise: an explicit value is authoritative, so a typo
serves nothing rather than quietly falling back to a different directory.

## Building images takes a while

Each variant is a couple of gigabytes and several minutes. The role skips any already on the
host, so a second run is quick. `ghspot_images_force: true` rebuilds regardless — do that
after upgrading, since a new release may expect a newer image.

Set `ghspot_build_images: false` if you build them some other way. The daemon cannot start a
runner without one, and `doctor` will say so.

## Upgrading

```bash
ansible-playbook -i inventory/hosts.ini playbook.yml --ask-vault-pass \
  -e ghspot_version=0.3.0 -e ghspot_images_force=true
```

The configuration is rewritten from the role's variables each run, so edits made directly on
the host are overwritten. That is the point of a role — change the variables.

## One file per pool

Set `ghspot_pools_in_directory: true` and the role writes `/etc/ghspot/pools.d/<name>.toml`
per pool, with the main file carrying only `include`. It also **removes** the file of a pool
you delete from the inventory — the include is a glob, so nothing else would.

Files are merged, not overridden: two pools of one name is a fatal error naming both files.
The same arrangement, and the same rules, as `php-fpm.d`.

## What it does not do

- **It does not add your user to the `docker` group.** The package puts the *service*
  account there, which is what the daemon needs. Doing it for a human is your call.
- **It does not open ports.** `ghspot_api_bind` defaults to unset, so neither the API nor
  the dashboard is served and nothing listens. Neither has authentication — keep it on
  localhost.
- **It does not manage the repository side.** Registering runners is the daemon's job; there
  is nothing to configure on GitHub beyond the credential.
