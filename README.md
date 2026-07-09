# rtl-antenna-picker

`rtl-antenna-picker` chooses RTL-SDR roles from RF antenna fingerprints when device serials, EEPROM labels, or enumeration order are not trustworthy.

The original use case is a PiAware-style host with two RTL-SDR dongles:

- one dongle is connected to an ADS-B / 1090 MHz antenna for `dump1090-fa`;
- another dongle is connected to an AIS / VHF antenna for `rtl_tcp` + AIS-catcher or Shipfeeder;
- both dongles may expose duplicate or otherwise unreliable selectors.

Instead of trusting serials, the picker briefly measures every free RTL-SDR with `rtl_power`, compares the RF energy seen by each antenna, and emits selected runtime indexes.

Default bands:

- AIS/VHF: `156M:163M:5k`
- ADS-B: `1088M:1092M:5k`

The picker selects the dongle with the strongest AIS-band median as `AIS_RTL_INDEX`; the best remaining ADS-B candidate becomes `ADSB_RTL_INDEX`. If the AIS median gap is not decisive, it fails instead of guessing.

## What it writes

By default:

- shell environment file: `/run/rtl-antenna-picker.env`
- JSON diagnostic state: `/var/lib/rtl-antenna-picker/latest.json`

Example environment file:

```sh
AIS_RTL_INDEX=1
ADSB_RTL_INDEX=0
RTL_AIS_MEDIAN_DELTA_DB=10.660
RTL_AIS_PEAK_DELTA_DB=26.470
```

With `--apply-piaware`, it also runs:

```sh
piaware-config rtlsdr-device-index "$ADSB_RTL_INDEX"
```

## Requirements

- Linux host with RTL-SDR tools installed:
  - `rtl_test`
  - `rtl_power`
- Python 3.10+
- For PiAware integration: `piaware-config`
- The radio consumers must be stopped while measuring, otherwise `rtl_power` cannot claim the dongles.

## Quick manual run

```sh
sudo systemctl stop ais-rtl-tcp.service
sudo systemctl stop dump1090-fa.service

sudo rtl-antenna-picker \
  --output-env /run/rtl-antenna-picker.env \
  --state-dir /var/lib/rtl-antenna-picker \
  --apply-piaware \
  --print-json

sudo systemctl start dump1090-fa.service
sudo systemctl start ais-rtl-tcp.service
```

If an AIS-catcher/Shipfeeder container is connected to `rtl_tcp`, restart that container if it does not reconnect after `rtl_tcp` is refreshed.

## Systemd PiAware/AIS example

Templates are in [`systemd/piaware/`](systemd/piaware/):

- `rtl-antenna-picker.service` — one-shot picker that writes `/run/rtl-antenna-picker.env` and updates PiAware.
- `dump1090-fa.service.d/10-rtl-antenna-picker.conf` — requires the picker before `dump1090-fa` starts.
- `ais-rtl-tcp.service` — complete example service using `AIS_RTL_INDEX`.
- `ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf` — drop-in for an existing `ais-rtl-tcp.service`.

See [`docs/piaware-ais-systemd.md`](docs/piaware-ais-systemd.md) for the full flow.

## Safety model

This tool intentionally prefers failing closed over silently swapping antennas:

- it requires at least two RTL-SDR devices;
- it requires the top AIS median to beat the runner-up by `--min-ais-median-delta-db` dB, default `6.0`;
- it records all measured medians, p95 values, peaks, and peak frequencies;
- it does not restart or kill services by itself.

The systemd examples handle ordering only. Operators still decide when to cut over a live host.

## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e . pytest
pytest -q
```

Or, without installing:

```sh
python3 -m py_compile rtl_antenna_picker.py
python3 -m pytest -q
```

## License

No license has been declared in this repository yet.
