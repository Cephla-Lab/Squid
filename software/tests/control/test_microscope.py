import control.microscope
import squid.stage.cephla
import squid.config
from control.microcontroller import Microcontroller, SimSerial
from tests.control.test_microcontroller import get_test_micro


def test_create_simulated_microscope():
    microcontroller = get_test_micro()
    stage = squid.stage.cephla.CephlaStage(microcontroller, squid.config.get_stage_config())
    sim_scope = control.microscope.Microscope(stage=stage, is_simulation=True)
