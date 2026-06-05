# TODO

## Near Term
- Expand options-flow tests for user policy editing and Advanced/profile interactions.
- Add deeper discarded/no-data no-END lifecycle coverage.
- Add cleanup tests that verify active Garmin LiveTrack entities are not removed by `garmin_livetrack.cleanup_legacy_entities`.
- Expand automated coverage for shape-change signal transitions.

## Later
- Improve `allowed_users` UX with autocomplete or suggestions while preserving free-text pre-registration.
- Add explicit per-user remove-user/admin controls in the Options UI instead of service-only management.
- Reduce remaining ambiguity when user settings inherit global defaults.
- Prevent any residual entity creation for rejected/register-only unknown-user events before policy rejection is finalized.
- Tighten user-facing documentation around the `strict_users` / `accept_first_seen_users` matrix.

## Release Management
- Create tag `v0.2.0`.
- Publish the matching GitHub release.
- Verify HACS picks up the tagged release correctly.
