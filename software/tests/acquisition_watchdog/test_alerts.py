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


def _held_manifest(held_at):
    return {
        "run_name": "merfish20",
        "run_dir": "/data/runs/merfish20_2026-11-22_00-01-02",
        "status": "held",
        "hold_reason": "error",
        "hold_message": "ValueError: not enough values to unpack",
        "held_at": held_at,
        "cursor": {"step": 39, "attempt": 1},
        "steps": [{"kind": "fluidics", "label": f"R{i // 2 + 1:02d}"} for i in range(39)]
        + [{"kind": "imaging", "label": "R20"}],
        "heartbeat_at": held_at + 27_000,
    }


def test_format_protocol_alert_says_how_long_and_why_the_run_is_held():
    held_at = 1_700_000_000.0
    text, blocks = alerts.format_protocol_alert(
        "protocol_held", _held_manifest(held_at), {"machine": "micro-1"}, held_at + 7 * 3600 + 32 * 60
    )
    assert "HELD" in text and "merfish20" in text and "micro-1" in text
    blob = str(blocks)
    assert "7 h 32 min" in blob
    assert "error: ValueError: not enough values to unpack" in blob
    assert "40/40 (imaging, R20)" in blob
    assert "merfish20_2026-11-22_00-01-02" in blob


def test_format_protocol_alert_each_kind_has_title():
    for kind in ("protocol_held", "protocol_held_reminder", "protocol_stalled"):
        text, blocks = alerts.format_protocol_alert(kind, _held_manifest(1_700_000_000.0), {}, 1_700_003_000.0)
        assert text and blocks
    _text, blocks = alerts.format_protocol_alert("protocol_stalled", _held_manifest(1.0), {}, 2.0)
    assert "Last heartbeat" in str(blocks)
