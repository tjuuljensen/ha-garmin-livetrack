# Coding Task List

## Core Behavior
- [DONE] UI setup through Home Assistant config entries
- [DONE] manual URL ingestion
- [DONE] IMAP event ingestion
- [DONE] independent concurrent session tracking
- [DONE] duplicate detection
- [DONE] restart recovery
- [DONE] per-user stable entities
- [DONE] integration-level global entities
- [DONE] session lifecycle events for automation/package consumers
- [DONE] configurable HTTP User-Agent
- [DONE] diagnostics redaction
- [DONE] repair signal for suspected Garmin response-shape changes
- [DONE] data-driven activity normalization and icon mapping
- [DONE] dedicated Garmin incremental trackpoint endpoint
- [DONE] one-time CSRF refresh retry on Garmin 403
- [DONE] Home Assistant `garmin_livetrack_point_received` event

## User Policy
- [DONE] Per-user activity override with global default fallback
- [DONE] Per-user policy persistence
- [DONE] Per-user policy editing in Options UI
- [DONE] Case-insensitive internal user matching
- [PENDING] Improve `allowed_users` UX with autocomplete or suggestions while preserving free-text pre-registration
- [PENDING] Add explicit per-user remove-user/admin controls in the Options UI instead of service-only management
- [PENDING] Reduce ambiguity in the options flow when a user setting inherits the global default

## Unknown User Handling
- [DONE] `strict_users=true` and `accept_first_seen_users=false` registers unknown users and rejects tracking
- [DONE] `strict_users=false` registers unknown users and starts tracking immediately
- [DONE] `strict_users=true` and `accept_first_seen_users=true` accepts one event and then requires explicit user enablement
- [PENDING] Prevent any residual entity creation for rejected/register-only unknown-user events before policy rejection
- [PENDING] Tighten user-facing documentation around the strict/accept-first matrix

## Lifecycle
- [DONE] No-progress detection
- [DONE] Historical/manual ended sessions finalize directly as `ended`
- [DONE] Fetch-ok inactive sessions can finalize through `ending` with `inactive_no_end`
- [DONE] End reason is retained on finalized sessions
- [DONE] Per-user status sensors retain ended-session state and summary values during retention
- [DONE] Retained ended-session summaries persist across Home Assistant restarts
- [DONE] Adaptive fast coordinator state survives restart recovery safely
- [PENDING] Cover true discarded/no-data no-END edge cases more deeply

## Diagnostics And Troubleshooting
- [DONE] Startup timing breadcrumbs retained in runtime state
- [DONE] Startup warning noise downgraded to debug
- [DONE] Debug attributes gated behind the normal `Expose debug attributes` option
- [DONE] `last_fetch` remains exposed by design
- [DONE] `sensor.garmin_livetrack_last_error` exposes shape-change status/count
- [DONE] Diagnostics expose effective User-Agent
- [DONE] Diagnostics expose shape-change issue expectation
- [DONE] Diagnostics expose adaptive trackpoint scheduling state
- [PENDING] Expand automated tests around shape-change signal transitions

## Entity Model
- [DONE] Per-user status, active, and tracker entities attach to user devices
- [DONE] Global entities attach to one integration device
- [DONE] Session fallback device exists for sessions without a known user yet
- [DONE] `garmin_livetrack.cleanup_legacy_entities` helper service
- [DONE] `sensor.garmin_livetrack_session_count` removed
- [DONE] Aggregate active-session summaries on `binary_sensor.garmin_livetrack_any_active`
- [PENDING] Add cleanup tests that verify active entities are not removed

## Protocol
- [DONE] Page-first fetch plus API fetch with hydration fallback
- [DONE] Garmin incremental `/track-points/common` endpoint with fallback to broader parsing
- [DONE] Adaptive fast mode using Garmin `postTrackPointFrequency`
- [DONE] Configurable HTTP User-Agent with validation
- [DONE] Common User-Agent documentation and examples
- [DONE] Add focused tests around the configurable User-Agent path
- [PENDING] Add per-session backoff tuning for repeated 429 and transient 5xx patterns

## Tests
- [DONE] Case-insensitive user matching tests
- [DONE] Strict/accept-first matrix tests
- [DONE] Storage migration test for removing legacy notification fields from stored user policies
- [DONE] Activity filter acceptance tests
- [DONE] Ended-session retention tests
- [DONE] Ended-session restore persistence tests
- [DONE] Aggregate active-session attribute tests
- [DONE] Cleanup regression test for deprecated `session_count`
- [DONE] LiveTrack URL parse tests for both supported URL forms
- [DONE] Incremental trackpoint endpoint URL construction test
- [DONE] 403 CSRF refresh retry test
- [DONE] Point-received event test
- [DONE] Adaptive fast no-early-trackpoint-fetch test
- [PENDING] Options-flow tests for user policy editing
- [PENDING] Additional no-END discarded-activity tests

## Operator Decisions Now Closed
- [DONE] Full LiveTrack URLs stay exposed on status entities
- [DONE] `Expose debug attributes` stays in the normal options UI as an advanced troubleshooting toggle

## Documentation
- [DONE] README rewritten as operator-facing documentation
- [DONE] Architecture plan updated to current behavior
- [DONE] Notification responsibility moved out of the integration and documented accordingly
- [DONE] HTTP User-Agent option documented
- [DONE] Local Windows and Linux test scripts documented
- [DONE] Cleanup guidance reframed as generic entity-registry cleanup documentation
