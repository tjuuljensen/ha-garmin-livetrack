# Garmin LiveTrack

Home Assistant custom integration for Garmin LiveTrack session monitoring.

The integration accepts Garmin LiveTrack URLs from Home Assistant services and IMAP events, tracks one or more sessions independently, restores recoverable sessions after restart, exposes stable user devices and global health entities, and keeps Garmin tokens out of normal logs, diagnostics, and non-storage state.

## Status
Current version: `0.1.2`

Implemented:
- UI setup through Home Assistant config entries
- manual URL ingestion
- IMAP event ingestion
- independent concurrent session tracking
- layered Garmin fetch strategy with a dedicated incremental trackpoint endpoint
- adaptive fast polling using Garmin `postTrackPointFrequency` when enabled
- per-user stable entities and device trackers
- restart recovery
- configurable HTTP User-Agent
- diagnostics with redaction
- repair signal for suspected Garmin response-shape changes

Remaining work is mainly:
- test coverage expansion
- a few remaining lifecycle edge cases
- entity-registry cleanup polish

## Important Warning
Garmin LiveTrack is not a documented public API. Garmin can change the public page structure, hydration payloads, or API response shape without notice. This integration is built defensively, but Garmin-side changes can still affect session parsing or trackpoint extraction.

## Installation
### HACS custom repository
1. Add this repository as a custom HACS repository.
2. Category: `Integration`.
3. Install `Garmin LiveTrack`.
4. Restart Home Assistant.
5. Add the integration from `Settings -> Devices & Services`.

### Manual installation
1. Copy `custom_components/garmin_livetrack` into `/config/custom_components/`.
2. Restart Home Assistant.
3. Add the integration from `Settings -> Devices & Services`.

## External Setup
### Garmin
- Configure the Garmin device/app to send LiveTrack emails if you want email-driven ingestion.
- Confirm that a normal LiveTrack URL opens in a browser.

### IMAP
If you want automatic ingestion from email, configure the Home Assistant IMAP integration to fire an `imap_content` event. A practical extraction template is:

```jinja
{{ (text | regex_findall(find='https://livetrack\.garmin\.com/session/[^"'>\s]+', ignorecase=True) | first | default('')) | regex_replace(find='=\r?\n', replace='') }}
```

The integration listens only for `imap_content` and only extracts Garmin LiveTrack URLs. It does not persist email body content.

## Quick Start
1. Add the integration.
2. Open `Configure` and set the global defaults.
3. Call `garmin_livetrack.add_url` with a current Garmin LiveTrack URL.
4. Confirm that:
   - `binary_sensor.garmin_livetrack_any_active` turns on
   - the Garmin user device appears
   - the user status sensor reaches `active`
   - the user device tracker receives coordinates
5. If using IMAP, verify that a Garmin email results in automatic URL ingestion.

## Configuration
The integration uses one config entry with:
- global settings
- known user registry
- per-user policy overrides
- runtime session storage for recovery

### Global options
- `Listen for IMAP events`
- `HTTP User-Agent`
- `Require configured users`
- `Accept first event from unknown users`
- `Configured users`
- `Default activity filter`
- `Update profile`
- `Update interval (seconds)`
- `Initial trackpoint wait (minutes)`
- `Maximum runtime (hours)`
- `Stale timeout (minutes)`
- `Finalization window (minutes)`
- `Retain ended sessions (hours)`
- `Startup poll defer (seconds)`
- `Expose debug attributes`

### Per-user policy options
Each known user can have overrides for:
- tracking enabled/disabled
- handling mode: `normal`, `register_only`, `one_event_only`
- activity filter mode: `inherit_global` or `custom`
- allowed activities when using custom mode

### Update profiles
- `Conservative`
  - uses the configured update interval
  - safest for Garmin and long activities
- `Balanced`
  - keeps the same overall fetch strategy
  - intended for more responsive dashboards when you lower the update interval
- `Adaptive fast`
  - still fetches session metadata normally
  - uses Garmin `postTrackPointFrequency` when available so trackpoint fetches are not attempted before Garmin is likely to have published a new point
  - falls back to the configured update interval when Garmin does not provide a usable frequency

### User matching
User policy matching is case-insensitive internally, while the original Garmin display name is preserved for display and diagnostics.

Garmin identity is based on Garmin `userDisplayName`. It is a user-facing string and not a guaranteed immutable account identifier.

## Notifications
This integration does not send notifications directly.

It exposes Home Assistant entities, device trackers, services, and events.
Use Home Assistant automations, scripts, blueprints, or YAML packages to send
mobile, persistent, voice, Android, or iOS notifications.

An example package is available at:
- [docs/garmin_livetrack_notifications_example_unverified.yaml](C:\Users\tjuuljensen\git\ha-garmin-livetrack\docs\garmin_livetrack_notifications_example_unverified.yaml)

## Custom HTTP User-Agent
The integration lets you override the HTTP User-Agent used for Garmin page and API requests.

Default:
- `HomeAssistant-GarminLiveTrack/0.1.2`

Typical reasons to change it:
- Garmin behaves differently for different clients
- you want to compare integration behavior against a browser session
- you want a stable custom identifier during troubleshooting

Recommended approach:
1. Start with the default.
2. Change it only when testing a concrete Garmin behavior difference.
3. Record the chosen value in diagnostics or issue notes when troubleshooting.
4. Clear the field and save if you want to revert to the built-in default.

Common examples:
- integration default:
  - `HomeAssistant-GarminLiveTrack/0.1.2`
- Windows Chrome:
  - `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36`
- macOS Safari:
  - `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15`
- iPhone Safari:
  - `Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1`
- Android Chrome:
  - `Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36`

Validation:
- empty value resets to the built-in default
- maximum 256 characters
- effective value is shown in diagnostics

## Services
### `garmin_livetrack.add_url`
Add a Garmin LiveTrack URL manually.

Fields:
- `url` required

### `garmin_livetrack.stop_session`
Stop one active session.

Fields:
- `session_id` optional
- `session_id_hash` optional

### `garmin_livetrack.refresh_session`
Force one active session to refresh immediately.

Fields:
- `session_id` optional
- `session_id_hash` optional

### `garmin_livetrack.refresh_all`
Force all active sessions to refresh immediately.

### `garmin_livetrack.clear_ended`
Clear retained ended sessions.

### `garmin_livetrack.reload_users`
Reload stored user policies.

### `garmin_livetrack.set_user_policy`
Update user tracking policy and activity overrides.

Fields:
- `user`
- `enabled`
- `mode`
- `allowed_activities`

### `garmin_livetrack.remove_user`
Remove a known user policy.

### `garmin_livetrack.list_users`
Return known users plus stored and effective policy information.

### `garmin_livetrack.cleanup_legacy_entities`
Remove orphaned Garmin LiveTrack entity-registry entries that are no longer provided by the integration.

## Events
The integration emits Home Assistant events for automation and package consumers:

- `garmin_livetrack_session_added`
- `garmin_livetrack_session_updated`
- `garmin_livetrack_session_ended`
- `garmin_livetrack_session_rejected`
- `garmin_livetrack_point_received`

Typical event payload fields include:
- `session_id_hash`
- `user`
- `activity_type`
- `activity_type_raw`
- `activity_icon`
- `source`
- `status`
- `reason` on ended and rejected events

`garmin_livetrack_point_received` also includes normalized point metrics such as:
- `latitude`
- `longitude`
- `speed_mps`
- `speed_kmh`
- `pace_min_km`
- `distance_km`
- `duration_s`
- `duration_hms`
- `heart_rate_bpm`
- `power_w`
- `cadence`
- `event_types`

## Entities
### Global entities
These are attached to one integration-level Garmin LiveTrack device:
- `binary_sensor.garmin_livetrack_any_active`
- `sensor.garmin_livetrack_active_count`
- `sensor.garmin_livetrack_last_error`

`sensor.garmin_livetrack_session_count` is no longer provided.

`binary_sensor.garmin_livetrack_any_active` also exposes aggregate attributes:
- `active_count`
- `active_users`
- `active_activities`
- `active_summaries`

### Per-user entities
Each known Garmin user gets a stable Home Assistant device with:
- status sensor
- active binary sensor
- device tracker

The per-user status sensor retains the most recent ended session during the configured retention window so dashboards can continue to show the final activity state and summary values after the LiveTrack stops. Retained ended-session summaries are stored and restored across Home Assistant restarts.

### Session fallback device
If a session becomes active before Garmin returns a user display name, the integration can use a temporary session-based fallback device until a user identity is known.

## Runtime Overview
### Ingestion
LiveTrack URLs can come from:
- `garmin_livetrack.add_url`
- `imap_content` events
- restart recovery from storage

### Runtime manager
`GarminLiveTrackManager` owns:
- active coordinators
- retained ended sessions
- user policy state
- IMAP event listener
- service registration
- storage load/save
- shape-change repair signal

### Session coordinators
Each active session runs independently through its own coordinator/task.

### Storage and recovery
Active or recoverable sessions are stored in Home Assistant storage with the token kept only there for restart recovery. Retained ended-session summaries are also stored so per-user status sensors can keep showing the last finished activity after a restart.

Startup flow:
1. load storage
2. rebuild recoverable active sessions
3. restore retained ended-session summaries
4. defer restored polling by the configured startup delay
5. start restored session pollers independently

## Garmin Fetch Strategy
### Request flow
The integration uses a layered Garmin fetch strategy:
1. fetch the public LiveTrack page first
2. capture cookies and possible CSRF token
3. call the Garmin session API for metadata
4. call Garmin's incremental trackpoint endpoint
5. fall back to session-payload and hydration parsing if Garmin changes the trackpoint response shape

### Trackpoint extraction
The dedicated incremental endpoint is preferred because it is smaller and more precise. The broader parser remains in place as a resilience layer.

The fallback parser walks likely structures and selects the best candidate based on session-like and trackpoint-like content.

Sources include:
- API `trackPoints`
- API `trackpoints`
- API `points`
- Next.js `__NEXT_DATA__`
- app-router or hydration payloads
- nested arrays containing Garmin point-like dicts

### Polling modes
- `Conservative`: safest for Garmin and long activities.
- `Balanced`: better dashboard responsiveness when paired with a lower update interval.
- `Adaptive fast`: fastest useful updates without polling trackpoints faster than Garmin publishes them.

### Shape-change signal
The integration watches for repeated anomalies such as:
- missing session
- missing trackpoints
- malformed response branches

When the signal crosses the suspicion threshold, the integration:
- raises a Home Assistant repair issue
- exposes `shape_change_suspected` and `shape_change_count` on `sensor.garmin_livetrack_last_error`
- includes the same signal in diagnostics

## Session Lifecycle
### Typical states
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

### Finalization
`finalization_minutes` keeps a just-ended session alive briefly when the end is inferred so the integration can capture late final data.

If Garmin continues responding but the session stops making progress without emitting `END`, the integration:
- enters `ending`
- records end reason `inactive_no_end`
- waits through `finalization_minutes`
- finalizes as `ended`

### Stale handling
Current stale handling includes:
- no-progress detection based on trackpoint count/timestamp
- timeout when a session never produces points after the initial wait window
- stale finalization when fetches fail beyond the stale threshold

## Tuning
### `update_interval_seconds`
Default is 60 seconds. That is intentionally conservative and roughly aligned with reasonable browser-like polling behavior.

Guidance:
- `60` for conservative/default use
- `30` for higher responsiveness if you accept more polling
- avoid lower values unless you have a concrete reason

### `initial_trackpoint_wait_minutes`
Controls how long the integration waits when Garmin has created the session but has not exposed trackpoints yet.

### `stale_minutes`
Controls how long the integration tolerates no useful progress before marking a session stale.

### `finalization_minutes`
Controls how long inferred-ending sessions remain active to capture late final data.

### `defer_startup_poll_seconds`
Delays restored pollers at startup to reduce startup pressure.

### `Expose debug attributes`
When disabled, troubleshooting-only attributes stay off the normal status-sensor surface.

When enabled, the status sensor also exposes:
- `page_status`
- `api_status`
- `trackpoints_source`
- `poll_task_alive`

## Privacy and Security
### Sensitive data
Garmin LiveTrack URLs contain a token. The integration treats that token as sensitive.

Rules:
- do not store raw token in normal entity state
- do not log raw token
- do not expose raw token in diagnostics
- do not include raw token in emitted events
- persist token only in Home Assistant storage for restart recovery

Retained ended-session summaries store the canonical URL so the status sensor can continue to expose the full URL after restart. This follows the same operator-visible URL policy used by live status entities.

### Coordinates
Coordinates are intentionally exposed through the device tracker because location tracking is a core function of the integration. They are not duplicated into diagnostics.

### URL exposure
Status entities intentionally expose the full LiveTrack URL. This is a product choice to support inline display, validation, and troubleshooting workflows.

This means:
- the full URL is available in normal entity attributes
- the token remains sensitive in logs and diagnostics, but not in the status-entity URL field
- dashboard exposure of the URL is an explicit trust decision by the operator

## Troubleshooting
### `Config flow could not be loaded`
Restart Home Assistant after updating the custom integration. Ensure the full updated custom component is deployed before retrying.

### Session stuck in `waiting_for_trackpoint`
Use `refresh_session` or `refresh_all` and, if needed, enable `Expose debug attributes` to inspect:
- `page_status`
- `api_status`
- `trackpoints_source`
- `poll_task_alive`

### Restart recovery is slow
Check:
- `defer_startup_poll_seconds`
- diagnostics
- debug logs for startup timing breadcrumbs

### IMAP is not creating sessions
Confirm:
- `Listen for IMAP events` is enabled
- the IMAP integration is firing `imap_content`
- the event body contains a Garmin URL
- quoted-printable soft line breaks are removed

### Stale entity-registry entries
If Home Assistant still shows Garmin LiveTrack entities that this integration no longer provides, run:

- `garmin_livetrack.cleanup_legacy_entities`

This is an optional cleanup utility. It is useful when stale entity-registry entries remain from earlier integration iterations or different unique-ID schemes.

### Invalid URL
Only Garmin LiveTrack URLs hosted on `livetrack.garmin.com` are accepted.

### Missing session or missing trackpoints
This can mean:
- Garmin changed response shape
- the session is no longer public or available
- Garmin returned a transient incomplete payload

Check:
- diagnostics
- status sensor debug attributes if enabled
- configured `HTTP User-Agent`
- browser behavior with the same LiveTrack

If the problem repeats, Home Assistant should also raise a Garmin LiveTrack repair issue indicating that the Garmin response shape may have changed.

## Local Testing
Run the full repo pytest suite in a Python 3.12 environment aligned with CI:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1
```

Linux/macOS:

```bash
bash ./scripts/test-local.sh
```

Useful options:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -RecreateVenv
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -SkipInstall
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -PythonCommand "python3.12"
```

```bash
bash ./scripts/test-local.sh --recreate-venv
bash ./scripts/test-local.sh --skip-install
bash ./scripts/test-local.sh --python python3.12
```

## Additional Documentation
- [TODO.md](C:\Users\tjuuljensen\git\ha-garmin-livetrack\TODO.md)
- [docs/ARCHITECTURE.md](C:\Users\tjuuljensen\git\ha-garmin-livetrack\docs\ARCHITECTURE.md)
