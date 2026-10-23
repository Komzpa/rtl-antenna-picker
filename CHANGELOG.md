# Changelog

## Unreleased

- Added `--pin-serial NAME=SERIAL` to assign roles by USB serial without an RF
  scan, for hosts where noise defeats the fingerprint but serials are unique.
- The systemd template reads host extras from `/etc/default/rtl-antenna-picker`
  (`RTL_ANTENNA_PICKER_ARGS`).

## 0.2.0 - 2026-07-09

- Generalized the picker from hardcoded AIS/ADS-B fields to configurable roles.
- Added `--role`, `--min-role-delta`, `--score-stat`, `--assignment-strategy`, and JSON config support.
- Kept the original AIS/ADS-B profile as the no-argument default.
- Added a built-artifact systemd installer entrypoint.
- Added MIT license metadata.
- Expanded tests and packaging verification.

## 0.1.0 - 2026-07-09

- Initial standalone release.
- Measures RTL-SDR antenna fingerprints with `rtl_power`.
- Selects AIS and ADS-B runtime indexes with a conservative AIS median delta guard.
- Emits a systemd-friendly environment file and JSON diagnostics.
- Includes PiAware + AIS RTLTCP systemd templates.
