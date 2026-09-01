---
title: Backups
description: What is worth keeping, and what regenerates itself.
slug: 0.6/reference/backups
---

There is nothing to back up. The state database is a projection: delete it and the next tick
rebuilds the fleet from the containers' own labels. Back up `config.toml` and your credential — the token file or the app's private key.
