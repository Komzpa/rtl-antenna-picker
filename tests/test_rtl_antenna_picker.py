import rtl_antenna_picker


def test_parse_rtl_test_device_count():
    output = "Found 2 device(s):\n  0: Realtek\n  1: Generic\n"
    assert rtl_antenna_picker.parse_rtl_test_device_count(output) == 2


def test_parse_rtl_power_csv_summary(tmp_path):
    csv_path = tmp_path / "scan.csv"
    csv_path.write_text(
        "2026-07-09,00:00:00,156000000,156010000,5000,1,-40.0,-30.0,-20.0\n"
    )
    summary = rtl_antenna_picker.parse_rtl_power_csv(csv_path)
    assert summary.sample_count == 3
    assert summary.median_db == -30.0
    assert summary.peak_db == -20.0
    assert summary.peak_hz == 156012500.0


def test_choose_selection_uses_ais_antenna_fingerprint():
    metrics = [
        rtl_antenna_picker.DeviceMetrics(
            index=0,
            ais=rtl_antenna_picker.BandSummary(-29.8, -20.0, -22.0, 158_000_000.0, 10),
            adsb=rtl_antenna_picker.BandSummary(-40.7, -35.0, -30.0, 1_090_000_000.0, 10),
        ),
        rtl_antenna_picker.DeviceMetrics(
            index=1,
            ais=rtl_antenna_picker.BandSummary(-18.1, -6.0, -1.2, 158_830_078.0, 10),
            adsb=rtl_antenna_picker.BandSummary(-43.6, -38.0, -34.0, 1_090_000_000.0, 10),
        ),
    ]
    selection = rtl_antenna_picker.choose_selection(metrics, min_ais_median_delta_db=6.0)
    assert selection.ais_index == 1
    assert selection.adsb_index == 0
    assert round(selection.ais_median_delta_db, 1) == 11.7


def test_choose_selection_fails_when_ais_delta_is_weak():
    metrics = [
        rtl_antenna_picker.DeviceMetrics(
            index=0,
            ais=rtl_antenna_picker.BandSummary(-25.0, -20.0, -10.0, None, 10),
            adsb=rtl_antenna_picker.BandSummary(-40.0, -35.0, -30.0, None, 10),
        ),
        rtl_antenna_picker.DeviceMetrics(
            index=1,
            ais=rtl_antenna_picker.BandSummary(-23.5, -18.0, -8.0, None, 10),
            adsb=rtl_antenna_picker.BandSummary(-42.0, -36.0, -32.0, None, 10),
        ),
    ]
    try:
        rtl_antenna_picker.choose_selection(metrics, min_ais_median_delta_db=6.0)
    except ValueError as exc:
        assert "not decisive" in str(exc)
    else:
        raise AssertionError("weak AIS delta should fail")


def test_shell_env_contains_selected_indexes():
    metrics = [
        rtl_antenna_picker.DeviceMetrics(
            index=0,
            ais=rtl_antenna_picker.BandSummary(-30.0, -20.0, -10.0, 158_000_000.0, 10),
            adsb=rtl_antenna_picker.BandSummary(-40.0, -35.0, -30.0, 1_090_000_000.0, 10),
        ),
        rtl_antenna_picker.DeviceMetrics(
            index=1,
            ais=rtl_antenna_picker.BandSummary(-18.0, -6.0, -1.0, 158_830_078.0, 10),
            adsb=rtl_antenna_picker.BandSummary(-43.0, -38.0, -34.0, 1_090_000_000.0, 10),
        ),
    ]
    selection = rtl_antenna_picker.Selection(1, 0, 12.0, 9.0, metrics)
    env = "\n".join(rtl_antenna_picker.shell_env_lines(selection, "2026-07-09T00:00:00+00:00"))
    assert "AIS_RTL_INDEX=1" in env
    assert "ADSB_RTL_INDEX=0" in env
    assert "RTL_DEVICE_1_AIS_PEAK_DB=-1.000" in env
