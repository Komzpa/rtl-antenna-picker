# Design

## Problem

RTL-SDR receivers are often selected by serial number or runtime index. Both can be wrong in multi-dongle stations:

- inexpensive dongles may ship with duplicate EEPROM serials;
- some tools display a different serial than the one written in EEPROM;
- runtime indexes can change after reboot or after a dongle is moved;
- physically identical dongles may be connected to very different antennas.

If two receivers feed different RF chains, selecting the wrong dongle can put a demodulator on the wrong antenna. The tool therefore treats the antenna fingerprint as the source of truth.

## Role model

A role is a named receiver purpose plus an `rtl_power` band range, for example:

```sh
--role ais=156M:163M:5k
--role adsb=1088M:1092M:5k
--role uhf=430M:440M:10k
```

Each role is measured on each candidate device. For every measurement, the picker parses the `rtl_power` CSV bins and computes:

- median dB;
- p95 dB;
- peak dB;
- peak frequency;
- sample count.

Role names are normalized to lowercase internally. Environment variable names use uppercase role names with non-alphanumeric characters converted to underscores.

## Assignment strategies

### `priority` (default)

Roles are assigned in order. Each role gets the best remaining device for its configured score statistic.

This is conservative for live stations: put the most important or most easily fingerprinted role first and optionally add a `min_delta_db` guard for it. The default AIS/ADS-B profile uses this strategy so the behavior remains “pick the VHF/AIS antenna first, then assign ADS-B to the remaining best device.”

### `maximize-total`

The picker evaluates all distinct role/device permutations and chooses the assignment with the highest sum of role scores.

This is useful when all roles are equally important and well-characterized, but it can surprise operators if one very strong role steals a device from a higher-priority role. Use `priority` when operational priority matters.

## Score statistic

The score statistic is configurable:

- `median` — robust band-floor discriminator and default;
- `p95` — emphasizes strong upper-bin behavior without relying on a single bin;
- `peak` — strongest single-bin discriminator.

Median is the safest default when the antenna’s broad RF environment is more stable than individual transient signals.

## Delta guards

A role may specify a required delta:

```sh
--min-role-delta ais=6
```

The selected device’s score for that role must beat the next-best device by at least that many dB. If not, the run fails instead of guessing.

Delta guards are role-specific because not every role has the same RF contrast. In the AIS/ADS-B default profile, AIS/VHF is usually the clearer discriminator, while ADS-B may be assigned to the remaining device.

## Output contract

The environment file is intended for systemd `EnvironmentFile=` consumers:

```sh
AIS_RTL_INDEX=1
ADSB_RTL_INDEX=0
RTL_AIS_MEDIAN_DELTA_DB=10.660
RTL_DEVICE_1_AIS_MEDIAN_DB=-20.460
```

The JSON diagnostic output is role-neutral:

```json
{
  "selected": {
    "roles": {
      "ais": {"index": 1, "median_delta_db": 10.66},
      "adsb": {"index": 0, "median_delta_db": -2.245}
    },
    "score_stat": "median",
    "assignment_strategy": "priority"
  }
}
```

Default `ais` and `adsb` roles also expose compatibility aliases such as `selected.ais_index`.

## PiAware compatibility

With `--apply-piaware`, the picker applies the selected role index through:

```sh
piaware-config rtlsdr-device-index <index>
```

The role defaults to `adsb` and can be changed with `--piaware-role NAME`.

## Non-goals

- It does not write RTL EEPROMs.
- It does not permanently rename devices.
- It does not kill or restart live radio services.
- It does not claim runtime indexes are universally stable; it makes indexes fresh immediately before consumers start.
