#!/usr/bin/env python3
"""Choose RTL-SDR receiver roles from RF antenna fingerprints.

The tool measures each free RTL-SDR with ``rtl_power`` over one or more named
role bands, then assigns distinct runtime device indexes to those roles. It is
useful when serials, EEPROM labels, or kernel enumeration order are unreliable
but the antennas themselves have distinguishable RF fingerprints.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Sequence

DEFAULT_AIS_BAND = "156M:163M:5k"
DEFAULT_ADSB_BAND = "1088M:1092M:5k"
# AIS chains are VHF-filtered or tuned near 162 MHz, so the AIS band rises far
# above the same device's FM band; wideband or 1090 chains do not. Absolute
# levels mostly measure dongle gain and LNA noise instead.
DEFAULT_AIS_REFERENCE_BAND = "88M:108M:20k"
DEFAULT_GAIN = "33.8"
DEFAULT_INTEGRATION = "1"
DEFAULT_DURATION = "3s"
DEFAULT_MIN_AIS_MEDIAN_DELTA_DB = 6.0
SCORE_STATS = ("median", "p95", "peak")
ASSIGNMENT_STRATEGIES = ("priority", "maximize-total")
MEMORY_FILE = "roles-by-serial.json"
ROLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
ENV_SAFE_RE = re.compile(r"[^A-Za-z0-9]")


@dataclass(frozen=True)
class BandSummary:
    median_db: float
    p95_db: float
    peak_db: float
    peak_hz: float | None
    sample_count: int


@dataclass(frozen=True)
class RoleSpec:
    name: str
    band: str
    min_delta_db: float | None = None
    reference_band: str | None = None

    @property
    def reference_key(self) -> str:
        return f"{self.name}:reference"


@dataclass(frozen=True)
class DeviceMetrics:
    index: int
    role_summaries: dict[str, BandSummary]


@dataclass(frozen=True)
class Selection:
    assignments: dict[str, int]
    role_deltas_db: dict[str, float]
    metrics: list[DeviceMetrics]
    roles: list[RoleSpec]
    score_stat: str
    assignment_strategy: str
    ambiguous_roles: tuple[str, ...] = ()

    def index_for(self, role_name: str) -> int:
        return self.assignments[normalize_role_name(role_name)]


@dataclass(frozen=True)
class RuntimeConfig:
    roles: list[RoleSpec]
    score_stat: str
    assignment_strategy: str
    gain: str
    integration: str
    duration: str


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
    match = re.search(r"found\s+(\d+)\s+device", output, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def parse_rtl_test_serials(output: str) -> dict[int, str]:
    return {
        int(match.group(1)): match.group(2)
        for match in re.finditer(r"^\s*(\d+):.*\bSN:\s*(\S+)", output, flags=re.MULTILINE)
    }


def run_rtl_test(rtl_test: str, timeout_seconds: float) -> str:
    result = subprocess.run(
        [rtl_test, "-t"],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    return f"{result.stdout}\n{result.stderr}"


def detect_device_count(rtl_test: str, timeout_seconds: float) -> int:
    output = run_rtl_test(rtl_test, timeout_seconds)
    count = parse_rtl_test_device_count(output)
    if count is None:
        raise RuntimeError(f"could not parse RTL device count from {rtl_test} -t output:\n{output}")
    return count


def normalize_role_name(raw: str) -> str:
    name = raw.strip().lower()
    if not ROLE_NAME_RE.match(name):
        raise ValueError(
            f"invalid role name {raw!r}; use letters, digits, underscore, dot, or dash, and start with a letter"
        )
    return name


def role_env_name(role_name: str) -> str:
    return ENV_SAFE_RE.sub("_", normalize_role_name(role_name)).upper()


def parse_role_arg(raw: str) -> RoleSpec:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("role must be NAME=BAND, for example ais=156M:163M:5k")
    name, band = raw.split("=", 1)
    name = normalize_role_name(name)
    band = band.strip()
    if not band:
        raise argparse.ArgumentTypeError(f"role {name!r} has an empty band")
    return RoleSpec(name=name, band=band)


def parse_role_reference_arg(raw: str) -> tuple[str, str]:
    name, sep, band = raw.partition("=")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError("role reference must be NAME=BAND, for example ais=88M:108M:20k")
    return normalize_role_name(name), band.strip()


def parse_role_delta_arg(raw: str) -> tuple[str, float]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("role delta must be NAME=DB, for example ais=6")
    name, value = raw.split("=", 1)
    try:
        delta = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid dB delta {value!r}") from exc
    if not math.isfinite(delta) or delta < 0:
        raise argparse.ArgumentTypeError("role delta must be a finite non-negative number")
    return normalize_role_name(name), delta


def parse_pin_serial_arg(raw: str) -> tuple[str, str]:
    name, sep, serial = raw.partition("=")
    if not sep or not name.strip() or not serial.strip():
        raise argparse.ArgumentTypeError("serial pin must be NAME=SERIAL, for example adsb=00001090")
    return normalize_role_name(name), serial.strip()


def load_json_config(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    with path.open() as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("config JSON must be an object")
    return payload


def roles_from_config(payload: dict[str, object]) -> list[RoleSpec]:
    raw_roles = payload.get("roles", [])
    if not isinstance(raw_roles, list):
        raise ValueError("config field 'roles' must be a list")
    roles: list[RoleSpec] = []
    for item in raw_roles:
        if not isinstance(item, dict):
            raise ValueError("each config role must be an object")
        if "name" not in item or "band" not in item:
            raise ValueError("each config role needs 'name' and 'band'")
        min_delta = item.get("min_delta_db")
        if min_delta is not None:
            min_delta = float(min_delta)
            if not math.isfinite(min_delta) or min_delta < 0:
                raise ValueError("config min_delta_db must be a finite non-negative number")
        reference = item.get("reference_band")
        roles.append(
            RoleSpec(
                normalize_role_name(str(item["name"])),
                str(item["band"]),
                min_delta,
                str(reference) if reference else None,
            )
        )
    return roles


def merge_roles(
    base_roles: Sequence[RoleSpec],
    overrides: dict[str, float],
    reference_overrides: dict[str, str] | None = None,
) -> list[RoleSpec]:
    reference_overrides = reference_overrides or {}
    seen_env_names: dict[str, str] = {}
    merged: list[RoleSpec] = []
    for role in base_roles:
        min_delta = overrides.get(role.name, role.min_delta_db)
        env_name = role_env_name(role.name)
        if env_name in seen_env_names and seen_env_names[env_name] != role.name:
            raise ValueError(f"role names {seen_env_names[env_name]!r} and {role.name!r} map to the same env name {env_name}")
        seen_env_names[env_name] = role.name
        if any(existing.name == role.name for existing in merged):
            raise ValueError(f"duplicate role name {role.name!r}")
        reference = reference_overrides.get(role.name, role.reference_band) or None
        merged.append(RoleSpec(role.name, role.band, min_delta, reference))
    if not merged:
        raise ValueError("at least one role is required")
    return merged


def build_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    config = load_json_config(args.config)
    config_roles = roles_from_config(config)
    cli_roles = list(args.role or [])

    if config_roles or cli_roles:
        role_by_name: dict[str, RoleSpec] = {role.name: role for role in config_roles}
        order = [role.name for role in config_roles]
        for role in cli_roles:
            if role.name not in role_by_name:
                order.append(role.name)
            role_by_name[role.name] = role
        roles = [role_by_name[name] for name in order]
    else:
        roles = [
            RoleSpec("ais", args.ais_band, args.min_ais_median_delta_db, args.ais_reference_band or None),
            RoleSpec("adsb", args.adsb_band, None),
        ]

    delta_overrides = dict(args.min_role_delta or [])
    # Backward compatibility: if a user keeps an 'ais' role, preserve the old
    # conservative default unless config/CLI already supplied a role-specific value.
    if "ais" not in delta_overrides and any(role.name == "ais" and role.min_delta_db is None for role in roles):
        delta_overrides["ais"] = args.min_ais_median_delta_db

    roles = merge_roles(roles, delta_overrides, dict(args.role_reference or []))
    score_stat = str(config.get("score_stat", args.score_stat))
    if score_stat not in SCORE_STATS:
        raise ValueError(f"score_stat must be one of {', '.join(SCORE_STATS)}")
    assignment_strategy = str(config.get("assignment_strategy", args.assignment_strategy))
    if assignment_strategy not in ASSIGNMENT_STRATEGIES:
        raise ValueError(f"assignment_strategy must be one of {', '.join(ASSIGNMENT_STRATEGIES)}")
    return RuntimeConfig(
        roles=roles,
        score_stat=score_stat,
        assignment_strategy=assignment_strategy,
        gain=args.gain,
        integration=args.integration,
        duration=args.duration,
    )


def band_score(summary: BandSummary, stat: str) -> float:
    if stat == "median":
        return summary.median_db
    if stat == "p95":
        return summary.p95_db
    if stat == "peak":
        return summary.peak_db
    raise ValueError(f"unsupported score stat {stat!r}")


def role_score(metric: DeviceMetrics, role: RoleSpec, stat: str) -> float:
    score = band_score(metric.role_summaries[role.name], stat)
    if role.reference_band:
        score -= band_score(metric.role_summaries[role.reference_key], stat)
    return score


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
    device_indexes: Sequence[int],
    roles: Sequence[RoleSpec],
    gain: str,
    integration: str,
    duration: str,
    timeout_seconds: float,
    work_dir: Path,
) -> list[DeviceMetrics]:
    metrics: list[DeviceMetrics] = []
    for index in device_indexes:
        role_summaries: dict[str, BandSummary] = {}
        for role in roles:
            output_path = work_dir / f"device{index}-{role_env_name(role.name).lower()}.csv"
            run_rtl_power(
                rtl_power=rtl_power,
                device_index=index,
                band=role.band,
                gain=gain,
                integration=integration,
                duration=duration,
                timeout_seconds=timeout_seconds,
                output_path=output_path,
            )
            role_summaries[role.name] = parse_rtl_power_csv(output_path)
            if role.reference_band:
                reference_path = work_dir / f"device{index}-{role_env_name(role.name).lower()}-reference.csv"
                run_rtl_power(
                    rtl_power=rtl_power,
                    device_index=index,
                    band=role.reference_band,
                    gain=gain,
                    integration=integration,
                    duration=duration,
                    timeout_seconds=timeout_seconds,
                    output_path=reference_path,
                )
                role_summaries[role.reference_key] = parse_rtl_power_csv(reference_path)
        metrics.append(DeviceMetrics(index=index, role_summaries=role_summaries))
    return metrics


def choose_selection(
    metrics: Sequence[DeviceMetrics],
    roles: Sequence[RoleSpec],
    score_stat: str = "median",
    assignment_strategy: str = "priority",
    require_decisive: bool = True,
) -> Selection:
    if not roles:
        raise ValueError("at least one role is required")
    if len(metrics) < len(roles):
        raise ValueError(f"need at least {len(roles)} RTL devices for {len(roles)} roles, found {len(metrics)}")
    if score_stat not in SCORE_STATS:
        raise ValueError(f"score_stat must be one of {', '.join(SCORE_STATS)}")
    if assignment_strategy not in ASSIGNMENT_STRATEGIES:
        raise ValueError(f"assignment_strategy must be one of {', '.join(ASSIGNMENT_STRATEGIES)}")

    metrics_by_index = {metric.index: metric for metric in metrics}
    if len(metrics_by_index) != len(metrics):
        raise ValueError("duplicate device indexes in metrics")

    device_indexes = [metric.index for metric in metrics]
    for role in roles:
        for metric in metrics:
            if role.name not in metric.role_summaries:
                raise ValueError(f"missing metrics for role {role.name!r} on device {metric.index}")
            if role.reference_band and role.reference_key not in metric.role_summaries:
                raise ValueError(f"missing reference metrics for role {role.name!r} on device {metric.index}")

    if assignment_strategy == "priority":
        remaining = set(device_indexes)
        best_assignment = {}
        for role in roles:
            selected_index = max(
                remaining,
                key=lambda index: (role_score(metrics_by_index[index], role, score_stat), -index),
            )
            best_assignment[role.name] = selected_index
            remaining.remove(selected_index)
    else:
        best_assignment: dict[str, int] | None = None
        best_score: float | None = None
        for candidate_indexes in itertools.permutations(device_indexes, len(roles)):
            score = 0.0
            for role, index in zip(roles, candidate_indexes, strict=True):
                score += role_score(metrics_by_index[index], role, score_stat)
            assignment = {role.name: index for role, index in zip(roles, candidate_indexes, strict=True)}
            tie_break = tuple(candidate_indexes)
            best_tie_break = tuple(best_assignment[role.name] for role in roles) if best_assignment else ()
            if best_score is None or score > best_score or (score == best_score and tie_break < best_tie_break):
                best_score = score
                best_assignment = assignment
        if best_assignment is None:
            raise ValueError("could not assign roles to devices")

    deltas: dict[str, float] = {}
    ambiguous: list[str] = []
    for role in roles:
        selected_index = best_assignment[role.name]
        selected_score = role_score(metrics_by_index[selected_index], role, score_stat)
        runner_up_scores = [
            role_score(metric, role, score_stat)
            for metric in metrics
            if metric.index != selected_index
        ]
        delta = math.inf if not runner_up_scores else selected_score - max(runner_up_scores)
        deltas[role.name] = delta
        if role.min_delta_db is not None and delta < role.min_delta_db:
            if not require_decisive:
                ambiguous.append(role.name)
                continue
            raise ValueError(
                f"{role.name} antenna fingerprint is not decisive: "
                f"selected device {selected_index} {score_stat} {selected_score:.1f} dB, "
                f"delta {delta:.1f} dB < required {role.min_delta_db:.1f} dB"
            )

    return Selection(
        assignments=best_assignment,
        role_deltas_db=deltas,
        metrics=list(metrics),
        roles=list(roles),
        score_stat=score_stat,
        assignment_strategy=assignment_strategy,
        ambiguous_roles=tuple(ambiguous),
    )


def select_by_serial(
    roles: Sequence[RoleSpec],
    pins: dict[str, str],
    serials: dict[int, str],
    score_stat: str = "median",
) -> Selection:
    """Assign every role to the device carrying its pinned USB serial.

    Use this when RF fingerprints are ambiguous but serials are unique: the
    kernel index of a serial can move between boots, the serial cannot.
    """
    role_names = {role.name for role in roles}
    unknown = sorted(set(pins) - role_names)
    if unknown:
        raise ValueError(f"serial pins for unknown roles: {', '.join(unknown)}")
    unpinned = [role.name for role in roles if role.name not in pins]
    if unpinned:
        raise ValueError(f"serial pinning needs a pin for every role; missing: {', '.join(unpinned)}")

    assignments: dict[str, int] = {}
    for role in roles:
        matches = [index for index, serial in serials.items() if serial == pins[role.name]]
        if len(matches) != 1:
            raise ValueError(f"{role.name} serial {pins[role.name]!r} matched {len(matches)} devices: {serials}")
        assignments[role.name] = matches[0]
    if len(set(assignments.values())) != len(assignments):
        raise ValueError(f"serial pins map several roles to one device: {assignments}")

    return Selection(
        assignments=assignments,
        role_deltas_db={role.name: math.inf for role in roles},
        metrics=[],
        roles=list(roles),
        score_stat=score_stat,
        assignment_strategy="serial-pin",
    )


def load_memory(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return payload


def save_memory(path: Path, memory: dict) -> None:
    write_text_atomic(path, json.dumps(memory, indent=2, sort_keys=True) + "\n")


def remember_selection(memory: dict, selection: Selection, serials: dict[int, str], source: str, timestamp: str) -> dict:
    """Record the role -> USB serial mapping so later boots survive index reordering."""

    if len(set(serials.values())) != len(serials):
        return memory
    if any(index not in serials for index in selection.assignments.values()):
        return memory
    updated = dict(memory)
    updated["roles"] = {role: serials[index] for role, index in selection.assignments.items()}
    updated["source"] = source
    updated["updated"] = timestamp
    return updated


def select_from_memory(
    roles: Sequence[RoleSpec],
    memory: dict,
    serials: dict[int, str],
    score_stat: str = "median",
) -> Selection | None:
    pins = memory.get("roles")
    if not isinstance(pins, dict) or set(pins) != {role.name for role in roles}:
        return None
    try:
        selection = select_by_serial(roles, pins, serials, score_stat)
    except ValueError:
        return None
    return replace(selection, assignment_strategy="memory")


def shell_env_lines(selection: Selection, timestamp: str) -> list[str]:
    role_names = [role.name for role in selection.roles]
    lines = [
        f"RTL_ANTENNA_PICKER_TIMESTAMP={shlex.quote(timestamp)}",
        f"RTL_ANTENNA_PICKER_SCORE_STAT={selection.score_stat}",
        f"RTL_ANTENNA_PICKER_ASSIGNMENT_STRATEGY={selection.assignment_strategy}",
        f"RTL_ANTENNA_PICKER_ROLES={shlex.quote(' '.join(role_names))}",
    ]

    for role in selection.roles:
        env_role = role_env_name(role.name)
        lines.append(f"{env_role}_RTL_INDEX={selection.assignments[role.name]}")
        delta = selection.role_deltas_db[role.name]
        delta_value = "inf" if math.isinf(delta) else f"{delta:.3f}"
        lines.append(f"RTL_{env_role}_{selection.score_stat.upper()}_DELTA_DB={delta_value}")

    for metric in selection.metrics:
        for role in selection.roles:
            summary = metric.role_summaries[role.name]
            prefix = f"RTL_DEVICE_{metric.index}_{role_env_name(role.name)}"
            lines.extend(
                [
                    f"{prefix}_MEDIAN_DB={summary.median_db:.3f}",
                    f"{prefix}_P95_DB={summary.p95_db:.3f}",
                    f"{prefix}_PEAK_DB={summary.peak_db:.3f}",
                    f"{prefix}_PEAK_HZ={summary.peak_hz:.1f}" if summary.peak_hz is not None else f"{prefix}_PEAK_HZ=",
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


def selection_payload(selection: Selection, timestamp: str, runtime: RuntimeConfig) -> dict[str, object]:
    selected_roles = {
        role.name: {
            "index": selection.assignments[role.name],
            f"{selection.score_stat}_delta_db": selection.role_deltas_db[role.name],
        }
        for role in selection.roles
    }
    selected: dict[str, object] = {
        "roles": selected_roles,
        "score_stat": selection.score_stat,
        "assignment_strategy": selection.assignment_strategy,
    }
    # Compatibility for older consumers and simple jq one-liners.
    for role_name in ("ais", "adsb"):
        if role_name in selection.assignments:
            selected[f"{role_name}_index"] = selection.assignments[role_name]
            selected[f"{role_name}_{selection.score_stat}_delta_db"] = selection.role_deltas_db[role_name]

    return {
        "timestamp": timestamp,
        "selected": selected,
        "bands": {role.name: role.band for role in selection.roles},
        "reference_bands": {role.name: role.reference_band for role in selection.roles if role.reference_band},
        "rtl_power": {"gain": runtime.gain, "integration": runtime.integration, "duration": runtime.duration},
        "metrics": [
            {
                "index": metric.index,
                "roles": {role.name: asdict(metric.role_summaries[role.name]) for role in selection.roles},
                "references": {
                    role.name: asdict(metric.role_summaries[role.reference_key])
                    for role in selection.roles
                    if role.reference_key in metric.role_summaries
                },
            }
            for metric in selection.metrics
        ],
    }


def apply_piaware_index(piaware_config: str, role_name: str, selection: Selection) -> None:
    role_name = normalize_role_name(role_name)
    if role_name not in selection.assignments:
        raise RuntimeError(f"cannot apply PiAware index: role {role_name!r} is not selected")
    index = selection.assignments[role_name]
    result = subprocess.run(
        [piaware_config, "rtlsdr-device-index", str(index)],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{piaware_config} rtlsdr-device-index {index} failed rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )



def find_systemd_template_root() -> Path:
    """Return the PiAware/AIS systemd template directory.

    Source checkouts keep templates under ``systemd/piaware``. Built wheels
    install the same files under ``share/rtl-antenna-picker/systemd/piaware``.
    """
    module_dir = Path(__file__).resolve().parent
    candidates = [base / "systemd" / "piaware" for base in (module_dir, *module_dir.parents)]
    candidates.append(Path(sys.prefix) / "share" / "rtl-antenna-picker" / "systemd" / "piaware")
    for candidate in candidates:
        if (candidate / "rtl-antenna-picker.service").exists():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"could not find systemd templates; searched {searched}")


def render_template(path: Path, replacements: dict[str, str]) -> str:
    text = path.read_text()
    for key, value in replacements.items():
        text = text.replace(key, value)
    unresolved = sorted(set(re.findall(r"@[A-Z0-9_]+@", text)))
    if unresolved:
        raise ValueError(f"unresolved template placeholders in {path}: {', '.join(unresolved)}")
    return text


def install_rendered_file(source: Path, destination: Path, replacements: dict[str, str], mode: int = 0o644) -> None:
    write_text_atomic(destination, render_template(source, replacements), mode=mode)


def install_piaware_ais_systemd_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install PiAware/AIS systemd templates for rtl-antenna-picker.")
    parser.add_argument("--systemd-dir", type=Path, default=Path("/etc/systemd/system"))
    parser.add_argument("--picker-bin", help="absolute rtl-antenna-picker path for systemd ExecStart")
    parser.add_argument("--template-root", type=Path, help="override systemd template source directory")
    parser.add_argument("--no-daemon-reload", action="store_true")
    parser.add_argument("--no-enable", action="store_true")
    parser.add_argument("--force-complete-ais-service", action="store_true", help="install the complete ais-rtl-tcp.service even if one already exists")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0 and not args.dry_run:
        raise RuntimeError("installing systemd files requires root; rerun with sudo")

    picker_bin = args.picker_bin or shutil.which("rtl-antenna-picker")
    if not picker_bin:
        raise RuntimeError("could not find rtl-antenna-picker in PATH; pass --picker-bin /absolute/path")
    picker_path = Path(picker_bin)
    if not picker_path.is_absolute():
        raise RuntimeError(f"--picker-bin must be absolute, got {picker_bin!r}")

    template_root = args.template_root or find_systemd_template_root()
    replacements = {"@RTL_ANTENNA_PICKER@": str(picker_path)}
    operations: list[tuple[Path, Path]] = [
        (template_root / "rtl-antenna-picker.service", args.systemd_dir / "rtl-antenna-picker.service"),
        (
            template_root / "dump1090-fa.service.d" / "10-rtl-antenna-picker.conf",
            args.systemd_dir / "dump1090-fa.service.d" / "10-rtl-antenna-picker.conf",
        ),
    ]

    existing_ais_unit = (args.systemd_dir / "ais-rtl-tcp.service").exists() or Path("/lib/systemd/system/ais-rtl-tcp.service").exists()
    if args.force_complete_ais_service or not existing_ais_unit:
        operations.append((template_root / "ais-rtl-tcp.service", args.systemd_dir / "ais-rtl-tcp.service"))
        enable_ais = True
    else:
        operations.append(
            (
                template_root / "ais-rtl-tcp.service.d" / "10-rtl-antenna-picker.conf",
                args.systemd_dir / "ais-rtl-tcp.service.d" / "10-rtl-antenna-picker.conf",
            )
        )
        enable_ais = False

    for source, destination in operations:
        if args.dry_run:
            print(f"would install {source} -> {destination}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            install_rendered_file(source, destination, replacements)
            print(f"installed {destination}")

    if not args.dry_run and not args.no_daemon_reload:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
    if not args.dry_run and not args.no_enable:
        subprocess.run(["systemctl", "enable", "rtl-antenna-picker.service"], check=True)
        if enable_ais:
            subprocess.run(["systemctl", "enable", "ais-rtl-tcp.service"], check=True)

    return 0

def positive_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return value


def non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-env", type=Path, default=Path("/run/rtl-antenna-picker.env"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/rtl-antenna-picker"))
    parser.add_argument("--config", type=Path, help="JSON config with roles, for example {'roles':[{'name':'ais','band':'156M:163M:5k'}]}")
    parser.add_argument("--rtl-test", default="rtl_test")
    parser.add_argument("--rtl-power", default="rtl_power")
    parser.add_argument("--piaware-config", default="piaware-config")
    parser.add_argument("--device-count", type=int, help="override auto-detected RTL device count")
    parser.add_argument("--device-index", type=non_negative_int, action="append", help="measure only this runtime device index; repeatable")
    parser.add_argument("--role", type=parse_role_arg, action="append", metavar="NAME=BAND", help="role band to measure; repeatable, e.g. ais=156M:163M:5k")
    parser.add_argument("--role-reference", type=parse_role_reference_arg, action="append", metavar="NAME=BAND", help="score the role by its band minus this reference band on the same device; empty BAND disables")
    parser.add_argument("--ais-reference-band", default=DEFAULT_AIS_REFERENCE_BAND, help="reference band for the default AIS role; empty disables")
    parser.add_argument("--min-role-delta", type=parse_role_delta_arg, action="append", metavar="NAME=DB", help="require selected role to beat the next device by this dB delta")
    parser.add_argument("--score-stat", choices=SCORE_STATS, default="median", help="band summary statistic used for assignment")
    parser.add_argument("--assignment-strategy", choices=ASSIGNMENT_STRATEGIES, default="priority", help="role assignment strategy: priority assigns roles in CLI/config order; maximize-total optimizes total score")
    parser.add_argument("--ais-band", default=DEFAULT_AIS_BAND, help="legacy/default AIS role band used when --role is absent")
    parser.add_argument("--adsb-band", default=DEFAULT_ADSB_BAND, help="legacy/default ADS-B role band used when --role is absent")
    parser.add_argument("--gain", default=DEFAULT_GAIN)
    parser.add_argument("--integration", default=DEFAULT_INTEGRATION)
    parser.add_argument("--duration", default=DEFAULT_DURATION)
    parser.add_argument("--command-timeout", type=positive_float, default=20.0)
    parser.add_argument("--min-ais-median-delta-db", type=float, default=DEFAULT_MIN_AIS_MEDIAN_DELTA_DB, help="legacy shortcut for --min-role-delta ais=DB")
    parser.add_argument("--apply-piaware", action="store_true", help="write PiAware rtlsdr-device-index to the selected --piaware-role index")
    parser.add_argument("--piaware-role", default="adsb", help="selected role to apply to PiAware when --apply-piaware is used")
    parser.add_argument("--pin-serial", type=parse_pin_serial_arg, action="append", metavar="NAME=SERIAL", help="assign the role to the device with this USB serial instead of measuring RF; repeat for every role")
    parser.add_argument("--learn", action="store_true", help="remember roles by USB serial, reuse them on later boots, and never fail on an ambiguous fingerprint (best guess instead)")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime = build_runtime_config(args)

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    memory_path = args.state_dir / MEMORY_FILE
    if args.pin_serial:
        serials = parse_rtl_test_serials(run_rtl_test(args.rtl_test, args.command_timeout))
        selection = select_by_serial(runtime.roles, dict(args.pin_serial), serials, runtime.score_stat)
        return emit_selection(args, runtime, selection, timestamp)

    serials: dict[int, str] = {}
    memory: dict = {}
    if args.learn:
        rtl_test_output = run_rtl_test(args.rtl_test, args.command_timeout)
        serials = parse_rtl_test_serials(rtl_test_output)
        memory = load_memory(memory_path)
        if not args.device_index and not args.device_count:
            args.device_count = parse_rtl_test_device_count(rtl_test_output)

    if args.device_index:
        device_indexes = sorted(dict.fromkeys(args.device_index))
    else:
        device_count = args.device_count or detect_device_count(args.rtl_test, args.command_timeout)
        if device_count < 1:
            raise RuntimeError(f"need at least one RTL device, found {device_count}")
        device_indexes = list(range(device_count))

    if len(device_indexes) < len(runtime.roles):
        raise RuntimeError(f"need at least {len(runtime.roles)} RTL devices for roles {', '.join(role.name for role in runtime.roles)}")

    args.state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rtl-antenna-picker-", dir=str(args.state_dir)) as tmp_dir:
        metrics = collect_metrics(
            rtl_power=args.rtl_power,
            device_indexes=device_indexes,
            roles=runtime.roles,
            gain=runtime.gain,
            integration=runtime.integration,
            duration=runtime.duration,
            timeout_seconds=args.command_timeout,
            work_dir=Path(tmp_dir),
        )

    selection = choose_selection(
        metrics,
        runtime.roles,
        runtime.score_stat,
        runtime.assignment_strategy,
        require_decisive=not args.learn,
    )
    if args.learn:
        if not selection.ambiguous_roles:
            save_memory(memory_path, remember_selection(memory, selection, serials, "fingerprint", timestamp))
        else:
            remembered = select_from_memory(runtime.roles, memory, serials, runtime.score_stat)
            fallback = "last decisive mapping by serial" if remembered is not None else "best guess"
            print(
                f"rtl-antenna-picker: fingerprint not decisive for {', '.join(selection.ambiguous_roles)}; using {fallback}",
                file=sys.stderr,
            )
            if remembered is not None:
                selection = replace(remembered, metrics=selection.metrics)
    return emit_selection(args, runtime, selection, timestamp)


def emit_selection(args: argparse.Namespace, runtime: RuntimeConfig, selection: Selection, timestamp: str) -> int:
    payload = selection_payload(selection, timestamp, runtime)

    env_text = "\n".join(shell_env_lines(selection, timestamp)) + "\n"
    write_text_atomic(args.output_env, env_text)
    write_text_atomic(args.state_dir / "latest.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")

    if args.apply_piaware:
        apply_piaware_index(args.piaware_config, args.piaware_role, selection)

    assignments = ", ".join(f"{role.name} RTL index {selection.assignments[role.name]}" for role in runtime.roles)
    deltas = ", ".join(
        f"{role.name} {runtime.score_stat} delta {selection.role_deltas_db[role.name]:.1f} dB"
        for role in runtime.roles
        if not math.isinf(selection.role_deltas_db[role.name])
    )
    print(f"selected {assignments}; {deltas}", file=sys.stderr)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - command-line service should log the reason.
        print(f"rtl-antenna-picker: {exc}", file=sys.stderr)
        raise SystemExit(1)
