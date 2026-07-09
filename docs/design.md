# Design

## Problem

RTL-SDR receivers are often selected by serial or by runtime index. Both can be wrong in small multi-dongle stations:

- cheap dongles may ship with duplicate EEPROM serials;
- some tools display a different serial than the one written in EEPROM;
- runtime indexes can change after reboot or after a dongle is moved;
- physically identical dongles may be connected to very different antennas.

For AIS + ADS-B stations, using the wrong dongle is not a minor labeling bug: it puts the VHF receiver on the 1090 MHz antenna or the ADS-B receiver on the VHF antenna.

## Approach

`rtl-antenna-picker` treats the antenna as the source of truth.

For each free RTL-SDR device it measures:

- AIS/VHF band, default `156M:163M:5k`;
- ADS-B band, default `1088M:1092M:5k`.

For each band it parses the `rtl_power` CSV bins and computes:

- median dB;
- p95 dB;
- peak dB;
- peak frequency;
- sample count.

The AIS device is the dongle with the highest AIS-band median. The picker requires that this median is at least `--min-ais-median-delta-db` dB stronger than the runner-up. The default threshold is `6.0 dB`.

The ADS-B device is selected from the remaining dongles by highest ADS-B median. In the common two-dongle case this is simply "the other one", but the metric is still recorded for auditability.

## Why AIS median is the primary discriminator

In the motivating deployment, both antennas could hear both bands a little, but the AIS/VHF antenna had a much stronger VHF-band floor and peaks. ADS-B-band median differences were smaller and less useful as the main discriminator.

That makes the AIS median delta a good guardrail: it is stable enough to pick the VHF antenna, and conservative enough to fail if the current RF environment does not make the distinction clear.

## Output contract

The environment file is intended for systemd `EnvironmentFile=` consumers:

```sh
AIS_RTL_INDEX=1
ADSB_RTL_INDEX=0
RTL_AIS_MEDIAN_DELTA_DB=10.660
RTL_AIS_PEAK_DELTA_DB=26.470
```

`ais-rtl-tcp.service` can then use:

```ini
EnvironmentFile=/run/rtl-antenna-picker.env
ExecStart=/bin/sh -eu -c 'exec /usr/bin/rtl_tcp -d "$${AIS_RTL_INDEX:?missing AIS_RTL_INDEX}" -a 0.0.0.0 -p 10101'
```

With `--apply-piaware`, the picker applies the ADS-B index through `piaware-config`.

## Non-goals

- It does not write RTL EEPROMs.
- It does not permanently rename devices.
- It does not kill or restart live radio services.
- It does not claim that runtime indexes are universally stable; it makes indexes fresh immediately before consumers start.
