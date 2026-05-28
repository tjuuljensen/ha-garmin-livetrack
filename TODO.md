# Coding Task List

## User-level Settings
- [PENDING] Add per-user notification settings:
  - `enable_notifications`
  - `notify_service`
  - `ios_notification_style`
- [PENDING] Add per-user activity filter (use the global filter as a predefined default). Make each activity type an enable/disable option.
- [PENDING] Persist per-user policy/settings in integration storage and expose management in Options UI.
- [PENDING] Define fallback precedence:
  - user override -> global default
  - unknown user behavior with `accept_first_seen_users`

## User Signal Behavior
- [PENDING] Implement unknown-user registration flow with explicit strict-mode behavior.
- [PENDING] `strict_users=true` and `accept_first_seen_users=false`:
  - Register unknown user in configured users.
  - Do not track the incoming LiveTrack event.
  - Do not create sensors/entities for that event.
  - Do not send notifications for that event.
- [PENDING] `strict_users=false`:
  - Register unknown user in configured users.
  - Start tracking immediately with current global defaults.
- [PENDING] `strict_users=true` and `accept_first_seen_users=true`:
  - Register unknown user in configured users.
  - Track the first unknown-user event immediately (using global defaults).
  - Enforce "one event only" default behavior for subsequent unknown-user events until explicit user configuration enables ongoing tracking.
- [PENDING] Add per-user admin controls in options UI:
  - `enabled` toggle for tracking
  - remove user
  - explicit opt-in/opt-out for subsequent events after first-seen auto-accept flow
- [PENDING] Update unknown-user precedence rule:
  - Replace generic fallback text with this explicit strict/accept-first matrix.

## Validation/Tests For User-level Settings
- [PENDING] Add tests for per-user notification routing and fallback behavior.
- [PENDING] Add tests for per-user activity filter acceptance/rejection.
- [PENDING] Add diagnostics coverage to show per-user settings without leaking sensitive data.
- [PENDING] Add tests for strict/accept-first signal matrix:
  - strict=true, accept_first=false => register only, no tracking/notifications
  - strict=false => register + immediate tracking
  - strict=true, accept_first=true => first event tracked, later events require explicit enablement

## Session Lifecycle Hardening
- [PENDING] Add explicit no-END stale detection for "stopped/discarded activity" scenarios where Garmin never emits END.
- [PENDING] Add no-progress detection: if trackpoint timestamp/count does not advance for `stale_minutes`, transition to `stale` and finalize safely.
- [PENDING] Define fallback finalize path when session is still fetch-ok but effectively inactive (no END, no progress).
- [PENDING] Ensure `finalization_minutes` behavior is documented and covered when end is inferred vs explicit END.

## Tests For No-END Cases
- [PENDING] Add test: activity discarded immediately, no END event, session should stale/finalize without lingering forever.
- [PENDING] Add test: fetch remains ok but trackpoint data does not move; transition to stale after threshold.
- [PENDING] Add test: inferred-ending enters `ending` and exits to `ended` after `finalization_minutes`.

## Client/Protocol
- [PENDING] Make Garmin HTTP User-Agent configurable via options (with safe default and validation).
- [PENDING] Add diagnostics exposure for active User-Agent value (redacted/safe).
- [PENDING] Promote Garmin service-shape detection from heuristic flag to repair issue and explicit troubleshooting guidance.

## Entity Migration / Cleanup
- [PENDING] Add garmin_livetrack.cleanup_legacy_entities helper service to identify/remove orphaned legacy per-session entities after per-user migration.
- [PENDING] Add one-time entity registry migration strategy to map/disable superseded unique_ids safely.
- [PENDING] Add docs section with cleanup steps and rollback guidance for entity migration changes.
- [PENDING] Add tests for migration/cleanup behavior (no deletion of active entities, only orphaned legacy ones).


## Pre-Production Cleanup
- [PENDING] Decide final policy for temporary debug attributes on status sensor (`poll_task_alive`, `page_status`, `api_status`, `trackpoints_source`, `last_fetch`).
- [PENDING] Either remove these debug attributes or gate them behind an explicit debug option before production release.

## README / Docs (HACS Quality)
- [PENDING] Rewrite `README.md` to align with HACS best practices:
  - concise value proposition
  - clear install paths (HACS + manual)
  - compatibility/minimum Home Assistant version
  - quick start and first validation flow
- [PENDING] Add architecture overview:
  - ingest paths (IMAP + manual service)
  - manager/coordinator lifecycle
  - per-user entity model
  - storage/recovery flow
  - notification flow
- [PENDING] Add deep-dive section for Garmin fetch/parsing resiliency:
  - page-first + API request sequence
  - CSRF/cookie handling
  - recursive "best candidate" trackpoint selection strategy
  - hydration/app-router fallback branches
  - why fixed-path parsing was rejected
- [PENDING] Add API change resiliency/troubleshooting section:
  - shape-change detection signals
  - what diagnostics fields to inspect
  - expected recovery behavior during transient API shape breaks
  - next-step guidance when Garmin changes payloads
- [PENDING] Add configuration tuning guide:
  - `update_interval_seconds`, `stale_minutes`, `initial_trackpoint_wait_minutes`, `finalization_minutes`, `max_runtime_hours`
  - tradeoffs between responsiveness, load, and false stale transitions
  - recommended profiles (conservative vs responsive)
- [PENDING] Add operational/maintenance sections:
  - restart behavior and expected pickup timing
  - `refresh_session` / `refresh_all` usage
  - `cleanup_legacy_entities` usage after model migrations
- [PENDING] Add privacy/security notes:
  - token sensitivity and storage boundaries
  - URL exposure scope (status attributes)
  - diagnostics redaction boundaries
- [PENDING] Add limitations and roadmap:
  - current known limitations
  - planned per-user policy UI management
  - debug attribute cleanup decision before production

