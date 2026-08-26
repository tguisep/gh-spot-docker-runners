# Security

## Reporting a vulnerability

Open a [security advisory](https://github.com/tguisep/gh-spot-docker-runners/security/advisories/new)
rather than a public issue. Expect a first response within a week.

## The threat model

This runs **your** code, from **your** repositories, on **your** hardware. That assumption is
load-bearing. The section on untrusted code below explains where it stops holding.

## What the design protects

**No credential enters a runner container.** Runners are registered from the host using
GitHub's just-in-time configuration API. The container receives a single-use blob scoped to
one runner — never a personal access token. A compromised job cannot register runners, read
repository settings, or reach any other repository the token can see.

This is enforced in more than one place, deliberately:

- `config.toml` refuses any `[pool.container.environment]` key containing `token`, `secret`,
  `password` or `jitconfig`.
- The log processor redacts credential-shaped keys and truncates long values, so a stray
  debug line cannot print a working config blob.
- `JitRegistration.__repr__` omits the blob, so a traceback in the journal cannot leak it.
- CI asserts the built image carries no token-shaped environment variable.

**The token is never an argument.** It is read from a file or the environment, so it does not
appear in `ps` output or shell history. A world-readable token file produces a warning naming
the `chmod` to run.

**The runner tarball is verified.** It is the only artefact in the image not coming from a
signed apt repository, so it is SHA256-checked against the digest GitHub publishes, on both
amd64 and arm64.

**Runners are single-use.** A just-in-time runner takes one job and exits. Nothing carries
between jobs, and the container is destroyed rather than reused.

## What it does not protect — read this one

### Mounting the Docker socket gives jobs root on the host

With `docker_socket = true`, a job can reach `/var/run/docker.sock`. Anything that can reach
that socket can start a privileged container mounting the host filesystem. **That is
equivalent to root on the machine.** There is no partial version of this.

This is a deliberate trade (see [ADR 5](docs/adr/0005-docker-socket-over-dind.md)) and it is
sound for repositories whose code you control — a job runs code you would have run anyway.

**It is not sound for a repository that accepts workflow runs from forked pull requests.**
There, an attacker chooses the code that runs, and the socket hands them your machine. If you
need that, set `docker_socket = false`, and understand that container-based actions and
`docker build` will fail.

GitHub's own guidance is the same: [do not use self-hosted runners on public
repositories](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners#self-hosted-runner-security).

### The REST API has no authentication

Anyone who can reach `api_bind` can list your fleet and stop runners. Bind it to `127.0.0.1`,
or put a reverse proxy with authentication in front of it. It is not designed to face a
network.

### The daemon's user is effectively root

Membership of the `docker` group is equivalent to root on the host. Run the daemon as a
dedicated system user that exists for this and nothing else. The shipped systemd unit does
this, with `ProtectSystem=strict`, `NoNewPrivileges` and a minimal `ReadWritePaths`.

### Jobs share the host kernel

Containers are not a security boundary against a determined attacker. A kernel exploit
escapes regardless of what the socket is doing.

## Hardening checklist

- [ ] Fine-grained token, scoped to exactly the repositories in `config.toml`
- [ ] Token file `chmod 600`, owned by the daemon user
- [ ] Daemon runs as a dedicated system user, not your login account
- [ ] `api_bind` on `127.0.0.1`, or absent
- [ ] `docker_socket = false` for any repository that accepts fork pull requests
- [ ] `max_runners` and `memory` set, so a runaway matrix cannot exhaust the host
- [ ] `max_job_duration` set, so a hung job is killed rather than holding a slot forever
- [ ] The runner image rebuilt when `actions/runner` releases — GitHub requires runners to be
      no more than 30 days behind
