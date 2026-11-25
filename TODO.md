# Development Backlog

This file tracks concrete behavior and usability issues. Add regression coverage with a fix for an identified issue. Setup and supported behavior are documented in the [README](README.md).

## Open Issues

- Align user-policy controls with their runtime effect: disabled users are blocked only in strict-user mode, and handling modes are stored without acting as independent runtime gates. See the [current policy model](docs/ARCHITECTURE.md#user-policy-model).
- Avoid unnecessary full fallback fetches after successful empty incremental trackpoint responses; handle these as no new point while retaining session finalization behavior.
- Show effective global values beside inherited settings in the options UI so users can see which settings apply.
