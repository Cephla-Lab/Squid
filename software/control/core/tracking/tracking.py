import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List, Any
from io import TextIOWrapper

import cv2
import numpy as np
from qtpy.QtCore import QObject, QThread, Signal
from qtpy.QtWidgets import QApplication

from control._def import Acquisition
from control.core.display.stream_handler import ImageSaver_Tracking
from control.utils_config import ChannelMode
import control.core.tracking.tracking_dasiamrpn as tracking
import control.utils as utils
import squid.logging
from squid.abc import AbstractCamera, AbstractStage
from squid.events import (
    SetTrackingParametersCommand,
    SetTrackingPathCommand,
    SetTrackingChannelsCommand,
    StartTrackingExperimentCommand,
    StartTrackingCommand,
    StopTrackingCommand,
    TrackingStateChanged,
)

if TYPE_CHECKING:
    from control.core.display import ImageDisplayWindow
    from control.core.display import LiveController
    from control.microcontroller import Microcontroller
    from squid.events import EventBus


class TrackingController(QObject):
    signal_tracking_stopped = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(ChannelMode)

    def __init__(
        self,
        camera: AbstractCamera,
        microcontroller: "Microcontroller",
        stage: AbstractStage,
        objectiveStore: Any,
        channelConfigurationManager: Any,
        liveController: "LiveController",
        autofocusController: Any,
        imageDisplayWindow: "ImageDisplayWindow",
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        self.camera: AbstractCamera = camera
        self.microcontroller: "Microcontroller" = microcontroller
        self.stage: AbstractStage = stage
        self.objectiveStore: Any = objectiveStore
        self.channelConfigurationManager: Any = channelConfigurationManager
        self.liveController: "LiveController" = liveController
        self.autofocusController: Any = autofocusController
        self.imageDisplayWindow: "ImageDisplayWindow" = imageDisplayWindow
        self.tracker: tracking.Tracker_Image = tracking.Tracker_Image()

        self.tracking_time_interval_s: float = 0

        self.display_resolution_scaling: float = (
            Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
        )
        self.counter: int = 0
        self.experiment_ID: Optional[str] = None
        self.base_path: Optional[str] = None
        self.selected_configurations: List[ChannelMode] = []

        self.flag_stage_tracking_enabled: bool = True
        self.flag_AF_enabled: bool = False
        self.flag_save_image: bool = False
        self.flag_stop_tracking_requested: bool = False

        self.pixel_size_um: Optional[float] = None
        self.objective: Optional[str] = None

        self.thread: Optional[QThread] = None
        self.trackingWorker: Optional[TrackingWorker] = None
        self.configuration_before_running_tracking: Optional[ChannelMode] = None
        self.was_live_before_tracking: bool = False
        self.camera_callback_was_enabled_before_tracking: bool = False
        self.image_resizing_factor: float = 1.0
        self.pixel_size_um_scaled: float = 1.0

        # Subscribe to EventBus commands
        if self._event_bus:
            self._event_bus.subscribe(SetTrackingParametersCommand, self._on_set_tracking_parameters)
            self._event_bus.subscribe(SetTrackingPathCommand, self._on_set_tracking_path)
            self._event_bus.subscribe(SetTrackingChannelsCommand, self._on_set_tracking_channels)
            self._event_bus.subscribe(StartTrackingExperimentCommand, self._on_start_tracking_experiment)
            self._event_bus.subscribe(StartTrackingCommand, self._on_start_tracking)
            self._event_bus.subscribe(StopTrackingCommand, self._on_stop_tracking)

    def start_tracking(self) -> None:
        # save pre-tracking configuration
        self._log.info("start tracking")
        # Publish tracking started state
        self._publish_tracking_state(is_tracking=True)
        self.configuration_before_running_tracking = (
            self.liveController.currentConfiguration
        )

        # stop live
        if self.liveController.is_live:
            self.was_live_before_tracking = True
            self.liveController.stop_live()  # @@@ to do: also uncheck the live button
        else:
            self.was_live_before_tracking = False

        # disable callback
        if self.camera.get_callbacks_enabled():
            self.camera_callback_was_enabled_before_tracking = True
            self.camera.enable_callbacks(False)
        else:
            self.camera_callback_was_enabled_before_tracking = False

        # hide roi selector
        self.imageDisplayWindow.hide_ROI_selector()

        # run tracking
        self.flag_stop_tracking_requested = False
        # create a QThread object
        try:
            if self.thread.isRunning():
                self._log.info("*** previous tracking thread is still running ***")
                self.thread.terminate()
                self.thread.wait()
                self._log.info("*** previous tracking threaded manually stopped ***")
        except Exception:
            pass
        self.thread = QThread()
        # create a worker object
        self.trackingWorker = TrackingWorker(self)
        # move the worker to the thread
        self.trackingWorker.moveToThread(self.thread)
        # connect signals and slots
        self.thread.started.connect(self.trackingWorker.run)
        self.trackingWorker.finished.connect(self._on_tracking_stopped)
        self.trackingWorker.finished.connect(self.trackingWorker.deleteLater)
        self.trackingWorker.finished.connect(self.thread.quit)
        self.trackingWorker.image_to_display.connect(self.slot_image_to_display)
        self.trackingWorker.image_to_display_multi.connect(
            self.slot_image_to_display_multi
        )
        self.trackingWorker.signal_current_configuration.connect(
            self.slot_current_configuration
        )
        # self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.thread.quit)
        # start the thread
        self.thread.start()

    def _on_tracking_stopped(self) -> None:
        # restore the previous selected mode
        self.signal_current_configuration.emit(
            self.configuration_before_running_tracking
        )
        self.liveController.set_microscope_mode(
            self.configuration_before_running_tracking
        )

        # re-enable callback
        if self.camera_callback_was_enabled_before_tracking:
            self.camera.enable_callbacks(True)
            self.camera_callback_was_enabled_before_tracking = False

        # re-enable live if it's previously on
        if self.was_live_before_tracking:
            self.liveController.start_live()

        # show ROI selector
        self.imageDisplayWindow.show_ROI_selector()

        # emit the acquisition finished signal to enable the UI
        self.signal_tracking_stopped.emit()
        # Publish tracking stopped state
        self._publish_tracking_state(is_tracking=False)
        QApplication.processEvents()

    def start_new_experiment(
        self, experiment_ID: str
    ) -> None:  # @@@ to do: change name to prepare_folder_for_new_experiment
        # generate unique experiment ID
        self.experiment_ID = (
            experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        )
        self.recording_start_time = time.time()
        # create a new folder
        try:
            utils.ensure_directory_exists(
                os.path.join(self.base_path, self.experiment_ID)
            )
            self.channelConfigurationManager.save_current_configuration_to_path(
                self.objectiveStore.current_objective,
                os.path.join(self.base_path, self.experiment_ID)
                + "/configurations.xml",
            )  # save the configuration for the experiment
        except Exception:
            self._log.info("error in making a new folder")
            pass

    def set_selected_configurations(
        self, selected_configurations_name: List[str]
    ) -> None:
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.channelConfigurationManager.get_channel_configuration_by_name(
                self.objectiveStore.current_objective, configuration_name
            )
            if config:
                self.selected_configurations.append(config)

    def toggle_stage_tracking(self, state: int) -> None:
        self.flag_stage_tracking_enabled = state > 0
        self._log.info(
            "set stage tracking enabled to " + str(self.flag_stage_tracking_enabled)
        )

    def toggel_enable_af(self, state: int) -> None:
        self.flag_AF_enabled = state > 0
        self._log.info("set af enabled to " + str(self.flag_AF_enabled))

    def toggel_save_images(self, state: int) -> None:
        self.flag_save_image = state > 0
        self._log.info("set save images to " + str(self.flag_save_image))

    def set_base_path(self, path: str) -> None:
        self.base_path = path

    def stop_tracking(self) -> None:
        self.flag_stop_tracking_requested = True
        self._log.info("stop tracking requested")

    def slot_image_to_display(self, image: np.ndarray) -> None:
        self.image_to_display.emit(image)

    def slot_image_to_display_multi(
        self, image: np.ndarray, illumination_source: int
    ) -> None:
        self.image_to_display_multi.emit(image, illumination_source)

    def slot_current_configuration(self, configuration: ChannelMode) -> None:
        self.signal_current_configuration.emit(configuration)

    def update_pixel_size(self, pixel_size_um: float) -> None:
        self.pixel_size_um = pixel_size_um

    def update_tracker_selection(self, tracker_str: str) -> None:
        self.tracker.update_tracker_type(tracker_str)

    def set_tracking_time_interval(self, time_interval: float) -> None:
        self.tracking_time_interval_s = time_interval

    def update_image_resizing_factor(self, image_resizing_factor: float) -> None:
        self.image_resizing_factor = image_resizing_factor
        self._log.info(
            "update tracking image resizing factor to "
            + str(self.image_resizing_factor)
        )
        self.pixel_size_um_scaled = self.pixel_size_um / self.image_resizing_factor

    # =========================================================================
    # EventBus Handlers
    # =========================================================================

    def _on_set_tracking_parameters(self, cmd: SetTrackingParametersCommand) -> None:
        """Handle SetTrackingParametersCommand from EventBus."""
        if cmd.time_interval_s is not None:
            self.set_tracking_time_interval(cmd.time_interval_s)
        if cmd.enable_stage_tracking is not None:
            self.flag_stage_tracking_enabled = cmd.enable_stage_tracking
        if cmd.enable_autofocus is not None:
            self.flag_AF_enabled = cmd.enable_autofocus
        if cmd.save_images is not None:
            self.flag_save_image = cmd.save_images
        if cmd.tracker_type is not None:
            self.update_tracker_selection(cmd.tracker_type)
        if cmd.pixel_size_um is not None:
            self.update_pixel_size(cmd.pixel_size_um)
        if cmd.objective is not None:
            self.objective = cmd.objective
        if cmd.image_resizing_factor is not None:
            self.update_image_resizing_factor(cmd.image_resizing_factor)

    def _on_set_tracking_path(self, cmd: SetTrackingPathCommand) -> None:
        """Handle SetTrackingPathCommand from EventBus."""
        self.set_base_path(cmd.base_path)

    def _on_set_tracking_channels(self, cmd: SetTrackingChannelsCommand) -> None:
        """Handle SetTrackingChannelsCommand from EventBus."""
        self.set_selected_configurations(cmd.channel_names)

    def _on_start_tracking_experiment(self, cmd: StartTrackingExperimentCommand) -> None:
        """Handle StartTrackingExperimentCommand from EventBus."""
        self.start_new_experiment(cmd.experiment_id)

    def _on_start_tracking(self, cmd: StartTrackingCommand) -> None:
        """Handle StartTrackingCommand from EventBus."""
        self.start_tracking()

    def _on_stop_tracking(self, cmd: StopTrackingCommand) -> None:
        """Handle StopTrackingCommand from EventBus."""
        self.stop_tracking()

    def _publish_tracking_state(self, is_tracking: bool) -> None:
        """Publish tracking state changed event."""
        if self._event_bus:
            self._event_bus.publish(TrackingStateChanged(is_tracking=is_tracking))


class TrackingWorker(QObject):
    finished = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(ChannelMode)

    def __init__(self, trackingController: TrackingController) -> None:
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.trackingController: TrackingController = trackingController

        self.camera: AbstractCamera = self.trackingController.camera
        self.stage: AbstractStage = self.trackingController.stage
        self.microcontroller: "Microcontroller" = (
            self.trackingController.microcontroller
        )
        self.liveController: "LiveController" = self.trackingController.liveController
        self.autofocusController: Any = self.trackingController.autofocusController
        self.channelConfigurationManager: Any = (
            self.trackingController.channelConfigurationManager
        )
        self.imageDisplayWindow: "ImageDisplayWindow" = (
            self.trackingController.imageDisplayWindow
        )
        self.display_resolution_scaling: float = (
            self.trackingController.display_resolution_scaling
        )
        self.counter: int = self.trackingController.counter
        self.experiment_ID: Optional[str] = self.trackingController.experiment_ID
        self.base_path: Optional[str] = self.trackingController.base_path
        self.selected_configurations: List[ChannelMode] = (
            self.trackingController.selected_configurations
        )
        self.tracker: tracking.Tracker_Image = trackingController.tracker

        self.number_of_selected_configurations: int = len(self.selected_configurations)

        self.image_saver: ImageSaver_Tracking = ImageSaver_Tracking(
            base_path=os.path.join(self.base_path, self.experiment_ID),
            image_format="bmp",
        )

        self.txt_file: Optional[TextIOWrapper] = None
        self.csv_file: Optional[TextIOWrapper] = None

    def _select_config(self, config: ChannelMode) -> None:
        self.signal_current_configuration.emit(config)
        # TODO(imo): replace with illumination controller.
        self.liveController.set_microscope_mode(config)
        self.microcontroller.wait_till_operation_is_completed()
        self.liveController.turn_on_illumination()  # keep illumination on for single configuration acqusition
        self.microcontroller.wait_till_operation_is_completed()

    def run(self) -> None:
        tracking_frame_counter = 0

        # save metadata
        self.txt_file = open(
            os.path.join(self.base_path, self.experiment_ID, "metadata.txt"), "w+"
        )
        self.txt_file.write(
            "t0: " + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f") + "\n"
        )
        self.txt_file.write("objective: " + self.trackingController.objective + "\n")
        self.txt_file.close()

        # create a file for logging
        self.csv_file = open(
            os.path.join(self.base_path, self.experiment_ID, "track.csv"), "w+"
        )
        self.csv_file.write(
            "dt (s), x_stage (mm), y_stage (mm), z_stage (mm), x_image (mm), y_image(mm), image_filename\n"
        )

        # reset tracker
        self.tracker.reset()

        # get the manually selected roi
        init_roi = self.imageDisplayWindow.get_roi_bounding_box()
        self.tracker.set_roi_bbox(init_roi)

        # tracking loop
        while not self.trackingController.flag_stop_tracking_requested:
            self._log.info("tracking_frame_counter: " + str(tracking_frame_counter))
            if tracking_frame_counter == 0:
                is_first_frame = True
            else:
                is_first_frame = False

            # timestamp
            timestamp_last_frame = time.time()

            # switch to the tracking config
            config = self.selected_configurations[0]

            # do autofocus
            if self.trackingController.flag_AF_enabled and tracking_frame_counter > 1:
                # do autofocus
                self._log.info(">>> autofocus")
                self.autofocusController.autofocus()
                self.autofocusController.wait_till_autofocus_has_completed()
                self._log.info(">>> autofocus completed")

            # get current position
            pos = self.stage.get_pos()

            # grab an image
            config = self.selected_configurations[0]
            if self.number_of_selected_configurations > 1:
                self._select_config(config)
            self.camera.send_trigger()
            camera_frame = self.camera.read_camera_frame()
            image = camera_frame.frame
            t = camera_frame.timestamp
            if self.number_of_selected_configurations > 1:
                self.liveController.turn_off_illumination()  # keep illumination on for single configuration acqusition
            image = np.squeeze(image)
            # get image size
            image_shape = image.shape
            image_center = np.array([image_shape[1] * 0.5, image_shape[0] * 0.5])

            # image the rest configurations
            for config_ in self.selected_configurations[1:]:
                self._select_config(config_)

                self.camera.send_trigger()
                image_ = self.camera.read_frame()
                # TODO(imo): use illumination controller
                self.liveController.turn_off_illumination()
                image_ = np.squeeze(image_)
                # display image
                image_to_display_ = utils.crop_image(
                    image_,
                    round(
                        image_.shape[1] * self.liveController.display_resolution_scaling
                    ),
                    round(
                        image_.shape[0] * self.liveController.display_resolution_scaling
                    ),
                )
                self.image_to_display_multi.emit(
                    image_to_display_, config_.illumination_source
                )
                # save image
                if self.trackingController.flag_save_image:
                    if camera_frame.is_color():
                        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                    self.image_saver.enqueue(
                        image_, tracking_frame_counter, str(config_.name)
                    )

            # track
            object_found, centroid, rect_pts = self.tracker.track(
                image, None, is_first_frame=is_first_frame
            )
            if not object_found:
                self._log.error("tracker: object not found")
                break
            in_plane_position_error_pixel = image_center - centroid
            in_plane_position_error_mm = (
                in_plane_position_error_pixel
                * self.trackingController.pixel_size_um_scaled
                / 1000
            )
            x_error_mm = in_plane_position_error_mm[0]
            y_error_mm = in_plane_position_error_mm[1]

            # display the new bounding box and the image
            self.imageDisplayWindow.update_bounding_box(rect_pts)
            self.imageDisplayWindow.display_image(image)

            # move
            if self.trackingController.flag_stage_tracking_enabled:
                # TODO(imo): This needs testing!
                self.stage.move_x(x_error_mm)
                self.stage.move_y(y_error_mm)

            # save image
            if self.trackingController.flag_save_image:
                self.image_saver.enqueue(
                    image, tracking_frame_counter, str(config.name)
                )

            # save position data
            self.csv_file.write(
                str(t)
                + ","
                + str(pos.x_mm)
                + ","
                + str(pos.y_mm)
                + ","
                + str(pos.z_mm)
                + ","
                + str(x_error_mm)
                + ","
                + str(y_error_mm)
                + ","
                + str(tracking_frame_counter)
                + "\n"
            )
            if tracking_frame_counter % 100 == 0:
                self.csv_file.flush()

            # wait till tracking interval has elapsed
            while (
                time.time() - timestamp_last_frame
                < self.trackingController.tracking_time_interval_s
            ):
                time.sleep(0.005)

            # increament counter
            tracking_frame_counter = tracking_frame_counter + 1

        # tracking terminated
        self.csv_file.close()
        self.image_saver.close()
        self.finished.emit()
