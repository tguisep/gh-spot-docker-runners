---
title: "Layers"
description: "Ports and adapters, and the dependency rule a test enforces."
---

## Layers

```
interfaces      CLI (Typer) · REST API (FastAPI)
     │              driving adapters — entry points
     ▼
application     use cases · reconciliation loop · DTOs
     │              orchestration only, no decisions
     ▼
domain          aggregates · value objects · scaling policy · ports
                    pure Python; imports nothing concrete
     ▲
     │
infrastructure  GitHub client · Docker backend · SQLite · config · logging
                    driven adapters — implement the ports
```

The rule everything rests on: **`domain` and `application` depend on nothing concrete.**

`interfaces` may reach `infrastructure`, because an entry point has to name a concrete adapter
or nothing would ever be constructed. `tests/unit/test_architecture.py` parses every module and
enforces this, so it cannot decay into a convention.

So the entire reconciliation loop, including crash recovery and every drift case, is tested
against in-memory fakes, with no Docker daemon and no network.

### The ports

| Port | Implemented by | Faked by |
|---|---|---|
| `ForgeClient` | `GitHubClient` (httpx) | `FakeForge` |
| `RunnerBackend` | `DockerRunnerBackend` | `FakeBackend` |
| `RunnerRepository` | `SqliteRunnerRepository` | `InMemoryRunnerRepository` |
| `Clock`, `IdGenerator` | `SystemClock`, `UuidGenerator` | `FakeClock`, `SequentialIds` |

`Clock` exists so a test can make a runner idle for an hour without waiting one.
