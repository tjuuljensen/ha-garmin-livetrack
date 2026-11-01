# Garmin Fetch Strategy

## Purpose
This document describes the Garmin request and parsing pipeline used by `ha-garmin-livetrack`.

For setup and operation, see the [README](../README.md). This document covers:
- URL parsing
- page bootstrap and CSRF handling
- staged API fetches
- adaptive incremental trackpoint polling
- parser fallbacks
- error classification and backoff
- normalization boundaries

The implementation is intentionally layered. The integration prefers small, purpose-built Garmin endpoints when they work, but it keeps broader parsing paths so Garmin response-shape changes do not immediately break tracking.

## High-Level Flow
For one LiveTrack session, the request strategy is:

1. Parse and canonicalize the LiveTrack URL.
2. Fetch the public LiveTrack page for that session.
3. Read page HTML and extract a CSRF token when present; cookie handling belongs to the shared HTTP session.
4. Fetch Garmin session metadata from `/api/v2/sessions/{sessionId}?token=...`.
5. Fetch trackpoints from `/api/sessions/{sessionId}/track-points/common?token=...`.
6. If dedicated trackpoint fetching is not usable, fall back to broader payload and hydration parsing.
7. Apply lifecycle logic, adaptive scheduling, and backoff in the coordinator layer.

This split is deliberate:
- `client.py` is responsible for HTTP, parsing, and structured fetch results.
- `coordinator.py` is responsible for timing, lifecycle, state transitions, and retry pressure.

## URL Parsing And Identity Construction
Entry point: `GarminLiveTrackClient.parse_livetrack_identity()`.

Accepted URL forms:
- path token form:
  - `https://livetrack.garmin.com/session/{sessionId}/token/{token}`
- query token form:
  - `https://livetrack.garmin.com/session/{sessionId}?token={token}`

Normalization behavior:
- quoted-printable line breaks are removed first (`=\r\n`, `=\n`)
- hostname must be `livetrack.garmin.com`
- a canonical path-token URL is constructed for internal use
- a redacted URL is stored alongside it for diagnostics; events contain no URL

The resulting `LiveTrackIdentity` contains:
- `session_id`
- `token`
- `canonical_url`
- `redacted_url`
- `source`

## Step 1: Page Bootstrap
Entry point: `GarminLiveTrackClient._fetch_page_context()`.

Current implementation:
- fetches the session-specific public page (`identity.canonical_url`)
- sends only the configured `User-Agent`
- captures:
  - page HTML
  - HTTP status
  - a CSRF token when present

CSRF extraction:
- regex-based extraction from the HTML `<meta>` tag
- accepted meta names include:
  - `csrf-token`
  - `livetrack-csrf-token`

Returned page context includes:
- `html`
- `csrf`
- `csrf_found`
- `page_status`
- structured page bootstrap errors

Why this exists:
- bootstrap supplies page context and any available CSRF header for subsequent API calls
- the page response is also the last-resort parsing source if dedicated JSON branches become unusable

## Step 2: Session Metadata Fetch
Entry point: `GarminLiveTrackClient.fetch_session()`.

Primary session metadata URL:
- `GET /api/v2/sessions/{sessionId}?token={token}`

Headers:
- `User-Agent`
- `Accept: application/json, text/plain, */*`
- `Referer: {canonical_url}`
- `Livetrack-Csrf-Token: {csrf}` when available

Expected metadata fields include Garmin values such as:
- `sessionId`
- `userDisplayName`
- `start`
- `end`
- `activityType`
- `postTrackPointFrequency`

The client does not assume a single top-level JSON shape. Instead it walks nested values and selects objects that look session-like.

Session detection is based on `_looks_like_session()`:
- exact `sessionId` match when available
- otherwise a fallback heuristic using fields like `start` or `userDisplayName`

Result surface:
- `GarminFetchResult`
- `ok=True` only when a usable session object is found
- structured errors otherwise, including `missing_session`

## Step 3: Dedicated Incremental Trackpoint Fetch
Entry points:
- `GarminLiveTrackClient.fetch_trackpoints()`
- `GarminLiveTrackClient._trackpoints_api_url()`

Primary trackpoint URL:
- `GET /api/sessions/{sessionId}/track-points/common?token={token}`

Optional incremental lower bound:
- `begin={timestamp}`

Current implementation details:
- `begin` accepts either `datetime` or string input
- datetimes are normalized to UTC ISO 8601 with `Z`
- the request uses the exact last-point timestamp as the lower bound
- exclusivity is enforced in the coordinator by filtering returned points to timestamps strictly greater than the last stored point

Filtering by timestamp avoids counting a repeated last point as a new point.

Trackpoint extraction logic:
- `_find_trackpoints()` searches for candidate arrays across nested payloads
- recognized branch names include:
  - `trackPoints`
  - `trackpoints`
  - `points`
- when multiple arrays are present, the largest point-like candidate is selected

Trackpoint result surface:
- `GarminTrackpointResult`
- `ok=True` only when usable points are found
- otherwise returns structured errors such as `missing_trackpoints`

## CSRF Retry Model
Entry point: `GarminLiveTrackClient._fetch_with_csrf_retry()`.

Behavior:
1. Use an existing page context or bootstrap one.
2. Perform the API request.
3. If Garmin returns `403`, refresh the page context once.
4. Retry the same API request once with the new CSRF/cookie context.
5. If the retry still fails, return a structured error result.

Applied to:
- session metadata fetches
- dedicated trackpoint fetches
- full fallback fetches via the same internal helper path

This keeps CSRF rotation contained in the client layer instead of leaking retry logic into the coordinator.

## Full Fetch Path
Entry point: `GarminLiveTrackClient.fetch_full()`.

`fetch_full()` is the staged path used when Garmin publish-frequency gating is disabled.

Order:
1. page bootstrap
2. session metadata fetch
3. dedicated trackpoint fetch
4. if both session metadata and dedicated trackpoints are usable, return the staged result immediately
5. otherwise run the broader normalization/fallback logic

The staged success path returns:
- session data from the dedicated session endpoint
- trackpoints from `/track-points/common`
- source markers:
  - `session_source = api_or_payload`
  - `trackpoints_source = trackpoints_common`

If dedicated trackpoint fetch fails or is empty in a way that is not usable, the method falls back to broader parsing instead of failing immediately.

## Full Payload Fallback
Entry point: `GarminLiveTrackClient.fetch_legacy_full()`.

This path parses the session payload and page hydration data when the dedicated trackpoint fetch is unusable.

Order:
1. page bootstrap
2. session API fetch
3. `normalize_payload(session_payload, page_html, session_id)`

This path is still important because Garmin can shift useful trackpoint data into payload branches or hydration blobs even when the dedicated endpoint becomes unusable or incomplete.

## Payload And Hydration Fallbacks
Primary implementation points:
- `normalize_payload()`
- `_find_trackpoints()`
- `_extract_hydration_data()`
- `_load_next_data()`
- `_extract_json_arrays_by_key()`
- `_extract_next_push_points()`

Fallback sources searched today:
- session/API payload branches containing:
  - `trackPoints`
  - `trackpoints`
  - `points`
- Next.js `__NEXT_DATA__`
- app-router or `self.__next_f.push(...)` payload fragments
- nested JSON arrays inside hydration content

Selection strategy:
- find session-like object separately from point-like arrays
- choose the largest plausible point array when multiple candidates exist
- treat hydration as a last-resort recovery source, not the preferred one

Why the fallback remains necessary:
- Garmin does not provide a stable public contract for LiveTrack data
- the dedicated endpoint is preferable, but not sufficient as the only parsing path for long-term resilience

## Adaptive Coordinator Path
Coordinator entry points:
- `LiveTrackSessionCoordinator._fetch_runtime_state()`
- `LiveTrackSessionCoordinator._fetch_adaptive_state()`

When Garmin-frequency gating is disabled:
- the coordinator calls `client.fetch()`
- this resolves to `fetch_full()`

When Garmin-frequency gating is enabled:
1. fetch session metadata; return its result if no usable session is found
2. compare the fetch time against the previously computed `next_trackpoints_allowed_at`
3. if the next point is not due, retain the current point/count and mark the source as `deferred`
4. otherwise call `fetch_trackpoints(begin=last_point.timestamp)` and keep points newer than the previous point
5. if new points exist, increase the count and use the newest point
6. if the response has no errors and `begin` was supplied, retain the current point/count when all returned points are duplicates
7. otherwise call `fetch_legacy_full()` and filter its points by timestamp as well

An empty trackpoint response currently carries a `missing_trackpoints` error and takes the fallback path. It is not treated like an error-free response containing only duplicate points.

After the fetch, `_refresh_once()` updates Garmin publishing frequency and computes the next allowed fetch time. Unlike `fetch_full()`, the adaptive path bootstraps page context separately in `fetch_session()` and `fetch_trackpoints()`.

## Adaptive Scheduling State
Coordinator fields:
- `post_trackpoint_frequency_s`
- `last_trackpoint_fetch`
- `next_trackpoints_allowed_at`

Frequency source:
- Garmin session metadata field `postTrackPointFrequency`

Scheduling rule:
- `next_trackpoints_allowed_at = last_point.timestamp + postTrackPointFrequency + 2 seconds`

Fallback rule when Garmin does not provide a usable frequency:
- use the effective metadata interval from the selected profile/options

This state is persisted for restored active sessions so adaptive polling can resume safely after restart.

## No-Duplicate Point Rule
Current duplicate avoidance lives in the coordinator, not in the URL builder.

Implementation:
- the trackpoint request may include `begin = last_point.timestamp`
- after the response returns, `_new_trackpoints()` keeps only points with timestamps strictly greater than the previous point timestamp

Points with a `dateTime` equal to or older than the previous timestamp are not counted again. Once a previous timestamp exists, points without a usable `dateTime` are also excluded.

## Error Surfaces
Client errors are returned as structured `LiveTrackError` entries.

Examples include:
- `page_http_error`
- `page_request_error`
- `session_http_error`
- `session_request_error`
- `trackpoints_http_error`
- `trackpoints_request_error`
- `missing_session`
- `missing_trackpoints`
- `malformed_response`

The client avoids raising normal parsing/transport conditions as control-flow exceptions. It returns structured results and lets the coordinator decide how to handle lifecycle and retry pressure.

## Per-Session Transport Backoff
Coordinator entry points:
- `_apply_fetch_backoff()`
- `_classify_fetch_backoff()`
- `_handle_backoff_window()`

Backoff state is transient per session:
- `backoff_until`
- `consecutive_http_failures`
- `last_http_status`

Current policy:
- `429`
  - start at 120 seconds
  - exponential growth
  - cap at 900 seconds
- `5xx`
  - start at 30 seconds
  - exponential growth
  - cap at 600 seconds
- `403` after the CSRF refresh retry still fails
  - start at 60 seconds
  - cap at 300 seconds
- retryable request failures such as request errors or malformed responses
  - start at 30 seconds
  - cap at 600 seconds

Success clears all backoff state.

Important boundary:
- backoff delays Garmin fetches
- it does not suspend lifecycle control
- no-progress, stale, ending, and finalization logic still run while a session is cooling down

## Normalization Boundaries
Activity normalization lives in `models.py` and `icons.py`.

Rules:
- preserve Garmin raw activity as `activity_type_raw`
- normalize aliases to canonical `activity_type`
- preserve unknown activities after whitespace/case cleanup
- derive icon selection from normalized activity

Metric normalization also happens after point parsing:
- `speed_kmh`
- `pace_min_km`
- `distance_km`
- `duration_hms`
- `has_location`

The client extracts Garmin values. The coordinator can derive missing duration from session start and point timestamps, and missing distance or speed from successive GPS positions. Missing altitude, heart rate, power, and cadence can retain the previous point value. These are estimates or retained values, not fresh Garmin measurements.

## Privacy And Redaction Boundaries
The fetch pipeline works with live tokens internally, but redaction rules still apply:
- the token is not emitted in events
- the token is not exposed in diagnostics
- the token is not logged intentionally in normal paths
- active-session storage includes tokens for recovery; retained ended-session summaries include full URLs

The client constructs both:
- `canonical_url`
- `redacted_url`

Status-sensor `url` attributes expose the full URL. Session diagnostics use the redacted form; events contain neither URL. Diagnostics still include user display names and configuration options.

## Why This Layering Exists
The current design intentionally combines two approaches:

1. targeted Garmin endpoints for efficiency and lower bandwidth
2. broader fallback parsing for resilience to upstream UI/API changes

That gives the integration a better operating envelope than either approach alone:
- faster and smaller normal polling
- fewer redundant point fetches in adaptive mode
- continued survivability when Garmin shifts payload shape

## Current Limitations
The current pipeline still assumes:
- Garmin page bootstrap remains publicly reachable
- if Garmin requires CSRF protection, a usable token can be discovered in page HTML
- trackpoint-like data remains identifiable by current heuristics

If Garmin changes those assumptions again, the likely repair path is:
- inspect diagnostics and debug attributes
- compare browser behavior with the configured User-Agent
- adjust parsing heuristics or request bootstrap strategy
