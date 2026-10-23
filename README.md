# rtl-antenna-picker

`rtl-antenna-picker` assigns RTL-SDR runtime device indexes by measuring RF antenna fingerprints.

Use it when several RTL-SDR dongles are attached to different antennas but serial numbers, EEPROM labels, or runtime enumeration order are not reliable enough to choose the right receiver.

The tool is role-based:

- define one or more roles such as `ais`, `adsb`, `vhf`, `uhf`, `satcom-l`, or any name that fits your station;
- give each role an `rtl_power` band range;
- stop the radio consumers so every dongle is free;
- run the picker;
- consume the generated environment or JSON output from your service manager.

## Install

From the repository:

```sh
pipx install git+https://github.com/Komzpa/rtl-antenna-picker.git
# or, in a venv:
python3 -m pip install git+https://github.com/Komzpa/rtl-antenna-picker.git
```

For development:

```sh
git clone https://github.com/Komzpa/rtl-antenna-picker.git
cd rtl-antenna-picker
python3 -m pip install -e . pytest
pytest -q
```

## Requirements

- Linux host with RTL-SDR tools installed:
  - `rtl_test`
  - `rtl_power`
- Python 3.10+
- The radio consumers must be stopped while measuring, otherwise `rtl_power` cannot claim the dongles.
- Optional PiAware integration needs `piaware-config`.

## Generic quickstart

Example: choose two roles, `vhf` and `uhf`, from whatever two RTL-SDR runtime indexes currently exist:

```sh
sudo rtl-antenna-picker \
  --role vhf=156M:163M:5k \
  --role uhf=430M:440M:10k \
  --min-role-delta vhf=6 \
  --output-env /run/rtl-antenna-picker.env \
  --state-dir /var/lib/rtl-antenna-picker \
  --print-json
```

The default assignment strategy is `priority`: roles are assigned in the order you pass them, and each role gets the best remaining device for its band. This preserves the common “pick the most important antenna first” behavior.

If you want a global optimum across all roles instead, use:

```sh
--assignment-strategy maximize-total
```

The score statistic defaults to the band median. You can choose another summary:

```sh
--score-stat median   # default, robust floor/energy discriminator
--score-stat p95      # stronger emphasis on high bins
--score-stat peak     # strongest single-bin discriminator
```

## Default AIS + ADS-B profile

With no `--role` arguments the tool keeps the original two-role default:

- `ais=156M:163M:5k`
- `adsb=1088M:1092M:5k`
- `--min-role-delta ais=6`, exposed for backward compatibility as `--min-ais-median-delta-db 6`

So this still works:

```sh
sudo rtl-antenna-picker --print-json
```

## Output

By default the picker writes:

- shell environment file: `/run/rtl-antenna-picker.env`
- JSON diagnostics: `/var/lib/rtl-antenna-picker/latest.json`

Example environment file:

```sh
RTL_ANTENNA_PICKER_TIMESTAMP=2026-07-09T07:26:04+00:00
RTL_ANTENNA_PICKER_SCORE_STAT=median
RTL_ANTENNA_PICKER_ASSIGNMENT_STRATEGY=priority
RTL_ANTENNA_PICKER_ROLES='ais adsb'
AIS_RTL_INDEX=1
ADSB_RTL_INDEX=0
RTL_AIS_MEDIAN_DELTA_DB=10.660
RTL_ADSB_MEDIAN_DELTA_DB=-2.245
RTL_DEVICE_0_AIS_MEDIAN_DB=-31.120
RTL_DEVICE_1_AIS_MEDIAN_DB=-20.460
```

Role names are uppercased for environment variables, and non-alphanumeric characters become underscores. For example `satcom-l` becomes `SATCOM_L_RTL_INDEX`.

The JSON output is role-neutral under `selected.roles` and `metrics[*].roles`. For convenience, the default `ais` and `adsb` roles also emit `selected.ais_index` and `selected.adsb_index` aliases.

## JSON config

For repeatable setups, put roles in JSON:

```json
{
  "score_stat": "median",
  "assignment_strategy": "priority",
  "roles": [
    {"name": "vhf", "band": "156M:163M:5k", "min_delta_db": 6},
    {"name": "uhf", "band": "430M:440M:10k"}
  ]
}
```

Run:

```sh
rtl-antenna-picker --config station-roles.json --print-json
```

CLI `--role` values override same-named config roles and append new roles.

## PiAware + AIS integration

The repository includes optional systemd templates for a PiAware + AIS RTLTCP station:

- `systemd/piaware/rtl-antenna-picker.service`
- `systemd/piaware/dump1090-fa.service.d/10-rtl-antenna-picker.conf`
- `systemd/piaware/ais-rtl-tcp.service`
- `systemd/piaware/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf`

The default AIS role is scored as its band median minus the same device's
`88M:108M:20k` (FM) median, not as an absolute level: an AIS chain behind a
VHF bandpass or a 162 MHz antenna rises far above its own FM band, while a
wideband LNA stick is loud everywhere and would win any absolute comparison.
Disable with `--ais-reference-band ''`.

The PiAware profile also runs with `--learn`: a decisive mapping is remembered
by USB serial in `/var/lib/rtl-antenna-picker/roles-by-serial.json`, and an
ambiguous later measurement falls back to it (or to the best guess) instead of
failing and taking the receivers down.

After installing the package, the built-artifact installer can copy those templates and substitute the real `rtl-antenna-picker` executable path:

```sh
sudo "$(command -v rtl-antenna-picker-install-piaware-ais-systemd)"
```

Read [`docs/piaware-ais-systemd.md`](docs/piaware-ais-systemd.md) before the first live cutover. The installer only installs ordering/configuration files; it does not stop live receivers or run the RF measurement.

## Safety model

Without `--learn` this tool intentionally prefers failing closed over silently swapping antennas:

- it requires at least as many RTL-SDR devices as roles;
- role-specific `--min-role-delta NAME=DB` guards fail if the selected device is not sufficiently distinguishable;
- it records all measured medians, p95 values, peaks, and peak frequencies;
- it does not restart or kill services by itself.

## Development checks

```sh
python3 -m py_compile rtl_antenna_picker.py
python3 -m pytest -q
python3 -m build
```

## License

MIT. See [`LICENSE`](LICENSE).
