# Troubleshooting

## `usb_claim_interface error -6`

A consumer already owns one of the dongles. Stop `dump1090-fa`, `rtl_tcp`, AIS-catcher, SDRSharp, or any other process using RTL-SDR before running the picker.

Useful commands:

```sh
rtl_test -t
fuser -v /dev/bus/usb/*/*
systemctl status dump1090-fa ais-rtl-tcp
```

## `antenna fingerprint is not decisive`

The picker found enough devices, but a role with a configured delta guard did not beat the next-best device by the required margin.

Options:

- check that the intended antenna and dongle are connected;
- run again when local RF noise is lower or signals are present;
- inspect `/var/lib/rtl-antenna-picker/latest.json` from previous successful runs;
- lower `--min-role-delta NAME=DB` only if you understand the risk.

Failing here is deliberate. A wrong automatic choice is worse than a noisy boot failure.

## A role got a surprising device

Check the assignment strategy:

- `priority` assigns roles in order and is best when the first role is operationally most important;
- `maximize-total` optimizes the total score and can assign a role to its second-best device if that improves the total.

Inspect the measured scores in `/var/lib/rtl-antenna-picker/latest.json` and reorder `--role` arguments if using `priority`.

## PiAware did not use the selected ADS-B index

Check:

```sh
piaware-config -show rtlsdr-device-index
systemctl status rtl-antenna-picker.service dump1090-fa.service
journalctl -u rtl-antenna-picker.service -u dump1090-fa.service -b
```

`dump1090-fa` must start after `rtl-antenna-picker.service`. Install the drop-in from `systemd/piaware/dump1090-fa.service.d/` and run `systemctl daemon-reload`.

If your ADS-B role is not named `adsb`, add `--piaware-role YOUR_ROLE` to the picker command.

## AIS consumer did not reconnect

Some AIS consumers do not reconnect cleanly after `rtl_tcp` is restarted. Restart the container or service that connects to RTLTCP after `ais-rtl-tcp.service` is running.

Confirm the TCP link:

```sh
ss -tnp | grep ':10101'
```

## Installer cannot find templates

If `rtl-antenna-picker-install-piaware-ais-systemd` cannot find systemd templates, check that you installed a wheel or source distribution that includes package data:

```sh
python3 - <<'PY'
import rtl_antenna_picker
print(rtl_antenna_picker.find_systemd_template_root())
PY
```
