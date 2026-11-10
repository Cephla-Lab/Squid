"""Smart acquisition: survey folder -> ring detection -> follow-up acquisition.yaml (no hardware, no Qt)."""

import os

import cv2
import numpy as np
import pandas as pd
import pytest
import yaml

from control.acquisition_yaml_loader import parse_acquisition_yaml
from control.core.smart_acquisition import emit, step
from control.workflow_runner import LAST_TARGETS_PLACEHOLDER, SequenceItem, SequenceType, Workflow

PIXEL_SIZE_UM = 2.0
IMAGE_PX = 2000
FOV_CENTER_MM = (20.0, 30.0)
RING_DIAMETER_UM = 320.0
GRID_PITCH_UM = 1000.0


def _ring_image() -> np.ndarray:
    image = np.full((IMAGE_PX, IMAGE_PX), 200, dtype=np.uint16)
    radius = int(RING_DIAMETER_UM / PIXEL_SIZE_UM / 2)
    pitch = int(GRID_PITCH_UM / PIXEL_SIZE_UM)
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            cv2.circle(image, (IMAGE_PX // 2 + i * pitch, IMAGE_PX // 2 + j * pitch), radius, 3000, 8)
    return cv2.GaussianBlur(image, (5, 5), 0)


def _acquisition_yaml(widget_type: str) -> dict:
    data = {
        "acquisition": {"widget_type": widget_type, "xy_mode": "Select Wells"},
        "objective": {"name": "4x", "magnification": 4.0, "pixel_size_um": PIXEL_SIZE_UM, "camera_binning": [1, 1]},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": "BF LED matrix full"}],
        "autofocus": {"contrast_af": False, "laser_af": True},
    }
    if widget_type == "wellplate":
        data["wellplate_scan"] = {"scan_size_mm": 1.0, "overlap_percent": 10.0, "regions": [{"name": "C5"}]}
    else:
        data["flexible_scan"] = {"nx": 3, "ny": 3, "delta_x_mm": 0.9, "delta_y_mm": 0.9, "positions": []}
    return data


@pytest.fixture
def survey(tmp_path):
    acq = tmp_path / "survey"
    (acq / "0").mkdir(parents=True)
    (acq / "acquisition.yaml").write_text(yaml.safe_dump(_acquisition_yaml("wellplate")))
    pd.DataFrame(
        [
            {
                "region": "C5",
                "fov": 0,
                "z_level": 0,
                "x (mm)": FOV_CENTER_MM[0],
                "y (mm)": FOV_CENTER_MM[1],
                "z (um)": 3000.0,
            }
        ]
    ).to_csv(acq / "0" / "coordinates.csv", index=False)
    cv2.imwrite(str(acq / "0" / "C5_0_0_BF_LED_matrix_full.tiff"), _ring_image())
    (acq / "0" / ".done").write_text("")
    template = tmp_path / "template_flexible.yaml"
    template.write_text(yaml.safe_dump(_acquisition_yaml("flexible")))
    return str(acq), str(template), tmp_path


def _analyze(acq, template, **kwargs):
    params = {"ring_diameter_um": RING_DIAMETER_UM, "grid_pitch_um": GRID_PITCH_UM, "grid_rows": 3, "grid_cols": 3}
    return step.analyze_and_emit(acq, "ring_grid", params, template, overlay=False, **kwargs)


def test_survey_becomes_flexible_follow_up_at_ring_positions(survey):
    acq, template, _ = survey
    summary = _analyze(acq, template)

    assert summary["written"] == 9
    assert summary["widget_type"] == "flexible"
    found = sorted((round(t.x_mm, 1), round(t.y_mm, 1)) for t in summary["targets"])
    expected = sorted(
        (round(FOV_CENTER_MM[0] + i, 1), round(FOV_CENTER_MM[1] + j, 1)) for i in (-1, 0, 1) for j in (-1, 0, 1)
    )
    assert found == expected

    # The emitted file is one the Flexible Multipoint loader accepts: one FOV per target, and no
    # objective name / binning, so loading it under the follow-up objective is not a hardware mismatch.
    parsed = parse_acquisition_yaml(summary["acquisition_yaml"])
    assert parsed.widget_type == "flexible"
    assert (parsed.nx, parsed.ny) == (1, 1)
    assert len(parsed.flexible_positions) == 9
    assert parsed.objective_name is None and parsed.camera_binning is None
    assert os.path.isfile(summary["coordinates_csv"])


def test_objective_offset_shifts_targets_and_carried_z(survey):
    acq, template, _ = survey
    plain = _analyze(acq, template, carry_z=True)
    shifted = _analyze(acq, template, carry_z=True, xy_offset_mm=[0.08, -0.94], z_offset_mm=0.075)

    by_label = {t.label: t for t in plain["targets"]}
    for target in shifted["targets"]:
        before = by_label[target.label]
        assert target.x_mm == pytest.approx(before.x_mm + 0.08)
        assert target.y_mm == pytest.approx(before.y_mm - 0.94)
        assert target.z_mm == pytest.approx(3.0 + 0.075)


def test_offset_in_microns_is_refused(survey):
    acq, template, _ = survey
    with pytest.raises(emit.TargetValidationError):
        _analyze(acq, template, xy_offset_mm=[80.0, -940.0])


def test_unfinished_survey_is_refused(survey):
    acq, template, _ = survey
    os.remove(os.path.join(acq, "0", ".done"))
    with pytest.raises(Exception, match="incomplete"):
        _analyze(acq, template)


def test_two_magnification_workflow_round_trips_and_validates(survey):
    acq, template, tmp_path = survey
    workflow = Workflow(
        sequences=[
            SequenceItem(name="Objective 4x", sequence_type=SequenceType.OBJECTIVE, objective="4x"),
            SequenceItem(
                name="Survey 4x",
                sequence_type=SequenceType.ACQUISITION,
                config_path=os.path.join(acq, "acquisition.yaml"),
            ),
            SequenceItem(
                name="Find targets",
                sequence_type=SequenceType.ANALYZE,
                template_path=template,
                xy_offset_mm=[0.08, -0.94],
                z_offset_mm=0.075,
                carry_z=True,
            ),
            SequenceItem(name="Objective 20x", sequence_type=SequenceType.OBJECTIVE, objective="20x"),
            SequenceItem(
                name="Image targets 20x", sequence_type=SequenceType.ACQUISITION, config_path=LAST_TARGETS_PLACEHOLDER
            ),
        ],
        stop_on_failure=True,
    )
    assert workflow.validate() == []

    path = tmp_path / "workflow.yaml"
    workflow.save_to_file(str(path))
    reloaded = Workflow.load_from_file(str(path))
    assert [s.sequence_type for s in reloaded.sequences] == [s.sequence_type for s in workflow.sequences]
    assert reloaded.sequences[2].xy_offset_mm == [0.08, -0.94]
    assert reloaded.sequences[4].uses_last_targets()
    assert reloaded.stop_on_failure

    # {last_targets} before any Analyze step can never resolve.
    reloaded.sequences[2].included = False
    assert any("no Analyze step" in e for e in reloaded.validate())
