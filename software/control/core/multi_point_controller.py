from dataclasses import dataclass
from typing import Callable

import numpy as np

from control.utils_config import ChannelMode
from squid.abc import CameraFrame


@dataclass
class MultiPointControllerFunctions:
    signal_acquisition_finished: Callable[[], None]
    signal_new_image: Callable[[CameraFrame], None]
    signal_new_spectrum: Callable[[np.ndarray], None]
    signal_current_configuration: Callable[[ChannelMode], None]
    signal_current_fov: Callable[[float, float], None]



class MultiPointController(QObject):
    acquisition_finished = Signal()
    image_to_display = Signal(np.ndarray) # replace with signal_new_image
    image_to_display_multi = Signal(np.ndarray, int) # replace with signal_new_image
    spectrum_to_display = Signal(np.ndarray)
    signal_current_configuration = Signal(ChannelMode)
    signal_register_current_fov = Signal(float, float)
    signal_stitcher = Signal(str)  # Replace with signal_acquisition_finished
    napari_layers_init = Signal(int, int, object)
    napari_layers_update = Signal(np.ndarray, float, float, int, str)  # image, x_mm, y_mm, k, channel
    signal_set_display_tabs = Signal(list, int)
    signal_z_piezo_um = Signal(float)
    signal_acquisition_progress = Signal(int, int, int)
    signal_region_progress = Signal(int, int)
    signal_coordinates = Signal(np.ndarray, np.ndarray, np.ndarray, np.ndarray)  # x, y, z, region

    def __init__(
        self,
        camera: AbstractCamera,
        stage: AbstractStage,
        piezo: Optional[PiezoStage],
        microcontroller: Microcontroller,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        channel_configuration_manager: ChannelConfigurationManager,
        usb_spectrometer=None,
        scan_coordinates: Optional[ScanCoordinates] = None,
        fluidics=None,
        parent=None,
        headless=False,
    ):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.camera: AbstractCamera = camera
        self.stage: AbstractStage = stage
        self.piezo: Optional[PiezoStage] = piezo
        self.microcontroller: Microcontroller = microcontroller
        self.liveController: LiveController = live_controller
        self.autofocusController: AutoFocusController = autofocus_controller
        self.objectiveStore: ObjectiveStore = objective_store
        self.channelConfigurationManager: ChannelConfigurationManager = channel_configuration_manager
        self.multiPointWorker: Optional[MultiPointWorker] = None
        self.thread: Optional[QThread] = None
        self.NX = 1
        self.NY = 1
        self.NZ = 1
        self.Nt = 1
        self.deltaX = Acquisition.DX
        self.deltaY = Acquisition.DY
        # TODO(imo): Switch all to consistent mm units
        self.deltaZ = Acquisition.DZ / 1000
        self.deltat = 0
        self.do_autofocus = False
        self.do_reflection_af = False
        self.focus_map = None
        self.use_manual_focus_map = False
        self.gen_focus_map = False
        self.focus_map_storage = []
        self.already_using_fmap = False
        self.do_segmentation = False
        self.display_resolution_scaling = Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
        self.counter = 0
        self.experiment_ID = None
        self.base_path = None
        self.use_piezo = MULTIPOINT_USE_PIEZO_FOR_ZSTACKS
        self.selected_configurations = []
        self.usb_spectrometer = usb_spectrometer
        self.scanCoordinates = scan_coordinates
        self.scan_region_names = []
        self.scan_region_coords_mm = []
        self.scan_region_fov_coords_mm = {}
        self.parent = parent
        self.start_time = 0
        self.old_images_per_page = 1
        z_mm_current = self.stage.get_pos().z_mm
        self.z_range = [z_mm_current, z_mm_current + self.deltaZ * (self.NZ - 1)]  # [start_mm, end_mm]
        self.use_fluidics = False
        self.fluidics = fluidics

        self.headless = headless
        self.z_stacking_config = Z_STACKING_CONFIG

    def acquisition_in_progress(self):
        if self.thread and self.thread.isRunning() and self.multiPointWorker:
            return True
        return False

    def set_use_piezo(self, checked):
        if checked and self.piezo is None:
            raise ValueError("Cannot enable piezo - no piezo stage configured")
        self.use_piezo = checked
        if self.multiPointWorker:
            self.multiPointWorker.update_use_piezo(checked)

    def set_z_stacking_config(self, z_stacking_config_index):
        if z_stacking_config_index in Z_STACKING_CONFIG_MAP:
            self.z_stacking_config = Z_STACKING_CONFIG_MAP[z_stacking_config_index]
        print(f"z-stacking configuration set to {self.z_stacking_config}")

    def set_z_range(self, minZ, maxZ):
        self.z_range = [minZ, maxZ]

    def set_NX(self, N):
        self.NX = N

    def set_NY(self, N):
        self.NY = N

    def set_NZ(self, N):
        self.NZ = N

    def set_Nt(self, N):
        self.Nt = N

    def set_deltaX(self, delta):
        self.deltaX = delta

    def set_deltaY(self, delta):
        self.deltaY = delta

    def set_deltaZ(self, delta_um):
        self.deltaZ = delta_um / 1000

    def set_deltat(self, delta):
        self.deltat = delta

    def set_af_flag(self, flag):
        self.do_autofocus = flag

    def set_reflection_af_flag(self, flag):
        self.do_reflection_af = flag

    def set_manual_focus_map_flag(self, flag):
        self.use_manual_focus_map = flag

    def set_gen_focus_map_flag(self, flag):
        self.gen_focus_map = flag
        if not flag:
            self.autofocusController.set_focus_map_use(False)

    def set_segmentation_flag(self, flag):
        self.do_segmentation = flag

    def set_focus_map(self, focusMap):
        self.focus_map = focusMap  # None if dont use focusMap

    def set_base_path(self, path):
        self.base_path = path

    def set_use_fluidics(self, use_fluidics):
        self.use_fluidics = use_fluidics

    def start_new_experiment(self, experiment_ID):  # @@@ to do: change name to prepare_folder_for_new_experiment
        # generate unique experiment ID
        self.experiment_ID = experiment_ID.replace(" ", "_") + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        self.recording_start_time = time.time()
        # create a new folder
        utils.ensure_directory_exists(os.path.join(self.base_path, self.experiment_ID))
        self.channelConfigurationManager.write_configuration_selected(
            self.objectiveStore.current_objective,
            self.selected_configurations,
            os.path.join(self.base_path, self.experiment_ID) + "/configurations.xml",
        )  # save the configuration for the experiment
        # Prepare acquisition parameters
        acquisition_parameters = {
            "dx(mm)": self.deltaX,
            "Nx": self.NX,
            "dy(mm)": self.deltaY,
            "Ny": self.NY,
            "dz(um)": self.deltaZ * 1000 if self.deltaZ != 0 else 1,
            "Nz": self.NZ,
            "dt(s)": self.deltat,
            "Nt": self.Nt,
            "with AF": self.do_autofocus,
            "with reflection AF": self.do_reflection_af,
            "with manual focus map": self.use_manual_focus_map,
        }
        try:  # write objective data if it is available
            current_objective = self.parent.objectiveStore.current_objective
            objective_info = self.parent.objectiveStore.objectives_dict.get(current_objective, {})
            acquisition_parameters["objective"] = {}
            for k in objective_info.keys():
                acquisition_parameters["objective"][k] = objective_info[k]
            acquisition_parameters["objective"]["name"] = current_objective
        except:
            try:
                objective_info = OBJECTIVES[DEFAULT_OBJECTIVE]
                acquisition_parameters["objective"] = {}
                for k in objective_info.keys():
                    acquisition_parameters["objective"][k] = objective_info[k]
                acquisition_parameters["objective"]["name"] = DEFAULT_OBJECTIVE
            except:
                pass
        # TODO: USE OBJECTIVE STORE DATA
        acquisition_parameters["sensor_pixel_size_um"] = self.camera.get_pixel_size_binned_um()
        acquisition_parameters["tube_lens_mm"] = TUBE_LENS_MM
        f = open(os.path.join(self.base_path, self.experiment_ID) + "/acquisition parameters.json", "w")
        f.write(json.dumps(acquisition_parameters))
        f.close()

    def set_selected_configurations(self, selected_configurations_name):
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.channelConfigurationManager.get_channel_configuration_by_name(
                self.objectiveStore.current_objective, configuration_name
            )
            if config:
                self.selected_configurations.append(config)

    def get_acquisition_image_count(self):
        """
        Given the current settings on this controller, return how many images an acquisition will
        capture and save to disk.

        NOTE: This does not cover debug images (eg: auto focus) or user created images (eg: custom scripts).

        NOTE: This does attempt to include the "merged" image if that config is enabled.

        Raises a ValueError if the class is not configured for a valid acquisition.
        """
        try:
            # We have Nt timepoints.  For each timepoint, we capture images at all the regions.  Each
            # region has a list of coordinates that we capture at, and at each coordinate we need to
            # do a capture for each requested camera + lighting + other configuration selected.  So
            # total image count is:
            coords_per_region = [
                len(region_coords) for (region_id, region_coords) in self.scanCoordinates.region_fov_coordinates.items()
            ]
            all_regions_coord_count = sum(coords_per_region)

            non_merged_images = self.Nt * self.NZ * all_regions_coord_count * len(self.selected_configurations)
            # When capturing merged images, we capture 1 per fov (where all the configurations are merged)
            merged_images = self.Nt * self.NZ * all_regions_coord_count if control._def.MERGE_CHANNELS else 0

            return non_merged_images + merged_images
        except AttributeError:
            # We don't init all fields in __init__, so it's easy to get attribute errors.  We consider
            # this "not configured" and want it to be a ValueError.
            raise ValueError("Not properly configured for an acquisition, cannot calculate image count.")

    def _temporary_get_an_image_hack(self) -> Tuple[np.array, bool]:
        was_streaming = self.camera.get_is_streaming()
        callbacks_were_enabled = self.camera.get_callbacks_enabled()
        self.camera.enable_callbacks(False)
        test_frame = None
        if not was_streaming:
            self.camera.start_streaming()
        try:
            config = self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)[0]
            if (
                self.liveController.trigger_mode == TriggerMode.SOFTWARE
                or self.liveController.trigger_mode == TriggerMode.HARDWARE
            ):
                self.camera.send_trigger()
            test_frame = self.camera.read_camera_frame()
        finally:
            self.camera.enable_callbacks(callbacks_were_enabled)
            if not was_streaming:
                self.camera.stop_streaming()
        return (test_frame.frame, test_frame.is_color()) if test_frame else (None, False)

    def get_estimated_acquisition_disk_storage(self):
        """
        This does its best to return the number of bytes needed to store the settings for the currently
        configured acquisition on disk.  If you don't have at least this amount of disk space available
        when starting this acquisition, it is likely it will fail with an "out of disk space" error.
        """
        # TODO(imo): This needs updating for AbstractCamera
        if not len(self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)):
            raise ValueError("Cannot calculate disk space requirements without any valid configurations.")
        first_config = self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)[0]

        # Our best bet is to grab an image, and use that for our size estimate.
        test_image = None
        try:
            test_image, is_color = self._temporary_get_an_image_hack()
        except Exception as e:
            self._log.exception("Couldn't capture image from camera for size estimate, using worst cast image.")
            # Not ideal that we need to catch Exception, but the camera implementations vary wildly...
            pass

        if test_image is None:
            is_color = squid.abc.CameraPixelFormat.is_color_format(self.camera.get_pixel_format())
            # Do our best to create a fake image with the correct properties.
            # TODO(imo): It'd be better to pull this from our camera but need to wait for AbstractCamera for a consistent way to do that.
            width, height = self.camera.get_crop_size()
            bytes_per_pixel = 3 if is_color else 2  # Worst case assumptions: 24 bit color, 16 bit grayscale

            test_image = np.random.randint(2**16 - 1, size=(height, width, (3 if is_color else 1)), dtype=np.uint16)

        # Depending on settings, we modify the image before saving.  This means we need to actually save an image
        # to see how much disk space it takes up.  This can be very wrong (eg: if we compress during saving, then
        # it is dependent on the data), but is better than just guessing based on raw image size.
        with tempfile.TemporaryDirectory() as temp_save_dir:
            file_id = "test_id"
            test_config = first_config
            size_before = utils.get_directory_disk_usage(temp_save_dir)
            saved_image = utils_acquisition.save_image(test_image, file_id, temp_save_dir, test_config, is_color)
            size_after = utils.get_directory_disk_usage(temp_save_dir)

            size_per_image = size_after - size_before

        # Add in 100kB for non-image files.  This is normally more like 10k total, so this gives us extra.
        non_image_file_size = 100 * 1024

        return size_per_image * self.get_acquisition_image_count() + non_image_file_size

    def run_acquisition(self, acquire_current_fov=False):
        if not self.validate_acquisition_settings():
            # emit acquisition finished signal to re-enable the UI
            self.acquisition_finished.emit()
            return

        self._log.info("start multipoint")

        if acquire_current_fov:
            pos = self.stage.get_pos()
            self.scan_region_coords_mm = [(pos.x_mm, pos.y_mm)]
            self.scan_region_names = "current"
            self.scan_region_fov_coords_mm = {"current": [(pos.x_mm, pos.y_mm)]}
            self.run_acquisition_current_fov = True
        else:
            self.scan_region_coords_mm = list(self.scanCoordinates.region_centers.values())
            self.scan_region_names = list(self.scanCoordinates.region_centers.keys())
            self.scan_region_fov_coords_mm = self.scanCoordinates.region_fov_coordinates
            self.run_acquisition_current_fov = False
        # Save coordinates to CSV in top level folder
        coordinates_df = pd.DataFrame(columns=["region", "x (mm)", "y (mm)", "z (mm)"])
        for region_id, coords_list in self.scan_region_fov_coords_mm.items():
            for coord in coords_list:
                row = {"region": region_id, "x (mm)": coord[0], "y (mm)": coord[1]}
                # Add z coordinate if available
                if len(coord) > 2:
                    row["z (mm)"] = coord[2]
                coordinates_df = pd.concat([coordinates_df, pd.DataFrame([row])], ignore_index=True)
        coordinates_df.to_csv(os.path.join(self.base_path, self.experiment_ID, "coordinates.csv"), index=False)

        self._log.info(f"num fovs: {sum(len(coords) for coords in self.scan_region_fov_coords_mm)}")
        self._log.info(f"num regions: {len(self.scan_region_coords_mm)}")
        self._log.info(f"region ids: {self.scan_region_names}")
        self._log.info(f"region centers: {self.scan_region_coords_mm}")

        self.abort_acqusition_requested = False

        self.configuration_before_running_multipoint = self.liveController.currentConfiguration
        # stop live
        if self.liveController.is_live:
            self.liveController_was_live_before_multipoint = True
            self.liveController.stop_live()  # @@@ to do: also uncheck the live button
        else:
            self.liveController_was_live_before_multipoint = False

        self.camera_callback_was_enabled_before_multipoint = self.camera.get_callbacks_enabled()
        # We need callbacks, because we trigger and then use callbacks for image processing.  This
        # lets us do overlapping triggering (soon).
        self.camera.enable_callbacks(True)

        if self.usb_spectrometer != None:
            if self.usb_spectrometer.streaming_started == True and self.usb_spectrometer.streaming_paused == False:
                self.usb_spectrometer.pause_streaming()
                self.usb_spectrometer_was_streaming = True
            else:
                self.usb_spectrometer_was_streaming = False

        # set current tabs
        if not self.run_acquisition_current_fov:
            self.signal_set_display_tabs.emit(self.selected_configurations, self.NZ)
        else:
            self.signal_set_display_tabs.emit(
                self.selected_configurations, 2
            )  # temp: modify the signal to show multiChannel Widget instead of Mosaic Widget

        # run the acquisition
        self.timestamp_acquisition_started = time.time()

        if self.focus_map:
            self._log.info("Using focus surface for Z interpolation")
            for region_id in self.scan_region_names:
                region_fov_coords = self.scan_region_fov_coords_mm[region_id]
                # Convert each tuple to list for modification
                for i, coords in enumerate(region_fov_coords):
                    x, y = coords[:2]  # This handles both (x,y) and (x,y,z) formats
                    z = self.focus_map.interpolate(x, y, region_id)
                    # Modify the list directly
                    region_fov_coords[i] = (x, y, z)
                    self.scanCoordinates.update_fov_z_level(region_id, i, z)

        elif self.gen_focus_map and not self.do_reflection_af:
            self._log.info("Generating autofocus plane for multipoint grid")
            bounds = self.scanCoordinates.get_scan_bounds()
            if not bounds:
                return
            x_min, x_max = bounds["x"]
            y_min, y_max = bounds["y"]

            # Calculate scan dimensions and center
            x_span = abs(x_max - x_min)
            y_span = abs(y_max - y_min)
            x_center = (x_max + x_min) / 2
            y_center = (y_max + y_min) / 2

            # Determine grid size based on scan dimensions
            if x_span < self.deltaX:
                fmap_Nx = 2
                fmap_dx = self.deltaX  # Force deltaX spacing for small scans
            else:
                fmap_Nx = min(4, max(2, int(x_span / self.deltaX) + 1))
                fmap_dx = max(self.deltaX, x_span / (fmap_Nx - 1))

            if y_span < self.deltaY:
                fmap_Ny = 2
                fmap_dy = self.deltaY  # Force deltaY spacing for small scans
            else:
                fmap_Ny = min(4, max(2, int(y_span / self.deltaY) + 1))
                fmap_dy = max(self.deltaY, y_span / (fmap_Ny - 1))

            # Calculate starting corner position (top-left of the AF map grid)
            starting_x_mm = x_center - (fmap_Nx - 1) * fmap_dx / 2
            starting_y_mm = y_center - (fmap_Ny - 1) * fmap_dy / 2
            # TODO(sm): af map should be a grid mapped to a surface, instead of just corners mapped to a plane
            try:
                # Store existing AF map if any
                self.focus_map_storage = []
                self.already_using_fmap = self.autofocusController.use_focus_map
                for x, y, z in self.autofocusController.focus_map_coords:
                    self.focus_map_storage.append((x, y, z))

                # Define grid corners for AF map
                coord1 = (starting_x_mm, starting_y_mm)  # Starting corner
                coord2 = (starting_x_mm + (fmap_Nx - 1) * fmap_dx, starting_y_mm)  # X-axis corner
                coord3 = (starting_x_mm, starting_y_mm + (fmap_Ny - 1) * fmap_dy)  # Y-axis corner

                self._log.info(f"Generating AF Map: Nx={fmap_Nx}, Ny={fmap_Ny}")
                self._log.info(f"Spacing: dx={fmap_dx:.3f}mm, dy={fmap_dy:.3f}mm")
                self._log.info(f"Center:  x=({x_center:.3f}mm, y={y_center:.3f}mm)")

                # Generate and enable the AF map
                self.autofocusController.gen_focus_map(coord1, coord2, coord3)
                self.autofocusController.set_focus_map_use(True)

                # Return to center position
                self.stage.move_x_to(x_center)
                self.stage.move_y_to(y_center)

            except ValueError:
                self._log.exception("Invalid coordinates for autofocus plane, aborting.")
                return

        self.multiPointWorker = MultiPointWorker(self, extra_job_classes=[])
        self.multiPointWorker.use_piezo = self.use_piezo

        if not self.headless:
            # create a QThread object
            self.thread = QThread(parent=self)
            # move the worker to the thread
            self.multiPointWorker.moveToThread(self.thread)
            # connect signals and slots
            self.thread.started.connect(self.multiPointWorker.run)
            self.multiPointWorker.finished.connect(self._on_acquisition_completed)
            self.multiPointWorker.finished.connect(self.multiPointWorker.deleteLater)
            self.multiPointWorker.finished.connect(self.thread.quit)
            self.multiPointWorker.image_to_display.connect(self.slot_image_to_display)
            self.multiPointWorker.image_to_display_multi.connect(self.slot_image_to_display_multi)
            self.multiPointWorker.spectrum_to_display.connect(self.slot_spectrum_to_display)
            self.multiPointWorker.signal_current_configuration.connect(self.slot_current_configuration)
            self.multiPointWorker.signal_register_current_fov.connect(self.slot_register_current_fov)
            self.multiPointWorker.napari_layers_init.connect(self.slot_napari_layers_init)
            self.multiPointWorker.napari_layers_update.connect(self.slot_napari_layers_update)
            self.multiPointWorker.signal_z_piezo_um.connect(self.slot_z_piezo_um)
            self.multiPointWorker.signal_acquisition_progress.connect(self.slot_acquisition_progress)
            self.multiPointWorker.signal_region_progress.connect(self.slot_region_progress)

            # self.thread.finished.connect(self.thread.deleteLater)
            self.thread.finished.connect(self.thread.quit)
            # start the thread
            self.thread.start()
        else:
            # for headless mode
            self.multiPointWorker.run()

    def _on_acquisition_completed(self):
        self._log.debug("MultiPointController._on_acquisition_completed called")
        # restore the previous selected mode
        if self.gen_focus_map:
            self.autofocusController.clear_focus_map()
            for x, y, z in self.focus_map_storage:
                self.autofocusController.focus_map_coords.append((x, y, z))
            self.autofocusController.use_focus_map = self.already_using_fmap
        self.signal_current_configuration.emit(self.configuration_before_running_multipoint)
        self.liveController.set_microscope_mode(self.configuration_before_running_multipoint)

        # Restore callbacks to pre-acquisition state
        self.camera.enable_callbacks(self.camera_callback_was_enabled_before_multipoint)

        # re-enable live if it's previously on
        if self.liveController_was_live_before_multipoint and RESUME_LIVE_AFTER_ACQUISITION:
            self.liveController.start_live()

        if self.usb_spectrometer != None:
            if self.usb_spectrometer_was_streaming:
                self.usb_spectrometer.resume_streaming()

        # emit the acquisition finished signal to enable the UI
        if self.parent is not None:
            try:
                self.parent.dataHandler.sort("Sort by prediction score")
                self.parent.dataHandler.signal_populate_page0.emit()
            except:
                pass
        self._log.info(f"total time for acquisition + processing + reset: {time.time() - self.recording_start_time}")
        utils.create_done_file(os.path.join(self.base_path, self.experiment_ID))

        if self.run_acquisition_current_fov:
            self.run_acquisition_current_fov = False
        else:
            # move back to the center of the current region if using "glass slide"
            if "current" in self.scanCoordinates.region_centers:
                region_center = self.scanCoordinates.region_centers["current"]
                try:
                    self.stage.move_x_to(region_center[0])
                    self.stage.move_y_to(region_center[1])
                    self.stage.move_z_to(region_center[2])
                except:
                    self._log.error("Failed to move to center of current region")

        self.acquisition_finished.emit()
        if not self.abort_acqusition_requested:
            self.signal_stitcher.emit(os.path.join(self.base_path, self.experiment_ID))

        if not self.headless:
            QApplication.processEvents()

    def request_abort_aquisition(self):
        self.abort_acqusition_requested = True

    def slot_image_to_display(self, image):
        self.image_to_display.emit(image)

    def slot_spectrum_to_display(self, data):
        self.spectrum_to_display.emit(data)

    def slot_image_to_display_multi(self, image, illumination_source):
        self.image_to_display_multi.emit(image, illumination_source)

    def slot_current_configuration(self, configuration):
        self.signal_current_configuration.emit(configuration)

    def slot_register_current_fov(self, x_mm, y_mm):
        self.signal_register_current_fov.emit(x_mm, y_mm)

    def slot_napari_layers_init(self, image_height, image_width, dtype):
        self.napari_layers_init.emit(image_height, image_width, dtype)

    def slot_napari_layers_update(self, image, x_mm, y_mm, k, channel):
        self.napari_layers_update.emit(image, x_mm, y_mm, k, channel)

    def slot_z_piezo_um(self, displacement_um):
        self.signal_z_piezo_um.emit(displacement_um)

    def slot_acquisition_progress(self, current_region, total_regions, current_time_point):
        self.signal_acquisition_progress.emit(current_region, total_regions, current_time_point)

    def slot_region_progress(self, current_fov, total_fovs):
        self.signal_region_progress.emit(current_fov, total_fovs)

    def validate_acquisition_settings(self) -> bool:
        """Validate settings before starting acquisition"""
        if self.do_reflection_af and not self.parent.laserAutofocusController.laser_af_properties.has_reference:
            QMessageBox.warning(
                None,
                "Laser Autofocus Not Ready",
                "Please set the laser autofocus reference position before starting acquisition with laser AF enabled.",
            )
            return False
        return True
