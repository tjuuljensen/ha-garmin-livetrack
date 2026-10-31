# TODO

## Near Term
- Expand options-flow tests for user policy editing and Advanced/profile interactions.
- Add deeper discarded/no-data no-END lifecycle coverage.
- Expand automated coverage for shape-change signal transitions.

## Later
- Reduce remaining ambiguity when user settings inherit global defaults.
- Prevent any residual entity creation for rejected/register-only unknown-user events before policy rejection is finalized.
- Tighten user-facing documentation around the `strict_users` / `accept_first_seen_users` matrix.

## Completed for 0.2.1
- Use Garmin LiveTrack API v2 for session metadata while retaining the existing incremental trackpoint endpoint.
- Align the integration manifest, package version, default User-Agent, and README to `0.2.1`.
- Add a cross-platform `.gitattributes` policy and normalize tracked text in Git to LF.
- Update the fetch strategy documentation and add v0.2.1 release notes.

## Release Management
- Run the repository test suite in the documented Python 3.12 environment before publishing `v0.2.1`.
- Create tag `v0.2.1`.
- Publish the matching GitHub release using `docs/releases/v0.2.1.md`.
- Verify HACS picks up the tagged release correctly.
