For any change about code structure, feature, deployment, update context.md and the documentation


## Keep the deployment paths in step

A change to configuration, packaging or the service is not finished when the code works. The
project ships three ways of installing itself, and each restates the same facts:

| Changed | Also update |
|---|---|
| A `config.toml` key — added, renamed, removed, default changed | `config.example.toml`, the Ansible role's `defaults/main.yml` and `templates/config.toml.j2`, `packaging/deb/config.toml` |
| The systemd unit, or anything it depends on | `deploy/ghspot.service`, `packaging/deb/postinst`, the Ansible role |
| The `.deb` layout, or a new file it installs | `packaging/deb/build.sh`, `packaging/deb/verify.sh` |
| A runner image variant, or the tools in one | `images/runner/build.sh`, `images/runner/verify.sh`, `images/runner/README.md` |
| Anything an operator does by hand | `docs/operations.md` |

The Ansible role is the one most easily forgotten, because **nothing fails when it falls
behind**. It keeps rendering a configuration the daemon quietly ignores, and the drift
surfaces much later as "why does this setting do nothing?".

So when a configuration key changes, render the role's template and feed the result to the
real parser rather than assuming it still fits:

```bash
ansible localhost -c local -m ansible.builtin.template \
  -a "src=deploy/ansible/roles/ghspot/templates/config.toml.j2 dest=/tmp/rendered.toml" \
  -e @vars.yml
uv run python -c "from ghspot.infrastructure.config.settings import load; print(load('/tmp/rendered.toml'))"
```

## Branches

One branch per unit of work, cut from `main`, named `<type>/<short-kebab-summary>` with the same types as commits:
`feat/`, `fix/`, `refactor/`, `docs/`, `ci/`, `chore/`, `test/`, `perf/`.

`feat/github-app-installation-flow`, `fix/pr-comment-dashboard-findings-link`, `ci/release-please-token`.

Never commit to `main` directly. One feature per branch — if a change is unrelated to the branch's name, it belongs on its own branch.

## Commits

Conventional commits: `<type>(<scope>): <imperative summary>`. Scope is the module or domain touched —
 `dashboard`, `docs`, `github`, `ci`...

Within a branch, one commit per domain. A feature that touches the API, the dashboard, CI and the docs is four commits,
not one squashed blob and not one commit per file:

```
feat(core): add the GitHub App installation callback
feat(dashboard): add the install button to the settings page
ci: build the app manifest in the release workflow
docs: document the installation flow
```

Subject line under 72 chars, imperative, no trailing period. Body only when the *why* isn't obvious from the diff.

## Pull request descriptions

Structured and scannable. A reviewer skims it in 30 seconds — no wall of prose.

Use this skeleton, dropping any section that has nothing to say:

```markdown
**What** — one sentence: the defect, or what this adds.

### Notes
- Left out: no backfill for comments already posted.
- Watch out: the link shape is duplicated in the platform webhook handler.

**Verification** — suites green.
```

Rules:

- One sentence for **What**. Not a paragraph.
- The **Changes** table is the body of the PR: one row per module, the file or endpoint named in code font, the change in a clause. No sentences, no "I did X so that Y".
- **Notes** are bullets, one line each. Only what a reviewer can't see in the diff: what was deliberately left out, what they'd otherwise miss.
- **Verification** is one line.
- Never narrate the journey — no "explored", no "considered X but chose Y". That belongs in the commit body.
- Don't restate what lives elsewhere: the diff shows the code, the commit body carries the reasoning, CONTEXT.md carries the history.

If the table has one row and there are no notes, skip the structure and ship the single **What** sentence.
