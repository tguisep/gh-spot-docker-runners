# The dashboard

Everything the CLI does, in a browser: pools and their capacity, runners with their state and
resource use, a live log tail, the usage report, and the two interventions — stop a runner,
force a tick.

```bash
npm ci
npm run build     # → dist/, which the daemon serves at /ui
npm run dev       # or a dev server on :5173, proxying to a daemon on :8770
```

`GHSPOT_API=http://other-host:8770 npm run dev` points the proxy elsewhere.

The dev server needs a daemon on the other end: start one with `api_bind` set, or every
panel will say the daemon is not answering. It proxies the API paths, so requests are
same-origin in development exactly as they are in production.

`npm run dev -- --host` binds it to every interface, for looking at it from a phone on the
same network. That is a development server and has no authentication in front of it — it is
not the way to expose the dashboard to anything you care about. For that, serve the built
assets from the daemon and put a proxy with auth in front.

## Why it is at /ui and not /

The dashboard's own routes are named after the same things the API's are — `/runners`,
`/pools` — so mounting it at the root would have the two shadow each other, and which one
won would depend on registration order. `/ui` costs one path segment and removes the class
of bug entirely. `/` redirects there.

## Shape

| File | |
|---|---|
| `src/api.ts` | The only module that talks to the daemon. Same-origin; there is no base URL to configure |
| `src/types.ts` | The API's shapes, mirrored by hand. Eight endpoints does not justify a generator |
| `src/usePoll.ts` | Read an endpoint and keep reading it — the daemon pushes nothing |
| `src/format.ts` | `duration`, `bytes`, `percent`. Unit-tested, because "5m03s" rendered as "303s" is invisible in review |
| `src/pages/` | One per tab |

## Three behaviours worth not breaking

- **A failed poll keeps the last good data on screen.** A blip must not blank the fleet.
- **Polling stops while the tab is hidden.** A dashboard left open overnight is not a steady
  stream of requests against a home server.
- **The log view stays pinned to the bottom only until the reader scrolls up.** Yanking
  someone back to the end while they are reading is the one thing a log viewer must not do.

`npm test` covers the pure formatting and mounts the app against a stubbed `fetch` — a React
app that throws on mount serves a 200 with an empty body, which every check short of opening
it in a browser reports as healthy.

## Not here

No authentication: the API has none either. Bind to localhost or put a proxy in front, as
`SECURITY.md` says. No websockets: the daemon polls GitHub and pushes nothing, so the
dashboard polls too.
