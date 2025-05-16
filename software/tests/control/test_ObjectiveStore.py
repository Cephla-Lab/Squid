import tests.control.gui_test_stubs as gts
import pytest
import control._def


def test_objective_store():
    objective_store = gts.get_test_objective_store()
    objective1 = {
        "name": "10x",
        "magnification": 10,
        "NA": 0.3,
        "tube_lens_f_mm": 180,
    }
    objective2 = {
        "name": "60x",
        "magnification": 60,
        "NA": 1.2,
        "tube_lens_f_mm": 180,
    }
    assert objective_store.calculate_pixel_size_factor(objective1, control._def.TUBE_LENS_MM) == float(
        18 / control._def.TUBE_LENS_MM
    )
    assert objective_store.calculate_pixel_size_factor(objective2, control._def.TUBE_LENS_MM) == float(
        3 / control._def.TUBE_LENS_MM
    )
