"""Unit tests for ImagingExecutor."""

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock

from squid.backend.controllers.orchestrator.imaging_executor import (
    ImagingExecutor,
    _FocusLockRunMonitor,
)
from squid.backend.controllers.orchestrator.state import AddWarningCommand
from squid.core.events import (
    EventBus,
    FocusLockMetricsUpdated,
    FocusLockPiezoLimitCritical,
    FocusLockStatusChanged,
    FocusLockWarning,
)
from squid.core.protocol import ImagingProtocol


def test_execute_with_config_resets_start_fov_index():
    event_bus = MagicMock()
    multipoint = MagicMock()
    multipoint.update_config = MagicMock()
    multipoint.run_acquisition = MagicMock(return_value=False)
    multipoint.set_start_fov_index = MagicMock()
    multipoint.set_current_round_index = MagicMock()
    multipoint._autofocus_executor = None

    executor = ImagingExecutor(
        event_bus=event_bus,
        multipoint_controller=multipoint,
        scan_coordinates=None,
        channel_config_manager=None,
    )

    protocol = ImagingProtocol(channels=["DAPI"])

    executor.execute_with_config(
        imaging_config=protocol,
        output_path="/tmp",
        cancel_token=MagicMock(),
        round_index=0,
        resume_fov_index=3,
        experiment_id="round_000",
    )

    # Resume index should be set for this run and reset afterwards.
    assert multipoint.set_start_fov_index.call_args_list[0][0][0] == 3
    assert multipoint.set_start_fov_index.call_args_list[-1][0][0] == 0


def test_focus_lock_run_monitor_writes_qc_artifacts_and_warning_commands(tmp_path):
    event_bus = EventBus()
    event_bus.start()
    warnings = []
    event_bus.subscribe(AddWarningCommand, warnings.append)

    monitor = _FocusLockRunMonitor(
        event_bus=event_bus,
        output_dir=tmp_path,
        round_index=0,
        round_name="Round 1",
    )
    monitor.start()
    monitor.update_progress(fov_index=2, total_fovs=8)

    try:
        event_bus.publish(
            FocusLockStatusChanged(
                is_locked=True,
                status="locked",
                lock_buffer_fill=4,
                lock_buffer_length=4,
            )
        )
        event_bus.publish(
            FocusLockMetricsUpdated(
                z_error_um=0.05,
                z_position_um=150.0,
                spot_snr=9.5,
                spot_intensity=120.0,
                z_error_rms_um=0.08,
                drift_rate_um_per_s=0.02,
                is_good_reading=True,
                correlation=0.93,
                spot_offset_px=1.0,
                piezo_delta_um=0.3,
                lock_buffer_fill=4,
                lock_buffer_length=4,
                lock_quality=0.95,
            )
        )
        event_bus.publish(FocusLockWarning(warning_type="snr_low", message="Spot SNR below threshold"))
        event_bus.publish(
            FocusLockStatusChanged(
                is_locked=False,
                status="searching",
                lock_buffer_fill=0,
                lock_buffer_length=4,
            )
        )
        event_bus.publish(
            FocusLockPiezoLimitCritical(
                direction="high",
                position_um=295.0,
                limit_um=300.0,
                margin_um=10.0,
            )
        )
        event_bus.drain()
    finally:
        monitor.stop()
        event_bus.stop()

    summary_path = Path(tmp_path) / "focus_lock_summary.json"
    timeseries_path = Path(tmp_path) / "focus_lock_timeseries.csv"

    assert summary_path.exists()
    assert timeseries_path.exists()

    summary = json.loads(summary_path.read_text())
    assert summary["status_transition_counts"]["searching"] >= 1
    assert summary["warning_counts"]["snr_low"] >= 1
    assert summary["max_abs_z_error_um"] == 0.05

    with timeseries_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert any(row["event_type"] == "warning" for row in rows)
    assert any(row["event_type"] == "status" and row["status"] == "searching" for row in rows)
    assert any(cmd.category == "FOCUS" and cmd.fov_index == 2 for cmd in warnings)
