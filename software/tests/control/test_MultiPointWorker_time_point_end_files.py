"""The files that close a time point folder: <time point>/coordinates.csv and <time point>/.done.

run_single_time_point writes both on every way out of the time point, also when an exception (a stage reply
error, a timeout) ends it part way: coordinates.csv then holds the rows of the FOVs imaged so far, and .done says
nothing more is written in the folder (an external copier waits for it). The exception still goes on to run().

A bare MultiPointWorker (no __init__) runs a stand-in for run_coordinate_acquisition that records some FOVs and
then raises, or returns.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

import squid.abc
import squid.exceptions
from control.core import multi_point_worker
from control.core.multi_point_worker import MultiPointWorker


def _worker(tmp_path, fovs_done=3, error=None):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = MagicMock()
    w.microcontroller = MagicMock()
    w._timing = MagicMock()
    w._slack_notifier = None
    w.callbacks = MagicMock()
    w.experiment_path = str(tmp_path / "exp")
    w.time_point = 0
    w.Nt = 1
    w.use_piezo = False
    w.initialize_z_stack = MagicMock()

    def run_coordinate_acquisition(current_path):
        for fov in range(fovs_done):
            pos = squid.abc.Pos(x_mm=1.0 + fov, y_mm=2.0, z_mm=0.5, theta_rad=None)
            w.update_coordinates_dataframe("A1", 0, pos, fov)
        if error is not None:
            raise error

    w.run_coordinate_acquisition = run_coordinate_acquisition
    return w


def _time_point_dir(tmp_path):
    return tmp_path / "exp" / f"{0:0{multi_point_worker.FILE_ID_PADDING}}"


def _fovs_in_csv(tmp_path):
    return list(pd.read_csv(_time_point_dir(tmp_path) / "coordinates.csv")["fov"])


@pytest.mark.parametrize(
    "error",
    [ValueError("unreadable position reply"), squid.exceptions.SquidTimeout("stage stopped answering")],
    ids=["stage_reply_error", "timeout"],
)
def test_an_error_part_way_writes_the_rows_so_far_and_done_then_raises(tmp_path, error):
    w = _worker(tmp_path, fovs_done=3, error=error)

    with pytest.raises(type(error)) as raised:
        w.run_single_time_point()

    assert raised.value is error
    assert _fovs_in_csv(tmp_path) == [0, 1, 2]
    assert (_time_point_dir(tmp_path) / ".done").exists()
    # The time point did not finish: its success notification is not sent.
    w.callbacks.signal_timepoint_finished.assert_not_called()
    w.microcontroller.enable_joystick.assert_called_with(True)


def test_an_error_before_the_first_fov_writes_an_empty_csv_and_done(tmp_path):
    w = _worker(tmp_path)
    error = squid.exceptions.SquidTimeout("z move to the stack start timed out")
    w.initialize_z_stack.side_effect = error

    with pytest.raises(squid.exceptions.SquidTimeout) as raised:
        w.run_single_time_point()

    assert raised.value is error
    assert _fovs_in_csv(tmp_path) == []
    assert (_time_point_dir(tmp_path) / ".done").exists()


def test_a_failed_csv_write_after_an_error_is_logged_and_the_error_goes_on(tmp_path, monkeypatch):
    error = ValueError("unreadable position reply")
    w = _worker(tmp_path, error=error)

    def to_csv(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", to_csv)

    with pytest.raises(ValueError) as raised:
        w.run_single_time_point()

    assert raised.value is error
    assert not (_time_point_dir(tmp_path) / "coordinates.csv").exists()
    assert (_time_point_dir(tmp_path) / ".done").exists()
    assert w._log.exception.call_count == 1


def test_a_failed_done_write_after_an_error_is_logged_and_the_error_goes_on(tmp_path, monkeypatch):
    error = ValueError("unreadable position reply")
    w = _worker(tmp_path, error=error)

    def create_done_file(path):
        raise OSError("disk full")

    monkeypatch.setattr(multi_point_worker.utils, "create_done_file", create_done_file)

    with pytest.raises(ValueError) as raised:
        w.run_single_time_point()

    assert raised.value is error
    assert _fovs_in_csv(tmp_path) == [0, 1, 2]
    assert not (_time_point_dir(tmp_path) / ".done").exists()
    assert w._log.exception.call_count == 1


def test_a_saved_csv_is_not_written_again_when_the_done_file_fails(tmp_path, monkeypatch):
    """The normal end saved coordinates.csv, then creating .done failed. The error path must not write the CSV
    again (to_csv truncates first, so a failed rewrite would destroy the good file); it still tries .done again."""
    w = _worker(tmp_path, fovs_done=2)
    real_to_csv = pd.DataFrame.to_csv
    csv_writes = []

    def to_csv(self, *args, **kwargs):
        csv_writes.append(args)
        if len(csv_writes) > 1:
            raise OSError("the disk went away")
        return real_to_csv(self, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", to_csv)
    error = OSError("cannot create .done")
    done_attempts = []

    def create_done_file(path):
        done_attempts.append(path)
        raise error

    monkeypatch.setattr(multi_point_worker.utils, "create_done_file", create_done_file)

    with pytest.raises(OSError) as raised:
        w.run_single_time_point()

    assert raised.value is error
    assert len(csv_writes) == 1
    assert _fovs_in_csv(tmp_path) == [0, 1]
    assert len(done_attempts) == 2


def test_the_normal_path_writes_both_files_around_its_notifications(tmp_path):
    w = _worker(tmp_path, fovs_done=4)
    w._slack_notifier = MagicMock()
    w._slack_notifier.estimate_remaining_time.return_value = 0.0
    w.timestamp_acquisition_started = 0.0
    folder = _time_point_dir(tmp_path)
    seen = {}

    def files_now(name):
        def record(*args):
            seen[name] = ((folder / "coordinates.csv").exists(), (folder / ".done").exists())

        return record

    w.callbacks.signal_slack_timepoint_notification.side_effect = files_now("slack")
    w.callbacks.signal_timepoint_finished.side_effect = files_now("finished")

    w.run_single_time_point()

    # coordinates.csv before the notifications, .done after them: as before.
    assert seen == {"slack": (True, False), "finished": (True, False)}
    assert _fovs_in_csv(tmp_path) == [0, 1, 2, 3]
    assert (folder / ".done").exists()
    w.callbacks.signal_timepoint_finished.assert_called_once_with(0)
    w._log.exception.assert_not_called()
