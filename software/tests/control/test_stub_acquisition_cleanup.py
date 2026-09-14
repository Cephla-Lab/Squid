"""The stub multipoint controllers must not litter /tmp with acquisition data.

Simulated acquisitions write real image data (~180 MB per run). The stub
factories used to point them at a literal "/tmp/", where the output accumulated
across runs until the disk filled. They now write under a registered temp dir
that the suite deletes at test teardown (see the autouse fixture in
tests/conftest.py).
"""

import os
import tempfile
from unittest.mock import patch

import pytest

import control.microscope
import tests.control.test_stubs as ts


def _new_closed_stub_acquisition_dir() -> str:
    """Build a stub controller, then close it and its microscope in the suite's
    teardown order so no writer is alive when the output dir is deleted."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    base_path = mpc.base_path
    mpc.close()
    scope.close()
    return base_path


def test_stub_controller_writes_under_a_registered_temp_dir():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    assert mpc.base_path != "/tmp/"
    assert mpc.base_path.startswith(tempfile.gettempdir())
    assert os.path.isdir(mpc.base_path)
    assert mpc.base_path in ts._acquisition_output_dirs


def test_cleanup_removes_registered_dirs():
    base_path = _new_closed_stub_acquisition_dir()

    ts.cleanup_stub_acquisition_dirs()

    assert not os.path.exists(base_path)
    assert base_path not in ts._acquisition_output_dirs


def test_cleanup_keeps_undeletable_dir_registered_and_warns():
    base_path = _new_closed_stub_acquisition_dir()

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
