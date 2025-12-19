from __future__ import annotations

import threading
from typing import Optional, List, TYPE_CHECKING

import time
import numpy as np

import squid.core.logging
import squid.core.utils.hardware_utils as utils
import _def

if TYPE_CHECKING:
    from squid.core.utils.config_utils import ChannelMode
    from squid.backend.services import (
        CameraService,
        StageService,
        PeripheralService,
        NL5Service,
        IlluminationService,
    )
    from squid.core.events import EventBus


class AutofocusWorker:
    def __init__(
        self,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        nl5_service: Optional["NL5Service"],
        illumination_service: Optional["IlluminationService"],
        trigger_mode: Optional[object],
        configuration: Optional["ChannelMode"],
        keep_running: threading.Event,
        event_bus: "EventBus",
        stream_handler: Optional[object] = None,
        *,
        n_planes: int,
        delta_z_mm: float,
        crop_width: int,
        crop_height: int,
    ):
        self._camera_service: "CameraService" = camera_service
        self._stage_service: "StageService" = stage_service
        self._peripheral_service: "PeripheralService" = peripheral_service
        self._nl5_service: Optional["NL5Service"] = nl5_service
        self._illumination_service: Optional["IlluminationService"] = illumination_service
        self._trigger_mode = trigger_mode
        self._configuration = configuration
        self._keep_running: threading.Event = keep_running
        self._event_bus = event_bus
        self._stream_handler = stream_handler
        self._log = squid.core.logging.get_logger(self.__class__.__name__)

        self.N: int = n_planes
        self.deltaZ: float = delta_z_mm
        self.crop_width: int = crop_width
        self.crop_height: int = crop_height

    def run(self) -> None:
        from squid.core.events import AutofocusWorkerFinished

        try:
            self.run_autofocus()
            self._event_bus.publish(
                AutofocusWorkerFinished(
                    success=True,
                    aborted=not self._keep_running.is_set(),
                )
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self._log.exception("Autofocus worker failed")
            self._event_bus.publish(
                AutofocusWorkerFinished(
                    success=False,
                    aborted=not self._keep_running.is_set(),
                    error=message,
                )
            )
        finally:
            pass

    def wait_till_operation_is_completed(self) -> None:
        self._peripheral_service.wait_till_operation_is_completed()

    def _move_z(self, distance_mm: float) -> None:
        """Move stage Z by relative distance."""
        self._stage_service.move_z(distance_mm)

    def _send_trigger(self) -> None:
        """Send camera trigger."""
        self._camera_service.send_trigger()

    def _read_frame(self) -> Optional[np.ndarray]:
        """Read frame from camera."""
        return self._camera_service.read_frame()

    def _get_exposure_time(self) -> float:
        """Get camera exposure time."""
        return self._camera_service.get_exposure_time()

    def _send_hardware_trigger(self, control_illumination: bool, illumination_on_time_us: float) -> None:
        """Send hardware trigger."""
        self._peripheral_service.send_hardware_trigger(
            control_illumination=control_illumination,
            illumination_on_time_us=illumination_on_time_us,
        )

    def _turn_on_illumination(self) -> None:
        if self._illumination_service is None or self._configuration is None:
            return
        channel = getattr(self._configuration, "illumination_source", None)
        intensity = getattr(self._configuration, "illumination_intensity", None)
        if channel is None or intensity is None:
            return
        self._illumination_service.set_channel_power(int(channel), float(intensity))
        self._illumination_service.turn_on_channel(int(channel))

    def _turn_off_illumination(self) -> None:
        if self._illumination_service is None or self._configuration is None:
            return
        channel = getattr(self._configuration, "illumination_source", None)
        if channel is None:
            return
        self._illumination_service.turn_off_channel(int(channel))

    def run_autofocus(self) -> None:
        # @@@ to add: increase gain, decrease exposure time
        # @@@ can move the execution into a thread - done 08/21/2021
        focus_measure_vs_z: List[float] = [0] * self.N
        focus_measure_max: float = 0

        z_af_offset: float = self.deltaZ * round(self.N / 2)

        self._move_z(-z_af_offset)

        steps_moved: int = 0
        image: Optional[np.ndarray] = None
        for i in range(self.N):
            if not self._keep_running.is_set():
                self._log.warning("Signal to abort autofocus received, aborting!")
                # This aborts and then we report our best focus so far
                break
            self._move_z(self.deltaZ)
            steps_moved = steps_moved + 1
            # trigger acquisition (including turning on the illumination) and read frame
            if self._trigger_mode == _def.TriggerMode.SOFTWARE:
                self._turn_on_illumination()
                self.wait_till_operation_is_completed()
                self._send_trigger()
                image = self._read_frame()
            elif self._trigger_mode == _def.TriggerMode.HARDWARE:
                if (
                    "Fluorescence" in getattr(self._configuration, "name", "")
                    and _def.ENABLE_NL5
                    and _def.NL5_USE_DOUT
                ):
                    if self._nl5_service is None:
                        raise RuntimeError("NL5Service required for NL5-triggered autofocus")
                    self._nl5_service.start_acquisition()
                    # TODO(imo): This used to use the "reset_image_ready_flag=False" arg, but oinly the toupcam camera implementation had the
                    #  "reset_image_ready_flag" arg, so this is broken for all other cameras.
                    image = self._read_frame()
                else:
                    self._send_hardware_trigger(
                        control_illumination=True,
                        illumination_on_time_us=self._get_exposure_time() * 1000,
                    )
                    image = self._read_frame()
            if image is None:
                continue
            # tunr of the illumination if using software trigger
            if self._trigger_mode == _def.TriggerMode.SOFTWARE:
                self._turn_off_illumination()

            image = utils.crop_image(image, self.crop_width, self.crop_height)
            if self._stream_handler is not None:
                try:
                    self._stream_handler.on_new_image(image)  # type: ignore[attr-defined]
                except Exception:  # pragma: no cover - defensive
                    self._log.exception("Failed to stream autofocus debug image")

            timestamp_0 = time.time()
            focus_measure = utils.calculate_focus_measure(
                image, _def.FOCUS_MEASURE_OPERATOR
            )
            timestamp_1 = time.time()
            self._log.info(
                "             calculating focus measure took "
                + str(timestamp_1 - timestamp_0)
                + " second"
            )
            focus_measure_vs_z[i] = focus_measure
            self._log.debug(f"{i} {focus_measure}")
            focus_measure_max = max(focus_measure, focus_measure_max)
            if focus_measure < focus_measure_max * _def.AF.STOP_THRESHOLD:
                break

        # maneuver for achiving uniform step size and repeatability when using open-loop control
        self._move_z(-steps_moved * self.deltaZ)
        # determine the in-focus position
        idx_in_focus = focus_measure_vs_z.index(max(focus_measure_vs_z))
        self._move_z((idx_in_focus + 1) * self.deltaZ)

        # move to the calculated in-focus position
        if idx_in_focus == 0:
            self._log.info("moved to the bottom end of the AF range")
        if idx_in_focus == self.N - 1:
            self._log.info("moved to the top end of the AF range")
