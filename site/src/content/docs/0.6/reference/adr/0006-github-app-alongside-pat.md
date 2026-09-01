---
title: 6. Support GitHub Apps alongside personal access tokens
description: An architecture decision, with what was rejected.
slug: 0.6/reference/adr/0006-github-app-alongside-pat
---

**Status:** accepted · 2026-08-26

## Context

v0.1 authenticated with a fine-grained personal access token. That works, and it is the
fastest thing to set up, but for a daemon running continuously it has three real problems:

* **The rate limit is shared.** A PAT's 5000 requests/hour covers everything that token does,
  including your own `gh` usage. The daemon polls forever, so it is a permanent tenant of a
  budget it does not own.
* **It authenticates as a person.** Scoping it to two repositories limits the blast radius,
  but the credential is still tied to an individual account. If that account's access
  changes, runners stop.
* **It does not expire on its own.** A leaked PAT is valid until someone notices.

GitHub Apps fix all three: the installation has its own rate limit which scales with what it
covers, the identity is the app rather than a person, and installation tokens live about an
hour.

## Decision

Support both, behind a `TokenProvider` port. `StaticTokenProvider` returns a fixed string;
`GitHubAppTokenProvider` signs a JWT with the app's private key, exchanges it for an
installation access token, and refreshes five minutes before expiry.

The choice is made from configuration: an `app_id` means App mode, otherwise token mode.

## Consequences

**Gained:**

* The daemon can hold a credential that is not a person's, with its own rate limit.
* Installation tokens rotate automatically, so the long-lived secret on disk is the private
  key, which is never sent anywhere — only signatures made with it are.
* `installation_id` is discovered from the first configured repository, so the common case
  needs no extra setup step.

**Given up / paid for:**

* A dependency on `pyjwt[crypto]`, and therefore on `cryptography`.
* The `Authorization` header now has to be built per request rather than set once on the
  client, because an App token expires underneath a long-running daemon. This is a small
  change but it is the reason the client was refactored.
* More setup for the operator: creating an app, generating a key, installing it. Hence
  keeping PAT support — the first five minutes with the project should not require it.

**Unchanged:**

Neither credential ever enters a runner container. Containers receive a just-in-time config
blob and nothing else, in both modes. Nothing about
[ADR 1](../0001-just-in-time-registration/) changes.

## Alternatives rejected

**Replace PAT support entirely.** Cleaner code, worse first experience. Trying the project
out should not begin with creating a GitHub App.

**A separate binary or mode flag.** Rejected because it would duplicate the client. The
difference between the two is entirely "where does the bearer token come from", which is one
interface with two implementations.

**OAuth device flow.** Solves a different problem — interactive user authorisation — and
still ends with a user-scoped token.

## Notes

GitHub caps the assertion JWT at ten minutes; the implementation uses nine and backdates
`iat` by sixty seconds, following GitHub's own guidance, so a host clock running slightly
fast does not fail every request.

The key is validated by signing at construction rather than on first use, so a malformed PEM
surfaces from `ghspot doctor` with a reason instead of as an opaque 401 later.
