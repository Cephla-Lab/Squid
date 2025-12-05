import os
import tempfile
from configparser import ConfigParser

import pytest

import tests.control.gui_test_stubs
import control.widgets


def test_config_editor_save_to_file(qtbot):
    config_editor = control.widgets.ConfigEditor(ConfigParser())

    (good_fd, good_filename) = tempfile.mkstemp()
    os.close(good_fd)
    assert config_editor.save_to_filename(good_filename)
    os.remove(good_filename)

    (bad_fd, bad_filename) = tempfile.mkstemp()
    os.close(bad_fd)
    read_only_permissions = 0o444
    os.chmod(bad_filename, read_only_permissions)

    assert not config_editor.save_to_filename(bad_filename)
