---
title: The runner lifecycle
description: The states a runner moves through, and the window a crash can land in.
slug: 0.6/reference/architecture/lifecycle
---

## The runner lifecycle

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> REGISTERED: JIT config minted
    REGISTERED --> STARTING: container created
    STARTING --> IDLE: connected to GitHub
    STARTING --> BUSY: took a job immediately
    IDLE --> BUSY: assigned a job
    IDLE --> DRAINING: asked to stop
    BUSY --> DRAINING: asked to stop
    BUSY --> RETIRED: job finished
    DRAINING --> RETIRED: drained
    REGISTERED --> RETIRED: reaped
    PENDING --> FAILED
    REGISTERED --> FAILED
    STARTING --> FAILED
    FAILED --> RETIRED: cleaned up
    RETIRED --> [*]
```

The aggregate refuses illegal moves. That matters because every skipped step leaves an
orphan on one side or the other — a runner that goes straight from `PENDING` to `STARTING`
has a container with no registration behind it.

### The crash-critical window

`REGISTERED` — config minted, container not yet created — is the only state where GitHub
knows about a runner that does not exist. It is deliberately its own state, and the record
is persisted before the container is attempted, so a crash there leaves evidence.

Two crash shapes are handled differently, because they leave different evidence:

| Shape | Record after the crash | How it is reaped |
|---|---|---|
| Container failed to start | `FAILED` — compensation ran | No active record claims the registration, so the stray sweep deletes it on the next tick |
| `SIGKILL` mid-provision | `REGISTERED` — nothing ran | Indistinguishable from a slow boot, so a 5-minute grace period resolves it |

The second is exactly the runner that gets stuck `Offline` elsewhere.
