# tests/acquisition_watchdog/test_cli.py
from unittest.mock import patch

from acquisition_watchdog.__main__ import main


def test_once_runs_single_check(tmp_path):
    with patch("acquisition_watchdog.monitor.Monitor.check_once") as check, patch(
        "acquisition_watchdog.monitor.Monitor.run_forever"
    ) as forever:
        main(["--once", "--state-dir", str(tmp_path)])
    check.assert_called_once()
    forever.assert_not_called()


def test_default_runs_forever(tmp_path):
    with patch("acquisition_watchdog.monitor.Monitor.run_forever") as forever:
        main(["--state-dir", str(tmp_path)])
    forever.assert_called_once()
