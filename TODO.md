# Coding Task List

## User-level Settings
- [PENDING] Add per-user notification settings:
  - `enable_notifications`
  - `notify_service`
  - `ios_notification_style`
- [PENDING] Add per-user activity filter (instead of single global filter).
- [PENDING] Persist per-user policy/settings in integration storage and expose management in Options UI.
- [PENDING] Define fallback precedence:
  - user override -> global default
  - unknown user behavior with `accept_first_seen_users`

## Validation/Tests For User-level Settings
- [PENDING] Add tests for per-user notification routing and fallback behavior.
- [PENDING] Add tests for per-user activity filter acceptance/rejection.
- [PENDING] Add diagnostics coverage to show per-user settings without leaking sensitive data.
