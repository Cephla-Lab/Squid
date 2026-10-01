"""Tests for the open/closed-loop laser AF test (control/core/laser_af_closed_loop.py).

Only the focus camera is faked: FocusPlant renders the AF spot where the sample's focus error puts it, so the
real spot fit, the real PiezoStage and the real Microcontroller (over SimSerial) all run.
"""

import csv
import math

import numpy as np
import pytest

import control._def
from control._def import CMD_SET, MCU_PINS
from control.core import laser_af_closed_loop
from control.core.laser_af_closed_loop import Sample
from control.microcontroller import Microcontroller, SimSerial
from control.models import LaserAFConfig
from control.piezo import PiezoStage

PIXEL_TO_UM = 0.2
X_REFERENCE_PX = 512.0
PIEZO_START_UM = 150.0


class RecordingSimSerial(SimSerial):
    """SimSerial that keeps every command written to it, so a test can see what reached the controller."""

    def __init__(self):
        super().__init__()
        self.commands = []

    def write(self, data, reconnect_tries=0):
        self.commands.append(bytes(data))
        return super().write(data, reconnect_tries)

    def af_laser_levels(self):
        return [c[3] for c in self.commands if c[1] == CMD_SET.SET_PIN_LEVEL and c[2] == MCU_PINS.AF_LASER]

    def piezo_writes(self):
        return [c for c in self.commands if c[1] == CMD_SET.ANALOG_WRITE_ONBOARD_DAC and c[2] == 7]


def spot_image(x_px, y_px=128, shape=(256, 1024), sigma_px=5.0):
    gy = np.exp(-((np.arange(shape[0]) - y_px) ** 2) / (2 * sigma_px**2))
    gx = np.exp(-((np.arange(shape[1]) - x_px) ** 2) / (2 * sigma_px**2))
    return np.round(255 * np.outer(gy, gx)).astype(np.uint8)


class FocusPlant:
    """Stands in for the focus camera. The focus error is the sample's offset plus how far the piezo has moved
    since the start; the spot sits error / pixel_to_um right of the reference, as on a calibrated scope."""

    def __init__(self, piezo, serial, sample_offset_um):
        self.piezo = piezo
        self.serial = serial
        self.sample_offset_um = sample_offset_um
        self.piezo_start_um = piezo.position
        self.laser_level_at_each_frame = []

    def get_frame(self):
        levels = self.serial.af_laser_levels()
        self.laser_level_at_each_frame.append(levels[-1] if levels else None)
        error_um = self.sample_offset_um + (self.piezo.position - self.piezo_start_um)
        return spot_image(X_REFERENCE_PX + error_um / PIXEL_TO_UM)


@pytest.fixture
def rig():
    serial = RecordingSimSerial()
    micro = Microcontroller(serial_device=serial, reset_and_initialize=False)
    piezo = PiezoStage(micro, {"OBJECTIVE_PIEZO_RANGE_UM": control._def.OBJECTIVE_PIEZO_RANGE_UM})
    piezo.move_to(PIEZO_START_UM)
    micro.wait_till_operation_is_completed()
    serial.commands.clear()
    yield serial, micro, piezo
    micro.close()


def config(**overrides):
    return LaserAFConfig(pixel_to_um=PIXEL_TO_UM, x_reference=X_REFERENCE_PX, has_reference=True, **overrides)


def run(rig, get_frame, closed_loop, cfg=None, duration_s=0.3, **kwargs):
    _, micro, piezo = rig
    return laser_af_closed_loop.run(
        get_frame=get_frame,
        config=cfg or config(),
        microcontroller=micro,
        piezo=piezo,
        duration_s=duration_s,
        closed_loop=closed_loop,
        gain=0.5,
        **kwargs,
    )


def test_open_loop_measures_the_offset_and_never_moves_the_piezo(rig):
    serial, _, piezo = rig
    plant = FocusPlant(piezo, serial, sample_offset_um=3.0)

    samples = run(rig, plant.get_frame, closed_loop=False)

    assert len(samples) > 5
    assert serial.piezo_writes() == []
    assert all(s.displacement_um == pytest.approx(3.0, abs=0.05) for s in samples)
    assert all(s.piezo_um == PIEZO_START_UM for s in samples)


def test_laser_stays_on_for_every_frame_and_is_off_afterwards(rig):
    serial, _, piezo = rig
    plant = FocusPlant(piezo, serial, sample_offset_um=0.0)

    run(rig, plant.get_frame, closed_loop=False)

    assert serial.af_laser_levels() == [1, 0]
    assert set(plant.laser_level_at_each_frame) == {1}


def test_laser_is_turned_off_when_a_frame_read_fails(rig):
    serial, _, _ = rig

    def unplugged_camera():
        raise RuntimeError("camera unplugged")

    with pytest.raises(RuntimeError, match="camera unplugged"):
        run(rig, unplugged_camera, closed_loop=True)

    assert serial.af_laser_levels() == [1, 0]


def test_refuses_to_run_without_a_reference_and_never_turns_the_laser_on(rig):
    serial, _, piezo = rig
    plant = FocusPlant(piezo, serial, sample_offset_um=0.0)

    with pytest.raises(ValueError, match="reference"):
        run(rig, plant.get_frame, closed_loop=False, cfg=LaserAFConfig(pixel_to_um=PIXEL_TO_UM))

    assert serial.af_laser_levels() == []


def test_a_missed_spot_is_recorded_as_nan_and_the_piezo_holds(rig):
    serial, _, _ = rig

    samples = run(rig, lambda: np.zeros((256, 1024), np.uint8), closed_loop=True)

    assert samples and all(math.isnan(s.displacement_um) for s in samples)
    assert serial.piezo_writes() == []


def test_a_spot_beyond_the_laser_af_range_is_recorded_but_not_acted_on(rig):
    serial, _, piezo = rig
    plant = FocusPlant(piezo, serial, sample_offset_um=8.0)

    samples = run(rig, plant.get_frame, closed_loop=True, cfg=config(laser_af_range=5.0))

    assert all(s.displacement_um == pytest.approx(8.0, abs=0.05) for s in samples)
    assert serial.piezo_writes() == []


def test_the_display_gets_about_ten_frames_a_second_not_every_frame(rig):
    serial, _, piezo = rig
    plant = FocusPlant(piezo, serial, sample_offset_um=0.0)
    shown = []

    samples = run(rig, plant.get_frame, closed_loop=False, duration_s=0.5, display_fn=shown.append)

    assert len(samples) > 20  # otherwise the loop was too slow for this check to mean anything
    assert 1 <= len(shown) <= 6


# The next two tests are the acceptance criteria for next_piezo_um (the control law).


def test_closed_loop_brings_the_spot_back_to_the_reference(rig):
    serial, _, piezo = rig
    plant = FocusPlant(piezo, serial, sample_offset_um=3.0)

    samples = run(rig, plant.get_frame, closed_loop=True)

    assert abs(samples[-1].displacement_um) < 0.05
    assert piezo.position == pytest.approx(PIEZO_START_UM - 3.0, abs=0.05)
    assert serial.piezo_writes()


def test_closed_loop_never_asks_the_piezo_to_leave_its_range(rig):
    serial, micro, piezo = rig
    piezo.move_to(1.0)
    micro.wait_till_operation_is_completed()
    plant = FocusPlant(piezo, serial, sample_offset_um=3.0)  # a full correction would need the piezo at -2 um

    samples = run(rig, plant.get_frame, closed_loop=True)

    assert all(0.0 <= s.piezo_um <= control._def.OBJECTIVE_PIEZO_RANGE_UM for s in samples)
    assert 0.0 <= piezo.position <= control._def.OBJECTIVE_PIEZO_RANGE_UM


def _sample(t_s, displacement_um, dac_ms=float("nan")):
    missed = math.isnan(displacement_um)
    return Sample(
        t_s=t_s,
        displacement_um=displacement_um,
        piezo_um=PIEZO_START_UM,
        spot_x_px=float("nan") if missed else 517.0,
        spot_y_px=float("nan") if missed else 128.0,
        frame_ms=2.0,
        fit_ms=1.0,
        dac_ms=dac_ms,
    )


def test_summary_reports_loop_rate_rms_displacement_and_missed_spots():
    samples = [_sample(0.00, 1.0, 0.5), _sample(0.01, -1.0, 0.7), _sample(0.02, 1.0, 0.6), _sample(0.03, float("nan"))]

    summary = laser_af_closed_loop.summarize(samples)

    assert summary.rate_hz == pytest.approx(100.0)
    assert summary.rms_displacement_um == pytest.approx(1.0)
    assert summary.missed == 1
    assert summary.median_dac_ms == pytest.approx(0.6)


def test_save_writes_a_csv_and_a_png_in_a_folder_named_for_the_mode(tmp_path):
    samples = [_sample(0.00, 1.0, 0.5), _sample(0.01, float("nan"))]

    folder = laser_af_closed_loop.save(samples, tmp_path, objective="20x", closed_loop=True, gain=0.5)

    assert folder.parent == tmp_path / "laser_af_closed_loop"
    assert folder.name.startswith("20x_closed_loop_")
    lines = (folder / "z_vs_t.csv").read_text().splitlines()
    assert lines[0].startswith("#") and "closed_loop" in lines[0] and "gain=0.5" in lines[0]
    rows = list(csv.DictReader(lines[1:]))
    assert len(rows) == 2
    assert float(rows[0]["displacement_um"]) == 1.0
    assert math.isnan(float(rows[1]["displacement_um"]))
    assert (folder / "z_vs_t.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_save_labels_the_gain_as_the_operator_set_it(tmp_path):
    # the GUI's spinbox steps 0.5 down by 0.05 to 0.20000000000000007
    folder = laser_af_closed_loop.save(
        [_sample(0.0, 1.0, 0.5)], tmp_path, objective="20x", closed_loop=True, gain=0.20000000000000007
    )

    assert "gain=0.2," in (folder / "z_vs_t.csv").read_text().splitlines()[0]
