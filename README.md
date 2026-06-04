# Garmin LiveTrack

Home Assistant custom integration for monitoring Garmin LiveTrack sessions without relying on template sensors, REST sensors, command_line sensors, or helper-based state machines.

The integration accepts Garmin LiveTrack URLs from Home Assistant services and IMAP events, tracks one or more active sessions independently, restores recoverable sessions after restart, exposes per-user devices and global health entities, and keeps LiveTrack tokens out of normal entity state, logs, and diagnostics.

## Status
Current integration version: `0.1.1`

This project is functional but still under active development. Core functionality is working:
- UI setup through Home Assistant config entries
- Manual `add_url` ingestion
- IMAP event ingestion
- multiple concurrent sessions
- per-user stable entities and device trackers
- restart recovery
- start/end notifications
- diagnostics with redaction

Remaining work is mostly around tests, lifecycle hardening for no-END edge cases, and pre-production cleanup of temporary debug attributes.
Recent changes include:
- per-user ended-session retention on the status sensor
- aggregate active-session attributes on `binary_sensor.garmin_livetrack_any_active`
- explicit inactive-without-END lifecycle handling
- customizable start/end notification message templates

## Important Warning
Garmin LiveTrack is not a documented public API. Garmin has changed the public site and response shape multiple times over the years. This integration is intentionally defensive, but future Garmin changes can still break session parsing or trackpoint extraction.

## Installation
### HACS custom repository
1. Add this repository as a custom HACS repository.
2. Category: `Integration`.
3. Install `Garmin LiveTrack`.
4. Restart Home Assistant.
5. Add the integration from `Settings -> Devices & Services`.

### Manual installation
1. Copy `custom_components/garmin_livetrack` into your Home Assistant `/config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration from `Settings -> Devices & Services`.

## External Setup
### Garmin side
- Configure your Garmin device/app to send LiveTrack emails.
- Confirm that a normal LiveTrack URL opens in a browser.

### IMAP side
If you want automatic email-driven ingestion, configure the Home Assistant IMAP integration to fire an `imap_content` event. A practical extraction template looks like this:

```jinja
{{ (text | regex_findall(find='https://livetrack\.garmin\.com/session/[^"'>\s]+', ignorecase=True) | first | default('')) | regex_replace(find='=\r?\n', replace='') }}
```

The integration listens only for `imap_content` and only extracts Garmin LiveTrack URLs. It does not persist email body content.

## Quick Start
1. Add the integration.
2. Open `Configure` and set your global defaults.
3. Call `garmin_livetrack.add_url` with a current Garmin LiveTrack URL.
4. Confirm that:
   - `binary_sensor.garmin_livetrack_any_active` turns on
   - the relevant Garmin user device appears
   - the user status sensor transitions into `active`
   - the user device tracker receives coordinates
5. If using IMAP, send or wait for a Garmin LiveTrack email and confirm the URL is consumed automatically.

## Configuration Model
The integration uses one config entry with:
- global settings
- known user registry
- per-user policy overrides
- runtime session storage for recovery

### Global options
- `Listen for IMAP events`
- `Enable notifications`
- `Notification target`
- `Start notification message`
- `End notification message`
- `Use iOS-style notification payload`
- `Require configured users`
- `Accept first event from unknown users`
- `Configured users`
- `Default activity filter`
- `Update interval (seconds)`
- `Initial trackpoint wait (minutes)`
- `Maximum runtime (hours)`
- `Stale timeout (minutes)`
- `Finalization window (minutes)`
- `Retain ended sessions (hours)`
- `Startup poll defer (seconds)`

### User policy options
Each known user can have overrides for:
- tracking enabled/disabled
- handling mode (`normal`, `register_only`, `one_event_only`)
- notification enable mode
- notification target override
- iOS-style payload mode
- activity filter mode (`inherit_global` or `custom`)
- allowed activities when using custom mode

### Notification message templates
Global notification messages are now configurable from the integration options UI.

Default templates:
- start: `LiveTrack started: {user} ({activity})`
- end: `LiveTrack ended: {user} ({activity}) - {reason}`

Supported placeholders:
- `user`
- `activity`
- `reason` for end notifications
- `source`
- `url`
- `redacted_url`
- `session_id_hash`
- `distance_km`
- `duration_min`

Example templates:
- Start: {user} started {activity}
- End: {user} finished {activity} after {duration_min} min ({distance_km} km) - {reason}

If a template is invalid, the integration falls back to the built-in default and logs a warning instead of breaking notifications.

### User matching
User policy matching is case-insensitive internally, but the original Garmin display name is preserved for display.

Garmin user identity currently relies on Garmin `userDisplayName`. That is practical, but not perfect: Garmin display names are user-facing strings, not guaranteed immutable IDs.

## Services
### `garmin_livetrack.add_url`
Add a Garmin LiveTrack URL manually.

Fields:
- `url` (required)

### `garmin_livetrack.stop_session`
Stop one active session.

Fields:
- `session_id` (optional)
- `session_id_hash` (optional)

### `garmin_livetrack.refresh_session`
Force one active session to refresh immediately.

Fields:
- `session_id` (optional)
- `session_id_hash` (optional)

### `garmin_livetrack.refresh_all`
Force all active sessions to refresh immediately.

### `garmin_livetrack.clear_ended`
Clear retained ended sessions.

### `garmin_livetrack.reload_users`
Reload stored user policies.

### `garmin_livetrack.test_notification`
Send a test notification using the current global notification settings.

Note:
- this service only validates the current notify target and delivery path
- it does not render a full live session template context

### `garmin_livetrack.set_user_policy`
Service-based user policy management.

Fields:
- `user`
- `enabled`
- `mode`
- `enable_notifications`
- `notify_service`
- `ios_notification_style`
- `allowed_activities`

### `garmin_livetrack.remove_user`
Remove a known user policy.

### `garmin_livetrack.list_users`
Return known users plus stored and effective policy information.

### `garmin_livetrack.cleanup_legacy_entities`
Remove orphaned legacy Garmin LiveTrack entities after migration from older per-session entity shapes.

## Entities
### Global entities
These are attached to one integration-level Garmin LiveTrack device:
- `binary_sensor.garmin_livetrack_any_active`
- `sensor.garmin_livetrack_active_count`
- `sensor.garmin_livetrack_last_error`

`sensor.garmin_livetrack_session_count` has been deprecated and is no longer provided.

### Per-user entities
Each known Garmin user gets a stable Home Assistant device with:
- status sensor
- active binary sensor
- device tracker

The per-user status sensor now retains the last ended session during the configured retention window so dashboards can continue to show the final activity state and summary values after the LiveTrack stops.

The entity model is intentionally per-user, not per-session, so a new LiveTrack session updates the same user entities instead of creating endless new entity families.

### Session fallback device
If a session is active before Garmin returns a user display name, the integration can temporarily use a session-based fallback device until the user identity is known.

## Architecture Overview
### Ingestion
The integration accepts LiveTrack URLs from:
- `garmin_livetrack.add_url`
- `imap_content` events
- storage-based restart recovery

### Runtime manager
`GarminLiveTrackManager` owns:
- active coordinators
- retained ended sessions
- known users and policy state
- IMAP event listener
- service registration
- storage load/save
- notification dispatch

### Session coordinators
Each active LiveTrack session runs independently through its own coordinator/task. That avoids the shared-state problems from the old YAML package:
- no shared active URL helper
- no shared data sensor
- no fixed slots
- no token leakage through template/history state

### Storage and recovery
Active or recoverable sessions are stored in Home Assistant storage with the token kept only there for restart recovery. On startup:
1. storage is loaded
2. recoverable sessions are reconstructed
3. restored pollers start after a configurable defer window
4. each restored session fetches independently

### Notification flow
Notifications are resolved in this order:
1. user override
2. global default

The same pattern applies to activity filtering and iOS-style notification payload handling.

Message body rendering uses the global notification templates from the options flow. Per-user routing still determines whether notifications are sent and which `notify` service receives them.

## Garmin Fetch / Parsing Resiliency
### Why the integration does page-first plus API fetch
The client does not assume Garmin’s API endpoint alone is sufficient. Instead it:
1. fetches the public LiveTrack page first
2. captures cookies and possible CSRF token
3. calls the API endpoint with the same session context
4. falls back to page hydration data when API trackpoints are missing

This is more resilient than fixed-path scraping because Garmin has changed where trackpoints appear more than once.

### Trackpoint extraction strategy
The client does not assume one exact JSON path. It walks likely branches and selects the best candidate based on session-like structures and trackpoint-like arrays. Sources include:
- API `trackPoints`
- API `trackpoints`
- API `points`
- Next.js `__NEXT_DATA__`
- app-router / hydration payloads
- nested arrays containing Garmin point-like dicts

This approach exists because a fixed-path implementation is brittle and was one of the reasons the old YAML-based stack was hard to keep working.

### Service-change detection
The integration already keeps heuristic shape-change counters when repeated fetches return anomalies such as:
- missing session
- missing trackpoints
- malformed response branches

The next phase is to promote this from a heuristic flag into repair issues and clearer diagnostics guidance.

## Session Lifecycle
### Normal transitions
Typical flow:
- `discovered`
- `fetching`
- `waiting_for_trackpoint`
- `active`
- `ending`
- `ended`

Other terminal/problem states include:
- `expired`
- `stale`
- `stopped`
- `garmin_error`
- `rejected_user`
- `rejected_activity`

### Finalization window
`finalization_minutes` keeps a just-ended session alive briefly when the end is inferred rather than explicit, so the integration can pick up final points. Historical/manual ended sessions are finalized directly rather than sitting in `ending` unnecessarily.

If Garmin keeps responding but a session stops making progress without emitting `END`, the integration now treats that as an inferred ending:
- it enters `ending`
- uses end reason `inactive_no_end`
- waits through `finalization_minutes`
- then finalizes as `ended`

### Stale detection
Current stale handling includes:
- no-progress detection based on trackpoint count/timestamp
- timeout when a session never produces points after the initial wait window
- stale finalization when fetches fail beyond the stale threshold

The main remaining lifecycle work is expanding test coverage and diagnostics around these inferred-ending paths rather than inventing more new states.

## Configuration Tuning
### `update_interval_seconds`
Default is 60 seconds. That is intentionally conservative and roughly aligned with reasonable browser-like polling behavior. Going lower increases load on Garmin and increases the chance of noisy transient states.

Recommended guidance:
- `60` for conservative/default use
- `30` if you want more responsiveness and accept more polling
- avoid lower values unless you have a concrete reason

### `initial_trackpoint_wait_minutes`
Use this when Garmin has created the session but has not exposed trackpoints yet.

### `stale_minutes`
Controls how long the integration tolerates no useful progress before marking a session stale.

### `finalization_minutes`
Controls how long inferred-ending sessions stay alive to capture late final data.

### `defer_startup_poll_seconds`
Delays restored pollers at startup to reduce Home Assistant startup pressure.

### `Expose debug attributes`
When disabled, the integration keeps troubleshooting-only attributes off the normal session status sensor surface.

When enabled, the status sensor also exposes:
- `page_status`
- `api_status`
- `trackpoints_source`
- `poll_task_alive`

## Privacy And Security
### Sensitive data
Garmin LiveTrack URLs contain a token. The integration treats that token as sensitive.

Rules:
- do not store raw token in entity state
- do not log raw token
- do not expose raw token in diagnostics
- do not include raw token in notifications
- persist token only in Home Assistant storage for restart recovery

### Coordinates
Coordinates are intentionally exposed through the device tracker because that is core functionality. They are not duplicated into diagnostics.

### URL exposure
Current status entities expose the full LiveTrack URL because that has been useful for inline display and validation during development. This is a deliberate tradeoff and remains a pre-production cleanup decision.

## Migration From The Old YAML Package
1. Disable the old Garmin LiveTrack YAML package.
2. Remove or recorder-exclude old helper entities if they still exist.
3. Restart Home Assistant.
4. Add the custom integration.
5. Configure IMAP ingestion if required.
6. Test with `garmin_livetrack.add_url`.
7. Run `garmin_livetrack.cleanup_legacy_entities` if legacy registry entries remain.

## Troubleshooting
### `Config flow could not be loaded`
A restart is usually required after updating the custom integration. The integration has already seen compatibility issues around Home Assistant options-flow API changes, so always deploy the full updated custom component before retesting.

### Session stuck in `waiting_for_trackpoint`
Use `refresh_session` or `refresh_all` to force a poll and, if needed, temporarily enable `Expose debug attributes` to inspect:
- `page_status`
- `api_status`
- `trackpoints_source`
- `poll_task_alive`

### Restart recovery is slow
Check `defer_startup_poll_seconds` and startup diagnostic logs. Recovery was intentionally deferred to avoid blocking Home Assistant startup.

### IMAP not creating sessions
Confirm:
- `Listen for IMAP events` is enabled
- IMAP integration is actually firing `imap_content`
- the event content contains a Garmin URL
- quoted-printable soft line breaks are removed

### Invalid URL
Only Garmin LiveTrack URLs hosted on `livetrack.garmin.com` are accepted.

### Missing session or missing trackpoints
This can mean:
- Garmin has changed response shape
- the session is no longer public/available
- Garmin returned a transient incomplete payload

Inspect diagnostics and the status sensor debug attributes before assuming the session is gone.

## Local Testing
Run tests locally in a Python 3.12 virtual environment that matches CI expectations:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1
```

Useful options:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -RecreateVenv
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -SkipInstall
```

## Additional Repo Documentation
For a deeper implementation snapshot and forward plan, see:
- [docs/architecture-implementation-plan.md](C:\Users\tjuuljensen\git\ha-garmin-livetrack\docs\architecture-implementation-plan.md)
- [TODO.md](C:\Users\tjuuljensen\git\ha-garmin-livetrack\TODO.md)
