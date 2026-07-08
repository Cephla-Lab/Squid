# tests/acquisition_watchdog/test_alerts.py
from acquisition_watchdog import alerts


def _run():
    return {
        "experiment_id": "plateA_2026",
        "machine": "micro-1",
        "output_path": "/data/plateA_2026",
        "progress": {"timepoint": 3, "expected_timepoints": 10, "images": 360},
        "expected": {"timepoints": 10},
        "started_at": 1_700_000_000.0,
        "heartbeat_at": 1_700_000_100.0,
    }


def test_format_alert_includes_key_facts():
    text, blocks = alerts.format_alert("crash", _run())
    assert "plateA_2026" in text
    assert "micro-1" in text
    blob = str(blocks)
    assert "plateA_2026" in blob
    assert "3" in blob and "10" in blob  # progress vs expected
    assert isinstance(blocks, list) and blocks


def test_format_alert_each_kind_has_title():
    for kind in ("crash", "hang", "error", "completed_with_errors", "user_abort"):
        text, _ = alerts.format_alert(kind, _run())
        assert text  # non-empty title line for every kind
