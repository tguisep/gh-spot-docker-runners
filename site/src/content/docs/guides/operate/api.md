---
title: "The REST API"
description: "The same projection, over HTTP."
---

## The REST API

Set `api_bind` under `[daemon]` and the API is served in-process with the loop:

```bash
curl -s localhost:8770/health | jq
curl -s localhost:8770/pools | jq
curl -s -X POST localhost:8770/reconcile | jq   # tick now, don't wait
curl -s 'localhost:8770/runners?usage=true' | jq   # with CPU and memory
```

Interactive docs at `/docs`. **There is no authentication** — bind to localhost, or put a
reverse proxy with auth in front of it.
