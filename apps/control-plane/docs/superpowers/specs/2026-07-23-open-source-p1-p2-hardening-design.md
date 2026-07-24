# Open-Source P1/P2 Hardening Design

## Goal

Make the control plane safe to publish and practical to operate by resolving
the P1 and P2 findings from the open-source readiness review.

## Scope

This work covers the tracked P1 and P2 findings only:

- local Docker Compose network exposure and development authentication bypass
- an explicit MIT license grant
- console-login brute-force resistance
- Prometheus scrape cost as retained metric data grows
- unusable release-smoke workflow and excessive CI permissions
- mobile navigation dialog keyboard containment
- topology-node keyboard focus visibility

Documentation-copy and version-label P3 findings are intentionally out of
scope for this change.

## Deployment Safety

`docker-compose.yml` remains the convenient local development stack. Its Plane
and PostgreSQL host ports bind to `127.0.0.1` by default, including when the
README's `cp .env.example .env` flow enables `dev` mode. This preserves the
existing development authentication bypass without making it reachable from
the LAN or Internet.

`docker-compose.deploy.yml` remains the public deployment entry point. Its
ingestion token requirement and production authentication/bootstrap behavior
are unchanged. The local and deployment Compose files must make their intended
audience clear in comments and README instructions.

The repository gets a top-level `LICENSE` containing the MIT license text.

## Login Protection

Console login failures are persisted in PostgreSQL so throttling works across
multiple API replicas. A failure record is keyed by normalized username and
tracks a rolling time window plus a short lockout. A successful login clears
the record. The login endpoint returns a generic authentication failure while
locked so it does not reveal account state.

The API accepts two settings for the allowed failure count and lockout/window
durations. Conservative defaults protect a public console while allowing
operators to tune them for an upstream identity provider or trusted network.
The existing local-user authentication service owns state changes; the router
only decides whether to reject a request before or after authentication.

## Prometheus Collection

The exporter must no longer load every retained metric window into Python on
each scrape. Database queries aggregate counter values by service, environment,
task, instance, custom metric and labels. A second grouped/latest-value query
provides gauges. The response is cached in-process for a small configurable
TTL, with explicit invalidation after telemetry writes where practical.

The exporter preserves current metric names, labels, escaping and output
ordering. Cached output may lag a just-ingested window by at most the TTL.

## CI And Release Verification

The nonfunctional GitHub-hosted Release Smoke workflow is removed: it cannot
access an untracked deployment environment and is not an appropriate runner
for a user's live stack. The checked-in CI smoke job remains the reproducible
container release check.

CI uses read-only permissions by default. `packages: write` is granted only to
the image-publishing job. All third-party GitHub Actions are pinned to full
commit SHAs, retaining version comments for maintainability.

## Accessibility

The mobile "More" sheet remains a modal dialog. On open it places focus on its
first enabled control; Tab and Shift+Tab cycle within the sheet; Escape and
backdrop click close it and restore focus to the trigger. The generic
dismissible-menu hook exposes this behavior as an opt-in setting so listboxes
and ordinary menus retain their current keyboard behavior.

Topology source, task and sink buttons receive a visible `focus-visible`
outline independent of their selected and hover styles.

## Validation

- Add focused backend tests for login throttle state and reset-on-success.
- Add exporter tests proving aggregation does not materialize ORM metric-window
  rows and that a cache hit avoids another query.
- Add frontend tests for focus trapping and topology focus classes.
- Run Ruff, the complete Python suite, frontend unit tests, frontend build,
  Compose config validation and the configured browser E2E suite when Chromium
  is available.
