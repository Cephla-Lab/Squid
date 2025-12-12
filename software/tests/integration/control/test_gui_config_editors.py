import os
import os.path
import tempfile
from configparser import ConfigParser

import pytest
from squid.ui.widgets.config import ConfigEditor


@pytest.mark.skipif(
    os.environ.get("SKIP_QT_TESTS") == "1",
    reason="Skipping Qt widget test in headless/unstable environment",
)
def test_config_editor_save_to_file():
    # Avoid constructing full Qt dialogs; exercise the save logic on a minimal stub.
    class DummyEditor:
        def __init__(self):
            self.config = ConfigParser()
            self._log = type("Log", (), {"exception": lambda *a, **k: None})()

        save_to_filename = ConfigEditor.save_to_filename

    editor = DummyEditor()

    (good_fd, good_filename) = tempfile.mkstemp()
    os.close(good_fd)
    assert editor.save_to_filename(good_filename)
    os.remove(good_filename)

    (bad_fd, bad_filename) = tempfile.mkstemp()
    os.close(bad_fd)
    read_only_permissions = 0o444
    os.chmod(bad_filename, read_only_permissions)

    assert not editor.save_to_filename(bad_filename)
