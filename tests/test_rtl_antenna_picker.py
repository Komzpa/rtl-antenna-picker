import argparse
import json

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


BALCONY_RTL_TEST = """Found 2 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00001090
  1:  Generic, RTL2832U, SN: 77771111153705700
"""

DEFAULT_ROLES = [
    rtl_antenna_picker.RoleSpec("ais", "156M:163M:5k"),
    rtl_antenna_picker.RoleSpec("adsb", "1088M:1092M:5k"),
]


def test_parse_rtl_test_serials():
    assert rtl_antenna_picker.parse_rtl_test_serials(BALCONY_RTL_TEST) == {0: "00001090", 1: "77771111153705700"}


def test_select_by_serial_follows_serial_not_index():
    serials = rtl_antenna_picker.parse_rtl_test_serials(BALCONY_RTL_TEST)
    pins = {"adsb": "77771111153705700", "ais": "00001090"}

    selection = rtl_antenna_picker.select_by_serial(DEFAULT_ROLES, pins, serials)
    swapped = rtl_antenna_picker.select_by_serial(DEFAULT_ROLES, pins, {0: serials[1], 1: serials[0]})

    assert selection.assignments == {"ais": 0, "adsb": 1}
    assert swapped.assignments == {"ais": 1, "adsb": 0}
    assert selection.assignment_strategy == "serial-pin"
    env = rtl_antenna_picker.shell_env_lines(selection, "2026-10-24T00:00:00+00:00")
    assert "AIS_RTL_INDEX=0" in env and "ADSB_RTL_INDEX=1" in env


@pytest.mark.parametrize(
    "pins, serials, message",
    [
        ({"adsb": "missing", "ais": "00001090"}, {0: "00001090", 1: "7777"}, "matched 0 devices"),
        ({"adsb": "dup", "ais": "00001090"}, {0: "dup", 1: "dup", 2: "00001090"}, "matched 2 devices"),
        ({"adsb": "7777"}, {0: "00001090", 1: "7777"}, "missing: ais"),
        ({"adsb": "7777", "ais": "7777"}, {0: "00001090", 1: "7777"}, "one device"),
        ({"adsb": "7777", "ais": "00001090", "vhf": "x"}, {0: "00001090", 1: "7777"}, "unknown roles"),
    ],
)
def test_select_by_serial_rejects_bad_pins(pins, serials, message):
    with pytest.raises(ValueError, match=message):
        rtl_antenna_picker.select_by_serial(DEFAULT_ROLES, pins, serials)


def test_main_with_serial_pins_skips_rf_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(rtl_antenna_picker, "run_rtl_test", lambda *_: BALCONY_RTL_TEST)
    monkeypatch.setattr(rtl_antenna_picker, "collect_metrics", lambda **_: pytest.fail("RF scan must not run"))
    env_path = tmp_path / "picker.env"

    rc = rtl_antenna_picker.main([
        "--output-env", str(env_path),
        "--state-dir", str(tmp_path / "state"),
        "--pin-serial", "adsb=77771111153705700",
        "--pin-serial", "ais=00001090",
    ])

    assert rc == 0
    env = env_path.read_text()
    assert "AIS_RTL_INDEX=0" in env and "ADSB_RTL_INDEX=1" in env




# Band medians (dB) measured 2026-10-24 on the PiAware host, both antennas
# inside a computer rack, rtl_power sweeps of both dongles at once. Device 0
# is a wideband LNA stick near clipping on FM/LTE/GSM; device 1 is flat except
# a VHF bandpass around 140-180 MHz. Nothing useful is heard at 1090 MHz.
RACK_MEDIANS = {
    0: {'156M:163M:5k@10': -24.8, '88M:108M:25k@10': -13.5, '60M:80M:25k@10': -9.8, '200M:300M:50k@10': -28.4, '1088M:1092M:5k@10': -32.1, '1000M:1050M:25k@10': -32.1, '1200M:1300M:50k@10': -32.2, '156M:163M:5k@30': -6.0, '88M:108M:25k@30': -4.0, '60M:80M:25k@30': -3.8, '200M:300M:50k@30': -10.2, '1088M:1092M:5k@30': -30.7, '1000M:1050M:25k@30': -28.7, '1200M:1300M:50k@30': -31.0, '156M:163M:5k@45': -0.5, '88M:108M:25k@45': -1.6, '60M:80M:25k@45': -0.1, '200M:300M:50k@45': 0.0, '1088M:1092M:5k@45': -23.9, '1000M:1050M:25k@45': -19.5, '1200M:1300M:50k@45': -25.9},
    1: {'156M:163M:5k@10': -23.9, '88M:108M:25k@10': -31.9, '60M:80M:25k@10': -31.9, '200M:300M:50k@10': -31.9, '1088M:1092M:5k@10': -32.1, '1000M:1050M:25k@10': -32.0, '1200M:1300M:50k@10': -32.0, '156M:163M:5k@30': -6.2, '88M:108M:25k@30': -29.8, '60M:80M:25k@30': -30.7, '200M:300M:50k@30': -31.2, '1088M:1092M:5k@30': -31.6, '1000M:1050M:25k@30': -31.7, '1200M:1300M:50k@30': -31.6, '156M:163M:5k@45': 0.4, '88M:108M:25k@45': -18.7, '60M:80M:25k@45': -19.9, '200M:300M:50k@45': -24.6, '1088M:1092M:5k@45': -31.0, '1000M:1050M:25k@45': -30.8, '1200M:1300M:50k@45': -31.2},
}
GAINS = rtl_antenna_picker.DEFAULT_VOTE_GAINS
FEATURES = rtl_antenna_picker.DEFAULT_FEATURES
ROLES = [rtl_antenna_picker.RoleSpec("ais", "156M:163M:5k", 6.0), rtl_antenna_picker.RoleSpec("adsb", "1088M:1092M:5k")]
SERIALS = {0: "00001090", 1: "77771111153705700"}


def device_metrics(medians):
    return [
        rtl_antenna_picker.DeviceMetrics(index=index, role_summaries={key: summary(value) for key, value in values.items()})
        for index, values in medians.items()
    ]


RACK = device_metrics(RACK_MEDIANS)


def test_votes_pick_filtered_ais_chain_on_rack_measurements():
    selection = rtl_antenna_picker.choose_by_votes(RACK, ROLES, FEATURES, GAINS)

    counted = [vote for vote in selection.votes if vote.device is not None]
    assert selection.assignments == {"ais": 1, "adsb": 0}
    assert len(selection.votes) == len(FEATURES) * len(GAINS)
    assert all(vote.device == 1 for vote in counted if vote.role == "ais")
    assert all(vote.device == 0 for vote in counted if vote.role == "adsb")
    assert len(counted) >= 8


def test_overloaded_stick_vote_below_margin_abstains():
    # At gain 45 the LNA stick lifts 1000-1050 MHz, so "1090 over 1000-1050"
    # leans the wrong way by ~4 dB; it must abstain, not veto.
    vote = next(
        vote
        for vote in rtl_antenna_picker.cast_votes(RACK, FEATURES, ["45"], 6.0)
        if vote.feature == "adsb: 1088M:1092M:5k - 1000M:1050M:25k"
    )

    assert vote.device is None
    assert vote.scores[1] > vote.scores[0]


def test_absolute_ais_level_alone_cannot_decide_rack():
    absolute = [
        metrics(index, ais=summary(values["156M:163M:5k@30"]), adsb=summary(values["1088M:1092M:5k@30"]))
        for index, values in RACK_MEDIANS.items()
    ]

    with pytest.raises(ValueError, match="not decisive"):
        rtl_antenna_picker.choose_selection(absolute, ROLES)


def test_contradicting_counted_votes_are_not_decisive():
    contradicting = {index: dict(values) for index, values in RACK_MEDIANS.items()}
    contradicting[0]["156M:163M:5k@45"] = 30.0  # device 0 suddenly wins one AIS feature by a mile

    with pytest.raises(ValueError, match="ais votes disagree"):
        rtl_antenna_picker.choose_by_votes(device_metrics(contradicting), ROLES, FEATURES, GAINS)

    guess = rtl_antenna_picker.choose_by_votes(device_metrics(contradicting), ROLES, FEATURES, GAINS, require_decisive=False)
    assert guess.ambiguous_roles == ("ais", "adsb")


def test_too_few_votes_are_not_decisive():
    flat = {index: {key: -30.0 for key in RACK_MEDIANS[0]} for index in (0, 1)}

    with pytest.raises(ValueError, match="only 0 of 18 votes"):
        rtl_antenna_picker.choose_by_votes(device_metrics(flat), ROLES, FEATURES, GAINS)


def test_features_from_config_default_and_custom():
    assert rtl_antenna_picker.features_from_config({}) == list(FEATURES)
    custom = rtl_antenna_picker.features_from_config({"features": [{"role": "ais", "band": "a", "reference": "b"}]})
    assert custom == [rtl_antenna_picker.Feature("ais", "a", "b")]
    with pytest.raises(ValueError):
        rtl_antenna_picker.features_from_config({"features": [{"role": "ais"}]})


def test_memory_follows_serials_across_index_reordering():
    selection = rtl_antenna_picker.choose_by_votes(RACK, ROLES, FEATURES, GAINS)
    memory = rtl_antenna_picker.remember_selection({}, selection, SERIALS, "fingerprint", "t")

    reordered = rtl_antenna_picker.select_from_memory(ROLES, memory, {0: SERIALS[1], 1: SERIALS[0]})
    replaced = rtl_antenna_picker.select_from_memory(ROLES, memory, {0: SERIALS[0], 1: "new-dongle"})

    assert memory["roles"] == {"ais": "77771111153705700", "adsb": "00001090"}
    assert reordered.assignments == {"ais": 0, "adsb": 1}
    assert replaced is None


def test_remember_skips_duplicate_serials():
    selection = rtl_antenna_picker.choose_by_votes(RACK, ROLES, FEATURES, GAINS)

    assert rtl_antenna_picker.remember_selection({}, selection, {0: "same", 1: "same"}, "x", "t") == {}


def _run_learn(tmp_path, monkeypatch, measured):
    rtl_test_output = "Found 2 device(s):\n" + BALCONY_RTL_TEST.split("\n", 1)[1]
    monkeypatch.setattr(rtl_antenna_picker, "run_rtl_test", lambda *_: rtl_test_output)
    monkeypatch.setattr(rtl_antenna_picker, "collect_vote_metrics", lambda **_: measured)
    argv = ["--output-env", str(tmp_path / "picker.env"), "--state-dir", str(tmp_path / "state"), "--learn"]
    assert rtl_antenna_picker.main(argv) == 0
    return (tmp_path / "picker.env").read_text()


def test_main_learn_remembers_decisive_and_reuses_it_when_ambiguous(tmp_path, monkeypatch):
    env = _run_learn(tmp_path, monkeypatch, RACK)
    assert "AIS_RTL_INDEX=1" in env and "ADSB_RTL_INDEX=0" in env
    assert "RTL_ANTENNA_PICKER_VOTES_TOTAL=18" in env

    flat = device_metrics({index: {key: -30.0 for key in RACK_MEDIANS[0]} for index in (0, 1)})
    env = _run_learn(tmp_path, monkeypatch, flat)
    assert "AIS_RTL_INDEX=1" in env and "ADSB_RTL_INDEX=0" in env


def test_main_learn_ambiguous_first_boot_uses_best_guess(tmp_path, monkeypatch):
    flat = device_metrics({index: {key: -30.0 for key in RACK_MEDIANS[0]} for index in (0, 1)})
    env = _run_learn(tmp_path, monkeypatch, flat)

    assert "AIS_RTL_INDEX=" in env
    assert not (tmp_path / "state" / rtl_antenna_picker.MEMORY_FILE).exists()


def _run_single(tmp_path, monkeypatch, serial, memory=None):
    rtl_test_output = f"Found 1 device(s):\n  0:  Realtek, RTL2838UHIDIR, SN: {serial}\n"
    monkeypatch.setattr(rtl_antenna_picker, "run_rtl_test", lambda *_: rtl_test_output)
    def no_scan(**_):
        raise AssertionError("a single device has nothing to compare")
    monkeypatch.setattr(rtl_antenna_picker, "collect_vote_metrics", no_scan)
    state = tmp_path / "state"
    if memory is not None:
        state.mkdir(parents=True, exist_ok=True)
        (state / rtl_antenna_picker.MEMORY_FILE).write_text(json.dumps({"roles": memory}))
    argv = ["--output-env", str(tmp_path / "picker.env"), "--state-dir", str(state), "--learn"]
    assert rtl_antenna_picker.main(argv) == 0
    return (tmp_path / "picker.env").read_text()


def test_single_device_keeps_its_remembered_role(tmp_path, monkeypatch):
    env = _run_single(tmp_path, monkeypatch, "77771111153705700", {"adsb": "00000001", "ais": "77771111153705700"})
    assert "AIS_RTL_INDEX=0" in env
    assert "ADSB_RTL_INDEX" not in env
    assert "RTL_ANTENNA_PICKER_MISSING_ROLES=adsb" in env


def test_single_unknown_device_takes_piaware_role(tmp_path, monkeypatch):
    env = _run_single(tmp_path, monkeypatch, "12345678")
    assert "ADSB_RTL_INDEX=0" in env
    assert "AIS_RTL_INDEX" not in env
    assert "RTL_ANTENNA_PICKER_ASSIGNMENT_STRATEGY=degraded" in env


def test_single_device_without_learn_still_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(rtl_antenna_picker, "detect_device_count", lambda *_: 1)
    argv = ["--output-env", str(tmp_path / "picker.env"), "--state-dir", str(tmp_path / "state")]
    with pytest.raises(RuntimeError, match="need at least 2"):
        rtl_antenna_picker.main(argv)
