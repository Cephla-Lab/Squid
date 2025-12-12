import pytest

pytest.skip("Deprecated multipoint callback bridges removed", allow_module_level=True)


def _make_params() -> AcquisitionParameters:
    return AcquisitionParameters(
        experiment_ID="exp",
        base_path="/tmp",
        selected_configurations=["cfg1"],
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


class _DummyObjectiveStore:
    def get_current_objective_info(self):
        return {"magnification": 10}


class _DummyController:
    run_acquisition_current_fov = False
    selected_configurations = ["cfg1"]
    NZ = 2


def test_image_signal_bridge_emits(qtbot):
    bridge = ImageSignalBridge()
    received = []
    bridge.image_to_display.connect(received.append)

    img = np.zeros((2, 2), dtype=np.uint8)
    bridge.emit_image(img)
    qtbot.wait(50)

    assert received and received[0] is img


def test_multipoint_signal_bridge_emits_callbacks(qtbot, monkeypatch):
    # Ensure images are emitted immediately for deterministic testing
    monkeypatch.setattr(_def, "MULTIPOINT_DISPLAY_IMAGES", True)
    bridge = MultiPointSignalBridge(_DummyObjectiveStore())
    bridge.set_controller(_DummyController())
    callbacks = bridge.get_callbacks()

    tabs = []
    bridge.signal_set_display_tabs.connect(lambda configs, nz: tabs.append((configs, nz)))
    with qtbot.waitSignal(bridge.signal_acquisition_start, timeout=500, raising=True):
        callbacks.signal_acquisition_start(_make_params())
    assert tabs == [(_DummyController.selected_configurations, _DummyController.NZ)]

    # Prepare CaptureInfo for image emission
    channel_mode = ChannelMode(
        id="1",
        name="Mode1",
        exposure_time=10.0,
        analog_gain=0.0,
        illumination_source=0,
        illumination_intensity=5.0,
        camera_sn="",
        z_offset=0.0,
    )
    cap_info = CaptureInfo(
        position=Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None),
        z_index=0,
        capture_time=0.0,
        configuration=channel_mode,
        save_directory="/tmp",
        file_id="f1",
        region_id=1,
        fov=1,
        configuration_idx=0,
    )

    image_hits = []
    napari_init_hits = []
    coords_hits = []
    bridge.image_to_display.connect(lambda frame: image_hits.append(frame))
    bridge.napari_layers_init.connect(lambda h, w, dtype: napari_init_hits.append((h, w, dtype)))
    bridge.signal_coordinates.connect(lambda x, y, z, region: coords_hits.append((x, y, z, region)))

    frame = np.ones((2, 3), dtype=np.uint16)
    callbacks.signal_new_image(SimpleNamespace(frame=frame), cap_info)
    qtbot.wait(100)

    with qtbot.waitSignal(bridge.acquisition_finished, timeout=500, raising=True):
        callbacks.signal_acquisition_finished()

    assert image_hits and np.array_equal(image_hits[0], frame)
    assert napari_init_hits and napari_init_hits[0][0:2] == frame.shape[:2]
    assert coords_hits == [(cap_info.position.x_mm, cap_info.position.y_mm, cap_info.position.z_mm, cap_info.region_id)]

    # Progress callbacks
    progress_hits = []
    region_hits = []
    bridge.signal_acquisition_progress.connect(lambda region, total, tp: progress_hits.append((region, total, tp)))
    bridge.signal_region_progress.connect(lambda fov, region_fovs: region_hits.append((fov, region_fovs)))

    callbacks.signal_overall_progress(OverallProgressUpdate(current_region=1, total_regions=2, current_timepoint=3, total_timepoints=4))
    callbacks.signal_region_progress(RegionProgressUpdate(current_fov=5, region_fovs=6))
    qtbot.wait(50)

    assert progress_hits == [(1, 2, 3)]
    assert region_hits == [(5, 6)]
