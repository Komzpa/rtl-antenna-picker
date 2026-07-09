# Changelog

## 0.1.0 - 2026-07-09

- Initial standalone release.
- Measures RTL-SDR antenna fingerprints with `rtl_power`.
- Selects AIS and ADS-B runtime indexes with a conservative AIS median delta guard.
- Emits a systemd-friendly environment file and JSON diagnostics.
- Includes PiAware + AIS RTLTCP systemd templates.
