"""ImagingPort over the GUI's QtMultiPointController.

The ProtocolRunner calls start() on its own thread; the actual configure+start must run on the GUI
thread (widget-adjacent state, Qt signals), so start() blocks on a BlockingQueuedConnection into
_do_start and returns a handle whose wait() the runner polls. Completion is the controller's
payload-less acquisition_finished signal plus last_end_reason/last_image_count, matched by
experiment_ID == the requested folder (a GUI-started acquisition never matches)."""

import threading
from typing import Optional

from qtpy.QtCore import QObject, Qt, QThread, Signal, Slot

import squid.logging
from control.core.acquisition_settings import acquisition_data_from_blocks, apply_acquisition_settings
from control.core.fluidics_protocol.ports import ImagingRequest, ImagingResult, ImagingStartError


_NO_RESTORE = object()  # distinguishes "nothing to restore" from a legitimate None base path


class QtImagingHandle:
    def __init__(self, controller, folder: str):
        self._controller = controller
        self.folder = folder
        self._finished = threading.Event()

    def _on_finished(self) -> None:  # GUI thread, via the port
        if self._controller.experiment_ID != self.folder:
            return
        self._finished.set()

    def wait(self, timeout: float) -> Optional[ImagingResult]:
        if not self._finished.wait(timeout):
            return None
        if self._controller.acquisition_in_progress():
            return None  # the worker thread is still unwinding; keep polling
        reason = self._controller.last_end_reason or "error"
        return ImagingResult(reason, int(self._controller.last_image_count or 0), self.folder)

    def abort(self) -> None:
        try:
            self._controller.request_abort_aquisition()
        except Exception:
            squid.logging.get_logger(__name__).exception("Failed to request acquisition abort")


class QtImagingPort(QObject):
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # nz, delta_z_um
    _start_requested = Signal(object)

    def __init__(self, controller, scan_coordinates, microscope, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._controller = controller
        self._scan_coordinates = scan_coordinates
        self._microscope = microscope
        self._current: Optional[QtImagingHandle] = None
        self._start_error: Optional[ImagingStartError] = None
        self._restore_base_path = _NO_RESTORE  # the operator's base path while a protocol acquisition runs
        self._start_requested.connect(self._do_start, Qt.BlockingQueuedConnection)
        controller.acquisition_finished.connect(self._on_acquisition_finished)

    def start(self, request: ImagingRequest) -> QtImagingHandle:
        """Runner-thread entry point; raises ImagingStartError when the acquisition cannot begin."""
        if QThread.currentThread() is self.thread():
            self._do_start(request)
        else:
            self._start_requested.emit(request)  # blocks until _do_start returns on the GUI thread
        error, self._start_error = self._start_error, None
        if error is not None:
            raise error
        return self._current

    @Slot(object)
    def _do_start(self, request: ImagingRequest) -> None:
        # Qt swallows slot exceptions under BlockingQueuedConnection: report through _start_error.
        self._start_error = None
        controller = self._controller
        # The multipoint controller is shared with the manual UI: its base path must
        # come back to whatever the operator had once the protocol acquisition ends.
        previous_base_path = controller.base_path
        try:
            if controller.acquisition_in_progress():
                raise ImagingStartError("another acquisition is already in progress")
            data = acquisition_data_from_blocks(request.settings.model_dump(), request.coordinates.model_dump())
            applied = apply_acquisition_settings(controller, self._scan_coordinates, self._microscope, data)
            controller.set_base_path(request.run_dir)
            controller.start_new_experiment(request.folder, add_timestamp=False)
            controller.protocol_info = dict(request.protocol)
            self._current = QtImagingHandle(controller, request.folder)
            self._restore_base_path = previous_base_path
            self.signal_acquisition_channels.emit(list(applied.channels))
            self.signal_acquisition_shape.emit(int(applied.nz), float(data.delta_z_um))
            controller.run_acquisition()
        except Exception as e:
            controller.set_base_path(previous_base_path)
            self._current = None
            self._restore_base_path = _NO_RESTORE
            if isinstance(e, ImagingStartError):
                self._start_error = e
            elif isinstance(e, (ValueError, FileExistsError)):
                self._start_error = ImagingStartError(str(e))
            else:
                self._log.exception("Imaging step failed to start")
                self._start_error = ImagingStartError(f"{type(e).__name__}: {e}")

    @Slot()
    def _on_acquisition_finished(self) -> None:
        handle = self._current
        if handle is not None:
            handle._on_finished()
            if handle._finished.is_set() and self._restore_base_path is not _NO_RESTORE:
                self._controller.set_base_path(self._restore_base_path)
                self._restore_base_path = _NO_RESTORE
