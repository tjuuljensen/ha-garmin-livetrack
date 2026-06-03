# Garmin LiveTrack Architecture And Implementation Plan

## Purpose
This document captures the current architecture, the migration context from the older YAML package, and the remaining phased work needed to take the `garmin_livetrack` integration from functional development state to a production-ready HACS integration.

It consolidates information from the previous handoff and resume documents together with the work completed since then.

## Historical Context
The original Home Assistant Garmin LiveTrack setup was implemented as a YAML package using helpers, template sensors, and shared REST-like state. It worked, but had structural weaknesses:
- shared active URL state
- shared data sensor
- token exposure risks through state/history
- fixed-slot assumptions that broke parallel sessions
- difficult restart recovery
- brittle template chains

The custom integration was created specifically to replace that architecture with a Python-managed runtime state machine.

## Current Architecture
### One integration entry, many user devices
The current design is intentionally single-instance at the config-entry level:
- one Garmin LiveTrack integration entry
- one global/integration device for aggregate sensors
- one stable device per Garmin display name/user policy
- optional fallback device for anonymous session startup before Garmin returns a user name

This model is preferred over multiple config entries because the integration is conceptually one ingestion/runtime manager with many users and many sessions.

### Core runtime components
#### `client.py`
Responsible for:
- URL parsing and canonicalization
- Garmin page-first fetch
- CSRF extraction
- API fetch
- hydration fallback parsing
- normalized session/trackpoint extraction
- redacted error handling

#### `coordinator.py`
Responsible for:
- `GarminLiveTrackManager`
- per-session poller/coordinator lifecycle
- known user policies
- storage and restart recovery
- IMAP listener registration
- service registration
- notifications
- aggregate runtime state

#### Entity platforms
- `sensor.py`: global sensors + per-user status sensors
- `binary_sensor.py`: global active sensor + per-user active sensors
- `device_tracker.py`: per-user GPS trackers

#### `config_flow.py`
Responsible for:
- first-time integration setup
- global options editing
- per-user policy editing through a second-step options flow

### Storage model
Persistent state is held in Home Assistant storage, not helper entities. Storage currently includes:
- active/recoverable session summaries
- token for recovery only
- known user policies

Tokens remain in storage only and are redacted elsewhere.

## Key Design Decisions Already Implemented
### 1. Independent polling per session
Each LiveTrack session owns its own runtime coordinator/task. This removed the old shared-state problem and allows multiple simultaneous sessions to run without overwriting one another.

### 2. Per-user stable entity model
Entities are intentionally stable per user, not per session. A new session for the same user updates the same user-facing status sensor, active binary sensor, and tracker.

### 3. Case-insensitive internal user matching
Garmin display names are matched case-insensitively internally while preserving the original display text for display and diagnostics.

### 4. Integration-level device for global sensors
Aggregate sensors now appear under a single Garmin LiveTrack integration device instead of floating independently.

### 5. Deferred restart recovery
Recovered sessions are reconstructed from storage first and their pollers are started later, using a configurable startup defer. This was added to reduce startup stalls and avoid immediate polling inside config entry setup.

### 6. Conservative token handling
The integration keeps the token out of normal entity state, logs, and diagnostics. The token is retained only in storage for restart recovery.

## Recovery And Startup Lessons Learned
The earlier rollout uncovered two important startup issues:
1. cross-thread `hass.async_create_task` scheduling in startup/recovery callbacks
2. restored pollers being started too early during storage restore

Both were fixed. Startup diagnostics were then added to make recovery timing visible. These diagnostics are still present and should be reviewed before production release.

## Current Functional Status
### Working now
- UI setup
- options flow for global settings
- options flow for one selected user policy
- manual URL ingestion
- IMAP event ingestion
- duplicate session detection
- restart recovery
- aggregate active sensor set
- per-user status/active/tracker devices
- start/end notifications
- service-driven user policy management
- `list_users` action response
- diagnostics redaction
- cleanup service for orphaned legacy entities

### Working, but still needs stronger validation
- strict user matrix behavior
- one-event-only semantics under mixed IMAP/manual timing
- stale/no-END handling for discarded activities
- Garmin shape-change detection and repair surfacing
- options-flow UX edge cases

## Policy Semantics
### Global versus user-level defaults
The intended model is:
- global setting = default
- user setting = override

This applies to:
- activity filter
- notification enablement
- notify target
- iOS-style notification payload

The current UI and reporting were adjusted to reflect this explicitly rather than implying a merge model.

### Unknown-user behavior
Current logic supports:
- `strict_users=false`: register and track immediately
- `strict_users=true`, `accept_first_seen_users=false`: register-only, reject tracking
- `strict_users=true`, `accept_first_seen_users=true`: accept one event, then disable until explicitly enabled

This is implemented, but still needs more direct tests and final docs wording.

## Garmin Fetch Strategy
### Why page-first exists
The integration deliberately fetches the Garmin LiveTrack page first, then the API, instead of calling the API only. That is required because Garmin has historically moved data between:
- API JSON
- Next.js hydration payloads
- app-router pushed payloads
- session-like nested branches

### Why candidate walking exists
A fixed JSON path is brittle. The integration instead walks likely structures and chooses the best candidate by session-like and trackpoint-like patterns. This is the pragmatic response to Garmin’s unstable payload shapes.

## Known Risks / Open Questions
### 1. No-END discarded activity cases
Garmin can stop exposing progress without emitting a clean `END`. This remains one of the highest-value hardening areas.

### 2. Display-name identity limitations
The integration currently relies on Garmin `userDisplayName`. That is practical and works well enough for the current use case, but it is not a guaranteed stable immutable ID.

### 3. Full URL exposure on status entities
This remains intentionally enabled today for validation/debugging and inline display use. It is a known privacy tradeoff that must be decided before production release.

### 4. Temporary debug attributes
Status entities currently expose some temporary debugging attributes that are useful during development but may be too noisy for a production-quality release.

## Recommended Next Phases
### Phase A - Tests and policy confidence
- add tests for user-policy inheritance/override behavior
- add tests for case-insensitive user matching
- add tests for strict/accept-first matrix
- add tests for options-flow per-user edits

### Phase B - Lifecycle hardening
- strengthen no-END stale handling
- define final inactive-but-fetch-ok finalize path
- document and test `finalization_minutes` behavior for inferred endings

### Phase C - Protocol and diagnostics polish
- make User-Agent configurable
- expose active User-Agent safely in diagnostics
- convert Garmin shape-change heuristics into repair issues and clearer operator guidance

### Phase D - Pre-production cleanup
- decide on full URL exposure policy
- decide on debug attribute policy
- add entity registry migration strategy if needed
- tighten docs and tests for HACS submission readiness

## Documentation Relationship
- `README.md` is the operator-facing document.
- `TODO.md` is the active backlog.
- This file is the architectural snapshot and phased implementation plan.

## Migration Notes From Prior Handoff
The earlier handoff correctly emphasized:
- avoiding mixed-mode testing with both YAML package and custom integration active
- reducing startup log storms from unrelated template problems before blaming Garmin recovery
- keeping token redaction strict
- treating startup/recovery as a first-order quality issue

Those lessons remain relevant and should continue guiding validation.
