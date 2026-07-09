# PiAware + AIS systemd setup

This optional integration wires `rtl-antenna-picker` into a host that runs:

- `dump1090-fa` for ADS-B;
- `rtl_tcp` for an AIS/VHF dongle;
- an AIS consumer such as AIS-catcher or Shipfeeder connecting to RTLTCP.

The same CLI can be used without PiAware. This page is only for the PiAware/AIS service-ordering pattern.

## Install package and templates

Install the package system-wide or with `pipx` so the `rtl-antenna-picker` executable path is durable:

```sh
pipx install git+https://github.com/Komzpa/rtl-antenna-picker.git
```

Then install the systemd templates from the built package:

```sh
sudo "$(command -v rtl-antenna-picker-install-piaware-ais-systemd)"
```

The installer:

- installs `rtl-antenna-picker.service`;
- installs a `dump1090-fa` ordering drop-in;
- installs a complete `ais-rtl-tcp.service` if one does not already exist;
- otherwise installs an `ais-rtl-tcp.service` drop-in;
- substitutes the actual `rtl-antenna-picker` executable path into the unit file;
- runs `systemctl daemon-reload` and enables `rtl-antenna-picker.service`.

## Manual install from a checkout

From a source checkout you can use the shell helper:

```sh
sudo scripts/install-piaware-ais-systemd.sh
```

Or install by hand. When copying `systemd/piaware/rtl-antenna-picker.service`, replace `@RTL_ANTENNA_PICKER@` with the absolute path from:

```sh
command -v rtl-antenna-picker
```

## First live cutover

Do this in a maintenance window. Measurement requires all relevant RTL-SDRs to be free.

```sh
sudo systemctl stop ais-rtl-tcp.service
sudo systemctl stop dump1090-fa.service
sudo systemctl stop rtl-antenna-picker.service || true
sudo rm -f /run/rtl-antenna-picker.env

sudo systemctl start rtl-antenna-picker.service
sudo systemctl start dump1090-fa.service
sudo systemctl start ais-rtl-tcp.service
```

If the AIS consumer was connected to the old `rtl_tcp`, restart that consumer if it does not reconnect by itself.

## Verify

```sh
systemctl is-active rtl-antenna-picker.service dump1090-fa.service ais-rtl-tcp.service
sudo cat /run/rtl-antenna-picker.env
sudo cat /var/lib/rtl-antenna-picker/latest.json
piaware-config -show rtlsdr-device-index
piaware-status
ss -ltnp | grep ':10101'
```

Expected shape for the default AIS/ADS-B profile:

```sh
AIS_RTL_INDEX=1
ADSB_RTL_INDEX=0
RTL_AIS_MEDIAN_DELTA_DB=10.660
```

The exact indexes and dB values depend on your hardware and RF environment.

## Custom role profile in systemd

If your station needs different bands, edit `rtl-antenna-picker.service` after installation and replace the default command with explicit roles, for example:

```ini
ExecStart=/usr/local/bin/rtl-antenna-picker --role vhf=156M:163M:5k --role uhf=430M:440M:10k --min-role-delta vhf=6 --output-env /run/rtl-antenna-picker.env --state-dir /var/lib/rtl-antenna-picker --print-json
```

Or use a JSON config file and pass `--config /etc/rtl-antenna-picker/station.json`.

## Rollback

Remove the picker ordering and return to your old fixed device selection:

```sh
sudo systemctl disable --now rtl-antenna-picker.service || true
sudo rm -f /etc/systemd/system/rtl-antenna-picker.service
sudo rm -f /etc/systemd/system/dump1090-fa.service.d/10-rtl-antenna-picker.conf
sudo rm -f /etc/systemd/system/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf
sudo systemctl daemon-reload
```

Then restore your previous `dump1090-fa` / PiAware and `rtl_tcp` device configuration.
