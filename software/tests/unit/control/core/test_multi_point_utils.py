import pytest

pytest.skip("Deprecated callback-based multipoint utilities removed", allow_module_level=True)


def _make_params() -> AcquisitionParameters:
    return AcquisitionParameters(
        experiment_ID="exp-1",
        base_path="/tmp",
        selected_configurations=[],
        acquisition_start_time=0.0,
        scan_position_information=ScanPositionInformation(
            scan_region_coords_mm=[],
            scan_region_names=[],
            scan_region_fov_coords_mm={},
        ),
        NX=1,
        deltaX=0.0,
        NY=1,
        deltaY=0.0,
        NZ=1,
        deltaZ=0.0,
        Nt=1,
        deltat=0.0,
        do_autofocus=False,
        do_reflection_autofocus=False,
        use_piezo=False,
        display_resolution_scaling=1.0,
        z_stacking_config="",
        z_range=(0.0, 0.0),
        use_fluidics=False,
    )


def test_create_eventbus_callbacks_publish_expected_events():
    bus = EventBus()
    captured = []

    bus.subscribe(AcquisitionStarted, captured.append)
    bus.subscribe(AcquisitionFinished, captured.append)
    bus.subscribe(AcquisitionProgress, captured.append)
    bus.subscribe(AcquisitionRegionProgress, captured.append)
    bus.subscribe(MicroscopeModeChanged, captured.append)

    callbacks = create_eventbus_callbacks(bus)
    params = _make_params()
    channel_mode = ChannelMode(
        id="mode-1",
        name="Test Mode",
        exposure_time=25.0,
        analog_gain=2.0,
        illumination_source=0,
        illumination_intensity=15.0,
        camera_sn="",
        z_offset=0.0,
    )

    callbacks.signal_acquisition_start(params)
    callbacks.signal_overall_progress(
        OverallProgressUpdate(
            current_region=1, total_regions=2, current_timepoint=1, total_timepoints=3
        )
    )
    callbacks.signal_region_progress(RegionProgressUpdate(current_fov=1, region_fovs=4))
    callbacks.signal_acquisition_finished()
    callbacks.signal_current_configuration(channel_mode)
    bus.drain()

    types = [type(evt) for evt in captured]
    assert AcquisitionStarted in types
    assert AcquisitionFinished in types
    assert AcquisitionProgress in types
    assert AcquisitionRegionProgress in types
    assert MicroscopeModeChanged in types

    started_event = next(evt for evt in captured if isinstance(evt, AcquisitionStarted))
    assert isinstance(started_event.timestamp, float)
    progress_event = next(evt for evt in captured if isinstance(evt, AcquisitionProgress))
    assert 0.0 <= progress_event.progress_percent <= 100.0
    mode_event = next(evt for evt in captured if isinstance(evt, MicroscopeModeChanged))
    assert mode_event.configuration_name == channel_mode.name
    assert mode_event.exposure_time_ms == channel_mode.exposure_time
    assert mode_event.analog_gain == channel_mode.analog_gain
    assert mode_event.illumination_intensity == channel_mode.illumination_intensity
