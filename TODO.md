# Coding Task List

## User-level Settings
- [DONE] Add per-user notification settings:
  - `enable_notifications`
  - `notify_service`
  - `ios_notification_style`
- [DONE] Add per-user activity filter override using the global filter as the default when no user override exists.
- [DONE] Persist per-user policy/settings in integration storage and expose management in Options UI.
- [DONE] Define fallback precedence:
  - user override -> global default
  - unknown user behavior with `accept_first_seen_users`
- [PENDING] Improve global `allowed_users` UX with autocomplete/suggestions from known Garmin display names while preserving free-text pre-registration.
- [PENDING] Add explicit per-user remove-user/admin controls in the Options UI instead of service-only management.
- [PENDING] Consider conditional display/hiding of user policy fields in the options flow to reduce ambiguity when a setting is inherited.

## User Signal Behavior
- [IN_PROGRESS] Implement unknown-user registration flow with explicit strict-mode behavior.
- [DONE] `strict_users=true` and `accept_first_seen_users=false`:
  - Register unknown user in configured users.
  - Do not track the incoming LiveTrack event.
  - Do not send notifications for that event.
- [DONE] `strict_users=false`:
  - Register unknown user in configured users.
  - Start tracking immediately with current global defaults.
- [DONE] `strict_users=true` and `accept_first_seen_users=true`:
  - Register unknown user in configured users.
  - Track the first unknown-user event immediately using global defaults.
  - Enforce one-event-only behavior until explicit user configuration enables ongoing tracking.
- [PENDING] Prevent entity creation for rejected/register-only unknown-user events if any residual paths still instantiate runtime entities before policy rejection.
- [PENDING] Replace generic fallback text in docs with the explicit strict/accept-first matrix.

## Validation / Tests For User-level Settings
- [PENDING] Add tests for per-user notification routing and fallback behavior.
- [PENDING] Add tests for per-user activity filter acceptance/rejection.
- [DONE] Add diagnostics coverage to show per-user settings without leaking sensitive data.
- [PENDING] Add tests for strict/accept-first signal matrix:
  - strict=true, accept_first=false => register only, no tracking/notifications
  - strict=false => register + immediate tracking
  - strict=true, accept_first=true => first event tracked, later events require explicit enablement
- [PENDING] Add tests for case-insensitive user matching.
- [PENDING] Add tests for options-flow user policy editing.

## Session Lifecycle Hardening
- [IN_PROGRESS] Add explicit no-END stale detection for stopped/discarded activity scenarios where Garmin never emits END.
- [DONE] Add no-progress detection: if trackpoint timestamp/count does not advance for `stale_minutes`, transition to `stale` and finalize safely.
- [PENDING] Define fallback finalize path when session is still fetch-ok but effectively inactive (no END, no progress).
- [PENDING] Ensure `finalization_minutes` behavior is documented and covered when end is inferred vs explicit END.
- [DONE] Finalize historical/manual ended sessions directly instead of leaving them in `ending`.

## Tests For No-END Cases
- [PENDING] Add test: activity discarded immediately, no END event, session should stale/finalize without lingering forever.
- [PENDING] Add test: fetch remains ok but trackpoint data does not move; transition to stale after threshold.
- [PENDING] Add test: inferred-ending enters `ending` and exits to `ended` after `finalization_minutes`.
- [PENDING] Add test: historical ended URL added manually should go directly to `ended`.

## Client / Protocol
- [PENDING] Make Garmin HTTP User-Agent configurable via options with safe default and validation.
- [PENDING] Add diagnostics exposure for active User-Agent value in a safe/redacted form.
- [IN_PROGRESS] Promote Garmin service-shape detection from heuristic flag to repair issue and explicit troubleshooting guidance.
- [DONE] Use page-first fetch plus API fetch with hydration fallbacks rather than fixed-path parsing.

## Entity Model / Cleanup
- [DONE] Treat each configured Garmin display name/user policy as a Home Assistant device where practical.
- [DONE] Attach that user's stable sensors/binary sensors/device tracker to the user device.
- [DONE] Add an integration-level device for global sensors.
- [DONE] Keep individual LiveTrack sessions as runtime data, not long-lived devices, unless a session has no known user.
- [DONE] Document the limitation that Garmin display names are not guaranteed immutable or globally unique.
- [DONE] Add `garmin_livetrack.cleanup_legacy_entities` helper service to identify/remove orphaned legacy per-session entities after per-user migration.
- [PENDING] Add one-time entity registry migration strategy to map/disable superseded unique_ids safely.
- [PENDING] Add docs section with cleanup steps and rollback guidance for entity migration changes.
- [PENDING] Add tests for migration/cleanup behavior with no deletion of active entities.

## Startup / Recovery
- [DONE] Fix cross-thread `hass.async_create_task` scheduling in startup/recovery callbacks.
- [DONE] Defer restored poller startup so storage restore does not immediately start polling during config entry setup.
- [DONE] Add startup diagnostics for recovery timing and restored poller launch.
- [PENDING] Decide whether temporary startup debug logging should remain, be downgraded, or be gated behind a debug option.

## Pre-Production Cleanup
- [PENDING] Decide final policy for temporary debug attributes on status sensor:
  - `poll_task_alive`
  - `page_status`
  - `api_status`
  - `trackpoints_source`
  - `last_fetch`
- [PENDING] Either remove these debug attributes or gate them behind an explicit debug option before production release.
- [PENDING] Decide whether full LiveTrack URLs should remain exposed on status entities in production, given the privacy tradeoff versus user validation/debugging needs.

## README / Docs (HACS Quality)
- [DONE] Rewrite `README.md` to align with HACS best practices.
- [DONE] Add architecture overview:
  - ingest paths (IMAP + manual service)
  - manager/coordinator lifecycle
  - per-user entity model
  - storage/recovery flow
  - notification flow
- [DONE] Add deep-dive section for Garmin fetch/parsing resiliency.
- [DONE] Add API change resiliency/troubleshooting section.
- [DONE] Add configuration tuning guide.
- [DONE] Add operational/maintenance sections.
- [DONE] Add privacy/security notes.
- [DONE] Add limitations and roadmap.
- [DONE] Add a repo-local architecture/implementation plan capturing current status and next phases.
