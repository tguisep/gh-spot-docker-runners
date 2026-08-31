# The documentation site

[tguisep.github.io/gh-spot-docker-runners](https://tguisep.github.io/gh-spot-docker-runners/) —
[Astro](https://astro.build) with [Starlight](https://starlight.astro.build).

```bash
cd site
npm ci
npm run dev      # http://localhost:4321/gh-spot-docker-runners/
npm run build    # into site/dist
```

Pages live in `src/content/docs/`, grouped by **domain** rather than by document:

| Group | Directory | Covers |
|---|---|---|
| Start here | `start/` | Requirements through to a running service |
| Pools | `guides/pools/` | Labels and routing, `pm`, priority, GPUs |
| The host | `guides/host/` | Capacity, images, housekeeping, tuning |
| Operating it | `guides/operate/` | Monitoring, dashboard, API, this repo's own CI |
| Reference | `reference/` | Troubleshooting, backups, architecture, decisions |

A setting belongs to whichever of those *owns* it: `max_runners` bounds one pool, so it is a
pool page; `max_containers` bounds the machine, so it is a host page.

Adding a page means adding the file **and** a `slug` entry in `astro.config.mjs`. Starlight
does not autogenerate these groups deliberately — the order of a "start here" section is an
editorial decision, and alphabetical is the wrong answer.

## House style

- Paragraphs of a few lines. Anything longer is usually a list or a table that has not been
  written as one yet.
- Enumerations become bullets; comparisons and option sets become tables.
- Say the mechanism, not the feeling about it. Keep the *why* where it is load-bearing —
  a default that looks arbitrary until you know what it prevents needs its sentence.

## Linking between pages

Write links as relative paths to the other **`.md` file**, not to its URL:

```markdown
See [the GPU guide](../guides/gpus.md).
```

Astro resolves those at build time, which is what makes them survive the `/gh-spot-docker-runners`
base path. A hand-written `/guides/gpus/` works locally and 404s in production.

`scripts/check-site-links.py` checks every internal link in the built output against the pages
that were actually generated, and CI runs it after every build. The build itself does not fail
on a bad link — from its point of view nothing is wrong — so a rename would otherwise break
inbound links silently.
