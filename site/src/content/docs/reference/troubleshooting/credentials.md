---
title: "Credentials"
description: "Token permissions, App assertions, and rate limits."
---

## `/etc/ghspot/token is readable by other users`

Check *which* others first. `0640 root:ghspot` is the packaged layout and is **correct** — the
daemon runs as `ghspot`, and a credential only root can read stops it starting.

The warning fires for genuinely wider access: any permission for `other`, group-write, or
group-read by a group that is not the daemon's. Its remedy is the packaged layout, not
`chmod 600`.

## `could not read the token ...: Permission denied`, unit will not start

The daemon runs as `ghspot`, not the root you ran the wizard with.

```bash
sudo chown root:ghspot /etc/ghspot/token && sudo chmod 640 /etc/ghspot/token
sudo systemctl reset-failed ghspot && sudo systemctl start ghspot
```

`reset-failed` is needed after five rapid failures hit `StartLimitBurst`. Current versions do
this at setup time, and `ghspot doctor` checks it — because `sudo ghspot doctor` otherwise
passes every file test as root while the service cannot start at all.

## Other credential or permission errors

[Authentication](../../../start/authentication/#when-permissions-are-wrong) maps each message to its
cause. The two that catch people out:

- `GitHub rejected the app assertion` — wrong App ID, or a skewed host clock.
- A GitHub App permission change does not apply until the installation **accepts** it.

## Rate limited

`ForgeRateLimitedError` means the hourly budget is spent. Conditional requests make idle
polling nearly free, so this usually means many repositories with constant activity.

- Raise `poll_interval`, or reduce pools.
- Move from a personal access token to a GitHub App — its own budget instead of yours.
