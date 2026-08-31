---
title: "Authentication"
description: "Setting up a token or a GitHub App, with the exact permissions each needs."
---

The daemon needs credentials to register runners and to see which jobs are queued. There are
two ways to give it those, and this page walks through both.

**Neither credential ever enters a runner container.** Containers receive a single-use
just-in-time config blob and nothing else, in both modes. See
[ADR 1](../../reference/adr/0001-just-in-time-registration/).

---

## Which permissions, and why

Both modes need exactly the same two repository permissions. Nothing else — no organisation
permissions, no account permissions, no webhooks.

| Permission | Level | |
|---|---|---|
| **Administration** | Read and write | Registering and removing runners |
| **Actions** | Read | Seeing which jobs are queued |
| **Metadata** | Read | Mandatory; GitHub selects it for you |

### Why write, specifically

"Administration: Read and write" sounds broad, so here is precisely what needs it. Every REST
call the daemon makes, and the permission GitHub requires for it:

| Call | When | Permission | Level |
|---|---|---|---|
| `POST .../actions/runners/generate-jitconfig` | Starting a runner | Administration | **write** |
| `GET .../actions/runners` | Every tick, to observe the fleet | Administration | read |
| `DELETE .../actions/runners/{id}` | Retiring or reaping a runner | Administration | **write** |
| `GET .../actions/runs` | Every tick, to find queued work | Actions | read |
| `GET .../actions/runs/{id}/jobs` | Every tick, to read job labels | Actions | read |

The write level exists solely because creating and deleting a self-hosted runner *is* an
administration operation in GitHub's model. There is no narrower permission that permits it —
"Actions: write" governs re-running workflows, not runner registration.

Scope the credential to only the repositories in your `config.toml`. `Administration: write`
on two repositories you own is a very different thing from the same on everything you can
reach, which is why the scoping matters more than the level.

> **Metadata: Read** is mandatory for every fine-grained token and every GitHub App. GitHub
> enables it automatically when you select any other repository permission; you do not choose
> it and cannot remove it.

---

## Which mode should I use?

| | Personal access token | GitHub App |
|---|---|---|
| Setup time | ~2 minutes | ~10 minutes |
| Rate limit | 5000/hour, **shared** with everything else the token does | Its own budget per installation, scaling with repositories and users |
| Identity | You | The app |
| Credential lifetime | Until it expires or is revoked | Installation tokens last ~1 hour and rotate automatically |
| Secret on disk | The token itself | A private key, which is never transmitted — only signatures made with it are |
| Survives you losing repository access | No | Yes |

**Use a token** to try the project out, or for a couple of personal repositories.

**Use an App** for anything left running. The rate limit alone is usually the deciding
factor: the daemon polls forever, so with a token it is a permanent tenant of a budget shared
with your own `gh` usage and every other script you run.

Recorded as [ADR 6](../../reference/adr/0006-github-app-alongside-pat/).

---

## Setting up a fine-grained personal access token

### 1. Create the token

Go to **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token**.

| Field | Value |
|---|---|
| Token name | `ghspot` |
| Expiration | Your call. Whatever you pick, the daemon will start failing when it lapses — `ghspot doctor` says so plainly |
| Resource owner | Your account |
| Repository access | **Only select repositories** → choose exactly the repositories in your `config.toml` |

Under **Permissions → Repository permissions**, set:

| Permission | Set to |
|---|---|
| Administration | **Read and write** |
| Actions | **Read-only** |
| Metadata | Read-only *(GitHub sets this for you)* |

Leave every other permission at **No access**. Do not set any *Account* permissions.

Click **Generate token** and copy it — GitHub shows it once.

### 2. Store it

```bash
mkdir -p ~/.config/ghspot
install -m 600 /dev/null ~/.config/ghspot/token
printf '%s' 'github_pat_...' > ~/.config/ghspot/token
```

The `install -m 600` creates the file with the right mode *before* anything is written to it,
so the secret is never briefly world-readable. `ghspot` warns if it finds the file readable by
others.

### 3. Point the config at it

```toml
[github]
token_file = "~/.config/ghspot/token"
```

Or set `GHSPOT_GITHUB_TOKEN` instead and omit `token_file` entirely.

### A note on classic tokens

Classic tokens work — the endpoints accept the `repo` scope — but `repo` grants read and write
to **every repository you can reach**, including code, issues and settings.

A fine-grained token limited to two repositories is strictly better. Use classic only if
fine-grained tokens are unavailable to you.

---

## Setting up a GitHub App

### 1. Register the app

Go to **Settings → Developer settings → GitHub Apps → New GitHub App**.

| Field | Value |
|---|---|
| GitHub App name | Anything unique, e.g. `ghspot-yourname` |
| Homepage URL | Anything — your repository URL is fine |
| **Webhook → Active** | **Uncheck it** |

Unchecking *Active* matters. This project polls the API and needs no inbound endpoint — which
is what lets it run behind NAT — so leaving webhooks on would have GitHub deliver events to a
URL that does not exist. See [ADR 3](../../reference/adr/0003-polling-over-webhooks/).

Under **Repository permissions**:

| Permission | Set to |
|---|---|
| Administration | **Read and write** |
| Actions | **Read-only** |
| Metadata | Read-only *(mandatory, pre-selected)* |

Leave **Organization permissions** and **Account permissions** entirely alone. Subscribe to
**no events** — with webhooks off there is nothing to subscribe to.

Under **Where can this GitHub App be installed?**, choose **Only on this account**.

Click **Create GitHub App**.

### 2. Note the App ID

On the app's **General** page, near the top:

> **App ID** `123456`

That number is what goes in `app_id`. It is **not** the Client ID, and **not** the
installation ID — mixing these up produces `GitHub rejected the app assertion`, which is the
most common setup mistake here.

### 3. Generate a private key

Still on **General**, scroll to **Private keys** → **Generate a private key**. A `.pem`
downloads immediately; GitHub does not keep a copy.

```bash
install -m 600 ~/Downloads/ghspot-yourname.*.private-key.pem ~/.config/ghspot/app.pem
```

Keep the file exactly as downloaded — do not re-wrap it or strip its newlines. This key is
the long-lived secret; treat it as you would the token it replaces.

### 4. Install the app

**Install App** in the left sidebar → **Install** next to your account → **Only select
repositories** → choose the repositories in your `config.toml` → **Install**.

An app that exists but is not installed can authenticate as itself and do nothing else. If
you skip this step, `ghspot doctor` reports that the app has no installations.

### 5. Point the config at it

```toml
[github]
app_id = "123456"
private_key_file = "~/.config/ghspot/app.pem"
```

That is enough. The installation ID is discovered from the first repository in your config.

Set it explicitly only if the app is installed in more than one place — the daemon refuses to
guess, because guessing would start runners on the wrong account:

```toml
installation_id = 98765432
```

To find it: **Install App → the gear icon** beside the installation. The URL ends
`/installations/98765432`.

### 6. Adding repositories later

A repository added to `config.toml` must also be added to the **installation**, or the app
cannot see it. Go to **Install App → the gear icon → Repository access** and add it there.
This is the step people forget; `ghspot doctor` reports the repository as not found.

---

## Supplying credentials from the environment

The environment always wins over the config file, so a service manager can inject secrets with
no file on disk.

| Variable | Mode |
|---|---|
| `GHSPOT_GITHUB_TOKEN` | Personal access token |
| `GHSPOT_GITHUB_APP_ID` | GitHub App |
| `GHSPOT_GITHUB_APP_PRIVATE_KEY` | GitHub App |

`GHSPOT_GITHUB_APP_PRIVATE_KEY` accepts `\n` escapes, because systemd's `EnvironmentFile`
cannot hold real newlines:

```bash
{
  printf 'GHSPOT_GITHUB_APP_ID=123456\n'
  printf 'GHSPOT_GITHUB_APP_PRIVATE_KEY=%s\n' "$(awk '{printf "%s\\n", $0}' ~/.config/ghspot/app.pem)"
} | sudo tee /etc/ghspot/env > /dev/null
sudo chmod 600 /etc/ghspot/env
```

Credentials are never command-line arguments — that would put them in `ps` output.

---

## Verifying

```bash
ghspot doctor
```

It reports which mode is in use and proves the credential works against every configured
repository. For an App this performs a **real** JWT signature and token exchange, so a wrong
App ID or an unusable key is found here rather than an hour into a run.

```
✓ github auth: GitHub App 123456 (installation 42)
✓ repository tguisep/my-project: reachable — 0 runner(s) registered, 0 ours
```

Listing runners is the check used deliberately: it needs the same **Administration**
permission that registration does, so if it passes, registration will too.

---

## When permissions are wrong

| What you see | What it means |
|---|---|
| `the token was rejected (Bad credentials)` | The token is invalid, expired, or revoked. Generate a new one — this is not a permissions problem |
| `forbidden ... token likely lacks 'Administration: read & write'` | The credential is valid but under-permissioned. Set Administration to **Read and write** |
| `not found, or the token cannot see it` | The repository is not in the token's selected repositories, or not in the App's installation. Also check the `owner/name` spelling |
| `GitHub rejected the app assertion` | `app_id` does not match the private key, **or** the host clock is wrong. GitHub refuses a JWT dated in the future — check `timedatectl`. Confirm you used the numeric **App ID**, not the Client ID |
| `the app is not installed on OWNER/REPO` | The app exists but that repository is not in its installation. Add it under **Install App → gear → Repository access** |
| `this app has several installations` | Set `installation_id` explicitly |
| `rate limited` | The hourly budget is spent. Raise `poll_interval`, or move from a token to an App to get a budget of your own |

### Changing permissions on an existing credential

- **Token:** editing permissions takes effect immediately. Restart the daemon so it picks up
  a re-issued token from disk.
- **App — adding or widening a permission:** the installation must **accept** it first.
  GitHub emails the account owner a request, and until it is approved the app keeps its old
  permissions. Approve at **Install App → gear → Review request**.
- **App — removing or narrowing a permission:** takes effect immediately, no approval needed.

The asymmetry causes real confusion: you widen a permission to fix an error, nothing changes,
and the reason is an unapproved request sitting in your inbox. If `doctor` still reports
`forbidden` after you have clearly set Administration to read and write, check there.

---

## See also

- [`operations.md`](../../guides/operate/monitoring/) — installing, running and tuning the daemon
- [`SECURITY.md`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/SECURITY.md) — threat model and hardening checklist
- [ADR 1](../../reference/adr/0001-just-in-time-registration/) — why no credential enters a container
- [ADR 6](../../reference/adr/0006-github-app-alongside-pat/) — why both modes are supported
