# PiAware systemd templates

These units are examples for a two-dongle PiAware + AIS host.

- `rtl-antenna-picker.service` runs the RF picker as a one-shot before radio consumers start.
- `dump1090-fa.service.d/10-rtl-antenna-picker.conf` orders `dump1090-fa` after the picker.
- `ais-rtl-tcp.service` is a complete RTLTCP service that uses `AIS_RTL_INDEX` from `/run/rtl-antenna-picker.env`.
- `ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf` is a drop-in for an already-existing hardcoded RTLTCP service.

Install either the complete `ais-rtl-tcp.service` or the drop-in, not both unless you know exactly which base unit you are overriding.
