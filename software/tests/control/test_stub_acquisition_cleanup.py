"""Stub multipoint controllers write under a registered temp dir that the suite
deletes at teardown, after every writer is closed (see tests/conftest.py)."""

import os
import pathlib
from unittest.mock import patch

import pytest

import control.microscope
import tests.control.test_stubs as ts


def _registered_dir_with_output() -> str:
    """Register a dir the way the stub factories do, holding a nested file like
    the one start_new_experiment() leaves behind."""
    base_path = ts.new_acquisition_base_path()
    experiment_dir = pathlib.Path(base_path, "unit_test_experiment")
    experiment_dir.mkdir()
    (experiment_dir / "acquisition parameters.json").touch()
    return base_path


def test_stub_controller_writes_under_a_registered_temp_dir():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    assert mpc.base_path != "/tmp/"
    assert os.path.isdir(mpc.base_path)
    assert mpc.base_path in ts._acquisition_output_dirs


def test_cleanup_removes_registered_dirs():
    base_path = _registered_dir_with_output()

    ts.cleanup_stub_acquisition_dirs()

    assert not os.path.exists(base_path)
    assert base_path not in ts._acquisition_output_dirs


def test_cleanup_keeps_undeletable_dir_registered_and_warns():
    base_path = _registered_dir_with_output()

    with patch.object(ts.shutil, "rmtree", side_effect=PermissionError("simulated open handle")):
        with pytest.warns(RuntimeWarning, match="kept registered"):
            ts.cleanup_stub_acquisition_dirs()

    # Deletion failed, so the dir must still be on disk *and* still registered.
    assert os.path.isdir(base_path)
    assert base_path in ts._acquisition_output_dirs

    # The next teardown retries and succeeds once the dir is deletable again.
    ts.cleanup_stub_acquisition_dirs()
    assert not os.path.exists(base_path)
    assert base_path not in ts._acquisition_output_dirs
