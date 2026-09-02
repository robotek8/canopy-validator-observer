# Changelog

Notable changes to Canopy Validator Observer are recorded here.

## Unreleased

### Added

- Added persistent block-height progress tracking. A responsive node is now marked `CRITICAL` when
  its height has not advanced for the configured interval.
- Added an always-on Docker Compose service with automatic restart and configurable polling.
- Added GitHub Actions tests on Python 3.11 and 3.12.
