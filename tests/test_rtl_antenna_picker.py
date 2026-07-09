import argparse

import pytest

import rtl_antenna_picker


def summary(median, p95=None, peak=None, peak_hz=None):
    return rtl_antenna_picker.BandSummary(
        median_db=median,
        p95_db=median if p95 is None else p95,
        peak_db=median if peak is None else peak,
        peak_hz=peak_hz,
        sample_count=10,
    )


def metrics(index, **roles):
    return rtl_antenna_picker.DeviceMetrics(index=index, role_summaries=roles)


def test_parse_rtl_test_device_count_accepts_common_variants():
    assert rtl_antenna_picker.parse_rtl_test_device_count("Found 2 device(s):") == 2
    assert rtl_antenna_picker.parse_rtl_test_device_count("Found 1 device(s):") == 1
    assert rtl_antenna_picker.parse_rtl_test_device_count("found 0 devices") == 0
    assert rtl_antenna_picker.parse_rtl_test_device_count("no supported devices") is None


def test_parse_rtl_power_csv_summary(tmp_path):
    csv_path = tmp_path / "scan.csv"
    csv_path.write_text(
        "2026-07-09,00:00:00,156000000,156010000,5000,1,-40.0,-30.0,-20.0\n"
    )
    parsed = rtl_antenna_picker.parse_rtl_power_csv(csv_path)
    assert parsed.sample_count == 3
    assert parsed.median_db == -30.0
    assert parsed.peak_db == -20.0
    assert parsed.peak_hz == 156012500.0


@pytest.mark.parametrize("raw", ["0", "-1", "inf", "nan"])
def test_positive_float_rejects_non_positive_or_non_finite(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        rtl_antenna_picker.positive_float(raw)


def test_choose_selection_default_roles_preserves_ais_first_behavior():
    roles = [
        rtl_antenna_picker.RoleSpec("ais", "156M:163M:5k", 6.0),
        rtl_antenna_picker.RoleSpec("adsb", "1088M:1092M:5k"),
    ]
    sample_metrics = [
        metrics(0, ais=summary(100.0), adsb=summary(100.0)),
        metrics(1, ais=summary(90.0), adsb=summary(0.0)),
    ]

    selected = rtl_antenna_picker.choose_selection(sample_metrics, roles)

    assert selected.assignments == {"ais": 0, "adsb": 1}
    assert selected.index_for("ais") == 0
    assert selected.index_for("adsb") == 1
    assert selected.role_deltas_db["ais"] == 10.0


def test_choose_selection_can_maximize_total_for_generic_roles():
    roles = [
        rtl_antenna_picker.RoleSpec("role-a", "100M:101M:5k"),
        rtl_antenna_picker.RoleSpec("role-b", "200M:201M:5k"),
    ]
    sample_metrics = [
        metrics(0, **{"role-a": summary(100.0), "role-b": summary(100.0)}),
        metrics(1, **{"role-a": summary(90.0), "role-b": summary(0.0)}),
    ]

    selected = rtl_antenna_picker.choose_selection(
        sample_metrics,
        roles,
        assignment_strategy="maximize-total",
    )

    assert selected.assignments == {"role-a": 1, "role-b": 0}


def test_choose_selection_uses_ais_antenna_fingerprint_from_original_deployment():
    roles = [
        rtl_antenna_picker.RoleSpec("ais", "156M:163M:5k", 6.0),
        rtl_antenna_picker.RoleSpec("adsb", "1088M:1092M:5k"),
    ]
    sample_metrics = [
        metrics(
            0,
            ais=summary(-29.8, -20.0, -22.0, 158_000_000.0),
            adsb=summary(-40.7, -35.0, -30.0, 1_090_000_000.0),
        ),
        metrics(
            1,
            ais=summary(-18.1, -6.0, -1.2, 158_830_078.0),
            adsb=summary(-43.6, -38.0, -34.0, 1_090_000_000.0),
        ),
    ]

    selected = rtl_antenna_picker.choose_selection(sample_metrics, roles)

    assert selected.assignments == {"ais": 1, "adsb": 0}
    assert round(selected.role_deltas_db["ais"], 1) == 11.7


def test_choose_selection_fails_when_required_delta_is_weak():
    roles = [rtl_antenna_picker.RoleSpec("vhf", "156M:163M:5k", 6.0)]
    sample_metrics = [
        metrics(0, vhf=summary(-25.0, -20.0, -10.0)),
        metrics(1, vhf=summary(-23.5, -18.0, -8.0)),
    ]

    with pytest.raises(ValueError, match="not decisive"):
        rtl_antenna_picker.choose_selection(sample_metrics, roles)


def test_shell_env_contains_generic_and_legacy_selected_indexes():
    roles = [
        rtl_antenna_picker.RoleSpec("ais", "156M:163M:5k"),
        rtl_antenna_picker.RoleSpec("satcom-l", "1525M:1559M:10k"),
    ]
    sample_metrics = [
        metrics(0, ais=summary(-30.0), **{"satcom-l": summary(-40.0)}),
        metrics(1, ais=summary(-18.0, peak=-1.0, peak_hz=158_830_078.0), **{"satcom-l": summary(-43.0)}),
    ]
    selected = rtl_antenna_picker.Selection(
        assignments={"ais": 1, "satcom-l": 0},
        role_deltas_db={"ais": 12.0, "satcom-l": 3.0},
        metrics=sample_metrics,
        roles=roles,
        score_stat="median",
        assignment_strategy="priority",
    )

    env = "\n".join(rtl_antenna_picker.shell_env_lines(selected, "2026-07-09T00:00:00+00:00"))

    assert "AIS_RTL_INDEX=1" in env
    assert "SATCOM_L_RTL_INDEX=0" in env
    assert "RTL_ANTENNA_PICKER_ASSIGNMENT_STRATEGY=priority" in env
    assert "RTL_DEVICE_1_AIS_PEAK_DB=-1.000" in env


def test_selection_payload_has_role_neutral_schema_and_legacy_aliases():
    roles = [rtl_antenna_picker.RoleSpec("ais", "156M:163M:5k")]
    sample_metrics = [metrics(1, ais=summary(-18.0))]
    selected = rtl_antenna_picker.Selection(
        assignments={"ais": 1},
        role_deltas_db={"ais": float("inf")},
        metrics=sample_metrics,
        roles=roles,
        score_stat="median",
        assignment_strategy="priority",
    )
    runtime = rtl_antenna_picker.RuntimeConfig(
        roles=roles,
        score_stat="median",
        assignment_strategy="priority",
        gain="33.8",
        integration="1",
        duration="3s",
    )

    payload = rtl_antenna_picker.selection_payload(selected, "2026-07-09T00:00:00+00:00", runtime)

    assert payload["selected"]["roles"]["ais"]["index"] == 1
    assert payload["selected"]["ais_index"] == 1
    assert payload["metrics"][0]["roles"]["ais"]["median_db"] == -18.0


def test_systemd_templates_are_available_and_renderable():
    root = rtl_antenna_picker.find_systemd_template_root()
    unit = root / "rtl-antenna-picker.service"

    rendered = rtl_antenna_picker.render_template(unit, {"@RTL_ANTENNA_PICKER@": "/usr/local/bin/rtl-antenna-picker"})

    assert "@RTL_ANTENNA_PICKER@" not in rendered
    assert "ExecStart=/usr/local/bin/rtl-antenna-picker" in rendered


def test_installer_dry_run_accepts_explicit_paths(tmp_path, capsys):
    rc = rtl_antenna_picker.install_piaware_ais_systemd_main(
        [
            "--dry-run",
            "--picker-bin",
            "/usr/local/bin/rtl-antenna-picker",
            "--systemd-dir",
            str(tmp_path / "systemd"),
        ]
    )

    assert rc == 0
    assert "would install" in capsys.readouterr().out


def test_build_runtime_config_accepts_generic_roles_and_strategy(tmp_path):
    config = tmp_path / "roles.json"
    config.write_text('{"score_stat":"p95","assignment_strategy":"maximize-total","roles":[{"name":"vhf","band":"156M:163M:5k","min_delta_db":5}]}')
    args = rtl_antenna_picker.build_parser().parse_args(["--config", str(config)])

    runtime = rtl_antenna_picker.build_runtime_config(args)

    assert runtime.score_stat == "p95"
    assert runtime.assignment_strategy == "maximize-total"
    assert runtime.roles == [rtl_antenna_picker.RoleSpec("vhf", "156M:163M:5k", 5.0)]


@pytest.mark.parametrize("raw", ["vhf=-1", "vhf=inf", "vhf=nan"])
def test_role_delta_rejects_negative_or_non_finite(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        rtl_antenna_picker.parse_role_delta_arg(raw)
