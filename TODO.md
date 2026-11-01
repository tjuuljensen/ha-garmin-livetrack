# Development Backlog

This file tracks outstanding work. Setup and supported behavior are documented in the [README](README.md).

## Validation and Test Coverage

- Replace the placeholder in `tests/test_services.py` with service registration, targeting, response, and user-management tests.
- Expand options-flow tests for saving user policies, custom activity filters, and Advanced baseline changes. Existing tests cover editor routing, user removal, and preserving advanced settings.
- Extend lifecycle coverage for discarded activities and fetch failures during finalization. Basic no-END completion, no-trackpoint timeouts, and past-end inference already have tests.
- Expand repair-signal coverage for repeated anomalies, recovery, and concurrent sessions. Basic signal synchronization and startup recovery already have tests.
- Validate the [notification example](docs/garmin_livetrack_notifications_example_unverified.yaml) on a live Home Assistant setup with Android and iOS clients.
- Validate more Garmin activity types and confirm the declared minimum Home Assistant version against the APIs used by the integration.

## Behavior to Review

- Review user-policy semantics: disabled users are currently blocked only in strict-user mode, and handling modes are not independent runtime gates.
- Verify that rejected or register-only sessions do not leave unintended entities or emit notifications before admission completes.
- Review empty incremental trackpoint responses: they currently trigger the full fallback instead of being treated as a no-new-point result.
- Clarify inherited settings in the options UI where effective global values are not shown.

## Release Preparation

- Publish tag `v0.2.1` and its matching release using [the release notes](docs/releases/v0.2.1.md), then verify HACS installation; follow the [release checklist](docs/ARCHITECTURE.md#release-model).
