"""Unit tests for ImagingExecutor."""

from unittest.mock import MagicMock

from squid.backend.controllers.orchestrator.imaging_executor import ImagingExecutor
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
