# tests/control/test_worker_reason.py
from control.core.multi_point_worker import MultiPointWorker


def _make_worker():
    # Bypass __init__; we only exercise _compute_end_reason()'s pure logic.
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._run_state_fatal = False
    w._abort_cause = None
    w._acquisition_error_count = 0
    w.abort_requested_fn = lambda: False
    return w


def test_reason_completed():
    assert _make_worker()._compute_end_reason() == "completed"


def test_reason_user_abort():
    w = _make_worker()
    w.abort_requested_fn = lambda: True
    assert w._compute_end_reason() == "user_abort"


def test_reason_error_on_timeout_abort():
    w = _make_worker()
    w.abort_requested_fn = lambda: True
    w._abort_cause = "error"
    assert w._compute_end_reason() == "error"


def test_reason_error_on_fatal_exception():
    w = _make_worker()
    w._run_state_fatal = True
    assert w._compute_end_reason() == "error"


def test_reason_completed_with_errors():
    w = _make_worker()
    w._acquisition_error_count = 3
    assert w._compute_end_reason() == "completed_with_errors"
