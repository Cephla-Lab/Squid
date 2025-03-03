"""
This file contains various tools meant for writing tests.  It shouldn't contain code used outside the tests/
directory structure.
"""

import os
import pathlib

import git
import matplotlib

import control.camera
import control.microcontroller
import squid.stage
import squid.stage.cephla
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from squid.config import get_stage_config
import control._def


class NonInteractiveMatplotlib:
    def __init__(self):
        self._existing_backend = None

    def __enter__(self):
        self._existing_backend = matplotlib.get_backend()
        matplotlib.use("Agg")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._existing_backend:
            matplotlib.use(self._existing_backend)


def get_test_microcontroller() -> control.microcontroller.Microcontroller:
    return control.microcontroller.Microcontroller(control.microcontroller.SimSerial(), True)


def get_test_camera():
    return control.camera.Camera_Simulation()


def get_test_stage(microcontroller):
    return squid.stage.cephla.CephlaStage(microcontroller=microcontroller, stage_config=get_stage_config())


def get_repo_root() -> pathlib.Path:
    git_repo = git.Repo(os.getcwd(), search_parent_directories=True)
    git_root = git_repo.git.rev_parse("--show-toplevel")

    return pathlib.Path(git_root).absolute()


def get_test_piezo_stage(microcontroller: Microcontroller):
    test_piezo_config = {
        "OBJECTIVE_PIEZO_HOME_UM": control._def.OBJECTIVE_PIEZO_HOME_UM,
        "OBJECTIVE_PIEZO_RANGE_UM": control._def.OBJECTIVE_PIEZO_RANGE_UM,
        "OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE": control._def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE,
        "OBJECTIVE_PIEZO_FLIP_DIR": control._def.OBJECTIVE_PIEZO_FLIP_DIR,
    }

    return PiezoStage(microcontroller=microcontroller, config=test_piezo_config)
