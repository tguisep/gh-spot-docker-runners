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

Write links as **relative URLs**, resolved from the linking page's own URL — not from where its
file sits, and not as a `.md` path:

```markdown
See [GPUs](../pools/gpus/).
```

Two traps, both of which have already bitten:

- **`.md` links are not rewritten.** `[GPUs](../pools/gpus.md)` is emitted verbatim and 404s in
  production. Starlight rewrites its own sidebar, not your prose.
- **A page is served one level deeper than its file.** `start/install.md` is served at
  `/start/install/`, so its sibling is `../configure/`, not `./configure/`. Only `index.md`
  pages have a URL matching their directory.

A root-absolute `/guides/...` is always wrong here: it works on the dev server and 404s under
the base path.

`scripts/check-site-links.py` catches all three, plus in-page `](#anchor)` links whose heading
has moved to another page. **CI runs it after every build, and it is the only thing that will
tell you** — Astro's build is perfectly happy with a link to nowhere.
