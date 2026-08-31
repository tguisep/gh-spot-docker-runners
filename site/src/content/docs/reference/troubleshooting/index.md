---
title: "Troubleshooting"
description: "Symptoms, what causes them, and what to do."
---

| Symptom | Cause | Fix |
|---|---|---|
| Jobs stay queued, no container | Pool lacks a label the job asks for | Compare `ghspot pool list` with `runs-on` |
| Runner shows `Offline` on github.com | Normal between ticks | Leave it. Persisting past a few ticks → check `tick.error` |
| Daemon exits immediately | Configuration | `ghspot config validate` names the field |
| `ImageNotFoundError` | Image not built here | `ghspot image build <variant>` |
| Jobs cannot run `docker` | Socket off, or wrong `DOCKER_GID` in the image | `docker_socket = true`; rebuild the image on this host |
| `ghspot: command not found` | `uv sync` installs to `.venv/`, not `PATH` | `uv run ghspot`, or `uv tool install .` |

Anything with a message of its own, by area:

| Page | Covers |
|---|---|
| [Credentials](credentials/) | `token is readable by other users`, `could not read the token ... Permission denied`, `GitHub rejected the app assertion`, `ForgeRateLimitedError` |
| [Configuration and the service](service/) | A change that does nothing, `/ui` 404, `the runner image sources are not installed`, `pip: command not found` |
| [Logs](logs/) | An empty log pane, "no job found", a job whose runner vanished |

