# PiAware + AIS systemd setup

This example wires `rtl-antenna-picker` into a host that runs:

- `dump1090-fa` for ADS-B;
- `rtl_tcp` for the AIS/VHF dongle;
- an AIS consumer such as AIS-catcher or Shipfeeder connecting to RTLTCP.

## Install files

From a checkout of this repository:

```sh
sudo install -m 0755 rtl_antenna_picker.py /usr/local/sbin/rtl-antenna-picker
sudo install -d /var/lib/rtl-antenna-picker

sudo install -m 0644 systemd/piaware/rtl-antenna-picker.service \
  /etc/systemd/system/rtl-antenna-picker.service

sudo install -d /etc/systemd/system/dump1090-fa.service.d
sudo install -m 0644 systemd/piaware/dump1090-fa.service.d/10-rtl-antenna-picker.conf \
  /etc/systemd/system/dump1090-fa.service.d/10-rtl-antenna-picker.conf
```

If you do not already have an AIS RTLTCP service, install the complete example:

```sh
sudo install -m 0644 systemd/piaware/ais-rtl-tcp.service \
  /etc/systemd/system/ais-rtl-tcp.service
```

If you already have an `ais-rtl-tcp.service`, install only the drop-in:

```sh
sudo install -d /etc/systemd/system/ais-rtl-tcp.service.d
sudo install -m 0644 systemd/piaware/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf \
  /etc/systemd/system/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf
```

Then reload and enable:

```sh
sudo systemctl daemon-reload
sudo systemctl enable rtl-antenna-picker.service
sudo systemctl enable ais-rtl-tcp.service
```

## First live cutover

Do this in a maintenance window. Measurement requires both RTL-SDRs to be free.

```sh
sudo systemctl stop ais-rtl-tcp.service
sudo systemctl stop dump1090-fa.service
sudo systemctl stop rtl-antenna-picker.service || true
sudo rm -f /run/rtl-antenna-picker.env

sudo systemctl start rtl-antenna-picker.service
sudo systemctl start dump1090-fa.service
sudo systemctl start ais-rtl-tcp.service
```

If the AIS consumer was connected to the old `rtl_tcp`, restart it if it does not reconnect by itself.

## Verify

```sh
systemctl is-active rtl-antenna-picker.service dump1090-fa.service ais-rtl-tcp.service
sudo cat /run/rtl-antenna-picker.env
sudo cat /var/lib/rtl-antenna-picker/latest.json
piaware-config -show rtlsdr-device-index
piaware-status
ss -ltnp | grep ':10101'
```

Expected shape:

```sh
AIS_RTL_INDEX=1
ADSB_RTL_INDEX=0
RTL_AIS_MEDIAN_DELTA_DB=10.660
```

The exact indexes and dB values depend on your hardware and RF environment.

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
