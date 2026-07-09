# Troubleshooting

## `usb_claim_interface error -6`

A consumer already owns one of the dongles. Stop `dump1090-fa`, `rtl_tcp`, AIS-catcher, SDRSharp, or any other process using RTL-SDR before running the picker.

Useful commands:

```sh
rtl_test -t
fuser -v /dev/bus/usb/*/*
systemctl status dump1090-fa ais-rtl-tcp
```

## `AIS antenna fingerprint is not decisive`

The picker found two devices, but the strongest AIS-band median did not beat the runner-up by the configured threshold.

Options:

- check that the AIS antenna and dongle are connected;
- run again when local RF noise is lower or signals are present;
- inspect `/var/lib/rtl-antenna-picker/latest.json` if a previous successful run exists;
- lower `--min-ais-median-delta-db` only if you understand the risk.

Failing here is deliberate. A wrong automatic choice is worse than a noisy boot failure.

## PiAware did not use the selected ADS-B index

Check:

```sh
piaware-config -show rtlsdr-device-index
systemctl status rtl-antenna-picker.service dump1090-fa.service
journalctl -u rtl-antenna-picker.service -u dump1090-fa.service -b
```

`dump1090-fa` must start after `rtl-antenna-picker.service`. Install the drop-in from `systemd/piaware/dump1090-fa.service.d/` and run `systemctl daemon-reload`.

## AIS consumer did not reconnect

Some AIS consumers do not reconnect cleanly after `rtl_tcp` is restarted. Restart the container or service that connects to RTLTCP after `ais-rtl-tcp.service` is running.

Confirm the TCP link:

```sh
ss -tnp | grep ':10101'
```
