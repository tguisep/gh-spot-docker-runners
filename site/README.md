# The documentation site

[tguisep.github.io/gh-spot-docker-runners](https://tguisep.github.io/gh-spot-docker-runners/) —
[Astro](https://astro.build) with [Starlight](https://starlight.astro.build).

```bash
cd site
npm ci
npm run dev      # http://localhost:4321/gh-spot-docker-runners/
npm run build    # into site/dist
```

Pages live in `src/content/docs/`, one Markdown file each, grouped the way the sidebar in
`astro.config.mjs` groups them. Adding a page means adding the file *and* a `slug` entry in
that sidebar — Starlight does not autogenerate these groups, deliberately: the order of a
"start here" section is an editorial decision and alphabetical is the wrong answer.

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
