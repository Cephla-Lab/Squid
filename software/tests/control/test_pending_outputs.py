"""MultiPointController's registry of asynchronous output writers (the finalization barrier the transfer
manifest waits on before its end record)."""

import concurrent.futures
import threading

from control.core.multi_point_controller import MultiPointController


def _controller():
    c = MultiPointController.__new__(MultiPointController)
    c._log = __import__("squid.logging").logging.get_logger("test-controller")
    c._pending_outputs = []
    c._pending_outputs_lock = threading.Lock()
    return c


def test_wait_returns_only_the_directories_of_writers_that_finished_in_time():
    c = _controller()
    done = concurrent.futures.Future()
    done.set_result(None)
    failed = concurrent.futures.Future()
    failed.set_exception(RuntimeError("disk error"))
    pending = concurrent.futures.Future()
    c.register_pending_output(done, "/exp/0/mosaic_view")
    c.register_pending_output(failed, "/exp/1/mosaic_view")
    c.register_pending_output(pending, "/exp/2/mosaic_view")

    assert c._wait_for_pending_outputs(timeout_s=0.2) == ["/exp/0/mosaic_view"]
    assert c._pending_outputs == [], "the registry is consumed by the wait"
    assert c._wait_for_pending_outputs(timeout_s=0.0) == []


def test_wait_blocks_until_a_running_writer_completes():
    c = _controller()
    future = concurrent.futures.Future()
    c.register_pending_output(future, "/exp/0/mosaic_view")
    threading.Timer(0.2, lambda: future.set_result(None)).start()
    assert c._wait_for_pending_outputs(timeout_s=5.0) == ["/exp/0/mosaic_view"]
