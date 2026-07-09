#!/usr/bin/env python3
"""Choose RTL-SDR roles from RF antenna fingerprints.

This is intended for the balcony PiAware host where two RTL-SDR dongles are
connected to different antennas but may not have trustworthy unique selectors.
It measures each free dongle in the AIS/VHF and ADS-B bands, picks the dongle
with the strongest AIS-band median as the AIS receiver, and uses the other
strongest ADS-B candidate for dump1090-fa.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

DEFAULT_AIS_BAND = "156M:163M:5k"
DEFAULT_ADSB_BAND = "1088M:1092M:5k"
DEFAULT_GAIN = "33.8"
DEFAULT_INTEGRATION = "1"
DEFAULT_DURATION = "3s"
DEFAULT_MIN_AIS_MEDIAN_DELTA_DB = 6.0


@dataclass(frozen=True)
class BandSummary:
    median_db: float
    p95_db: float
    peak_db: float
    peak_hz: float | None
    sample_count: int


@dataclass(frozen=True)
class DeviceMetrics:
    index: int
    ais: BandSummary
    adsb: BandSummary


@dataclass(frozen=True)
class Selection:
    ais_index: int
    adsb_index: int
    ais_median_delta_db: float
    ais_peak_delta_db: float
    metrics: list[DeviceMetrics]


def parse_rtl_power_csv(path: Path) -> BandSummary:
    values: list[float] = []
    peak_db = -math.inf
    peak_hz: float | None = None

    with path.open(newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 7:
                continue
            try:
                low_hz = float(row[2])
                step_hz = float(row[4])
            except ValueError:
                low_hz = 0.0
                step_hz = 0.0
            for offset, raw in enumerate(row[6:]):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                values.append(value)
                if value > peak_db:
                    peak_db = value
                    peak_hz = low_hz + (offset + 0.5) * step_hz if step_hz else None

    if not values:
        raise ValueError(f"no rtl_power bins parsed from {path}")

    ordered = sorted(values)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return BandSummary(
        median_db=statistics.median(ordered),
        p95_db=ordered[p95_index],
        peak_db=peak_db,
        peak_hz=peak_hz,
        sample_count=len(ordered),
    )


def parse_rtl_test_device_count(output: str) -> int | None:
    match = re.search(r"Found\s+(\d+)\s+device", output)
    if not match:
        return None
    return int(match.group(1))


def detect_device_count(rtl_test: str, timeout_seconds: float) -> int:
    result = subprocess.run(
        [rtl_test, "-t"],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    output = f"{result.stdout}\n{result.stderr}"
    count = parse_rtl_test_device_count(output)
    if count is None:
        raise RuntimeError(f"could not parse RTL device count from {rtl_test} -t output:\n{output}")
    return count


def run_rtl_power(
    *,
    rtl_power: str,
    device_index: int,
    band: str,
    gain: str,
    integration: str,
    duration: str,
    timeout_seconds: float,
    output_path: Path,
) -> None:
    command = [
        rtl_power,
        "-d",
        str(device_index),
        "-g",
        gain,
        "-f",
        band,
        "-i",
        integration,
        "-e",
        duration,
        str(output_path),
    ]
    result = subprocess.run(command, check=False, text=True, capture_output=True, timeout=timeout_seconds)
    if result.returncode != 0:
        raise RuntimeError(
            "rtl_power failed for "
            f"device {device_index} band {band} rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def collect_metrics(
    *,
    rtl_power: str,
    device_count: int,
    ais_band: str,
    adsb_band: str,
    gain: str,
    integration: str,
    duration: str,
    timeout_seconds: float,
    work_dir: Path,
) -> list[DeviceMetrics]:
    metrics: list[DeviceMetrics] = []
    for index in range(device_count):
        ais_path = work_dir / f"device{index}-ais.csv"
        adsb_path = work_dir / f"device{index}-adsb.csv"
        run_rtl_power(
            rtl_power=rtl_power,
            device_index=index,
            band=ais_band,
            gain=gain,
            integration=integration,
            duration=duration,
            timeout_seconds=timeout_seconds,
            output_path=ais_path,
        )
        run_rtl_power(
            rtl_power=rtl_power,
            device_index=index,
            band=adsb_band,
            gain=gain,
            integration=integration,
            duration=duration,
            timeout_seconds=timeout_seconds,
            output_path=adsb_path,
        )
        metrics.append(
            DeviceMetrics(
                index=index,
                ais=parse_rtl_power_csv(ais_path),
                adsb=parse_rtl_power_csv(adsb_path),
            )
        )
    return metrics


def choose_selection(metrics: Sequence[DeviceMetrics], min_ais_median_delta_db: float) -> Selection:
    if len(metrics) < 2:
        raise ValueError(f"need at least two RTL devices, found {len(metrics)}")

    by_ais = sorted(metrics, key=lambda metric: metric.ais.median_db, reverse=True)
    ais = by_ais[0]
    runner_up = by_ais[1]
    median_delta = ais.ais.median_db - runner_up.ais.median_db
    peak_delta = ais.ais.peak_db - runner_up.ais.peak_db
    if median_delta < min_ais_median_delta_db:
        raise ValueError(
            "AIS antenna fingerprint is not decisive: "
            f"top device {ais.index} median {ais.ais.median_db:.1f} dB, "
            f"runner-up device {runner_up.index} median {runner_up.ais.median_db:.1f} dB, "
            f"delta {median_delta:.1f} dB < required {min_ais_median_delta_db:.1f} dB"
        )

    remaining = [metric for metric in metrics if metric.index != ais.index]
    adsb = max(remaining, key=lambda metric: metric.adsb.median_db)
    return Selection(
        ais_index=ais.index,
        adsb_index=adsb.index,
        ais_median_delta_db=median_delta,
        ais_peak_delta_db=peak_delta,
        metrics=list(metrics),
    )


def shell_env_lines(selection: Selection, timestamp: str) -> list[str]:
    lines = [
        f"RTL_ANTENNA_PICKER_TIMESTAMP={shlex.quote(timestamp)}",
        f"AIS_RTL_INDEX={selection.ais_index}",
        f"ADSB_RTL_INDEX={selection.adsb_index}",
        f"RTL_AIS_MEDIAN_DELTA_DB={selection.ais_median_delta_db:.3f}",
        f"RTL_AIS_PEAK_DELTA_DB={selection.ais_peak_delta_db:.3f}",
    ]
    for metric in selection.metrics:
        prefix = f"RTL_DEVICE_{metric.index}"
        lines.extend(
            [
                f"{prefix}_AIS_MEDIAN_DB={metric.ais.median_db:.3f}",
                f"{prefix}_AIS_P95_DB={metric.ais.p95_db:.3f}",
                f"{prefix}_AIS_PEAK_DB={metric.ais.peak_db:.3f}",
                f"{prefix}_AIS_PEAK_HZ={metric.ais.peak_hz:.1f}" if metric.ais.peak_hz is not None else f"{prefix}_AIS_PEAK_HZ=",
                f"{prefix}_ADSB_MEDIAN_DB={metric.adsb.median_db:.3f}",
                f"{prefix}_ADSB_P95_DB={metric.adsb.p95_db:.3f}",
                f"{prefix}_ADSB_PEAK_DB={metric.adsb.peak_db:.3f}",
                f"{prefix}_ADSB_PEAK_HZ={metric.adsb.peak_hz:.1f}" if metric.adsb.peak_hz is not None else f"{prefix}_ADSB_PEAK_HZ=",
            ]
        )
    return lines


def write_text_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def selection_payload(selection: Selection, timestamp: str, args: argparse.Namespace) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "selected": {
            "ais_index": selection.ais_index,
            "adsb_index": selection.adsb_index,
            "ais_median_delta_db": selection.ais_median_delta_db,
            "ais_peak_delta_db": selection.ais_peak_delta_db,
        },
        "bands": {"ais": args.ais_band, "adsb": args.adsb_band},
        "rtl_power": {"gain": args.gain, "integration": args.integration, "duration": args.duration},
        "metrics": [asdict(metric) for metric in selection.metrics],
    }


def apply_piaware_index(piaware_config: str, adsb_index: int) -> None:
    result = subprocess.run(
        [piaware_config, "rtlsdr-device-index", str(adsb_index)],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{piaware_config} rtlsdr-device-index {adsb_index} failed rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def positive_float(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-env", type=Path, default=Path("/run/rtl-antenna-picker.env"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/rtl-antenna-picker"))
    parser.add_argument("--rtl-test", default="rtl_test")
    parser.add_argument("--rtl-power", default="rtl_power")
    parser.add_argument("--piaware-config", default="piaware-config")
    parser.add_argument("--device-count", type=int, help="override auto-detected RTL device count")
    parser.add_argument("--ais-band", default=DEFAULT_AIS_BAND)
    parser.add_argument("--adsb-band", default=DEFAULT_ADSB_BAND)
    parser.add_argument("--gain", default=DEFAULT_GAIN)
    parser.add_argument("--integration", default=DEFAULT_INTEGRATION)
    parser.add_argument("--duration", default=DEFAULT_DURATION)
    parser.add_argument("--command-timeout", type=positive_float, default=20.0)
    parser.add_argument("--min-ais-median-delta-db", type=float, default=DEFAULT_MIN_AIS_MEDIAN_DELTA_DB)
    parser.add_argument("--apply-piaware", action="store_true", help="write PiAware rtlsdr-device-index to the selected ADS-B index")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    device_count = args.device_count or detect_device_count(args.rtl_test, args.command_timeout)
    if device_count < 2:
        raise RuntimeError(f"need at least two RTL devices, found {device_count}")

    args.state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rtl-antenna-picker-", dir=str(args.state_dir)) as tmp_dir:
        metrics = collect_metrics(
            rtl_power=args.rtl_power,
            device_count=device_count,
            ais_band=args.ais_band,
            adsb_band=args.adsb_band,
            gain=args.gain,
            integration=args.integration,
            duration=args.duration,
            timeout_seconds=args.command_timeout,
            work_dir=Path(tmp_dir),
        )

    selection = choose_selection(metrics, args.min_ais_median_delta_db)
    payload = selection_payload(selection, timestamp, args)

    env_text = "\n".join(shell_env_lines(selection, timestamp)) + "\n"
    write_text_atomic(args.output_env, env_text)
    write_text_atomic(args.state_dir / "latest.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")

    if args.apply_piaware:
        apply_piaware_index(args.piaware_config, selection.adsb_index)

    summary = (
        f"selected AIS RTL index {selection.ais_index}, ADS-B RTL index {selection.adsb_index}; "
        f"AIS median delta {selection.ais_median_delta_db:.1f} dB, "
        f"AIS peak delta {selection.ais_peak_delta_db:.1f} dB"
    )
    print(summary, file=sys.stderr)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - command-line service should log the reason.
        print(f"rtl-antenna-picker: {exc}", file=sys.stderr)
        raise SystemExit(1)
