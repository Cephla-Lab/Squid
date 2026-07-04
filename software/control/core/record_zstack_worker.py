"""RecordZStackWorker — per-FOV orchestration for "Record + Z-Stack" acquisitions.

For each (time point, region, FOV) the worker:
  1. moves XY to the FOV,
  2. establishes a Z reference (laser AF if requested+available, else current Z),
  3. (optional) records a continuous high-fps stream to a per-FOV recording zarr,
  4. (optional) acquires a software-triggered z-stack saved via the inherited
     ``SaveZarrJob`` dispatch path.

The recording phase reuses the C3 ``StreamingCapture`` primitive
(``ContinuousFrameSource`` + ``RecordingRouter`` + ``CountStop`` +
``RecordingWriter``).  The z-stack phase reuses ``MultiPointWorkerBase``'s
shared single-frame capture + frame callback + job dispatch machinery, so the
worker builds its own ``JobRunner`` (or accepts a pre-warmed one) plus a
``BackpressureController`` exactly the way ``MultiPointWorker`` does.
"""

import os
import time
from typing import Callable, Dict, List, Optional, Tuple, Type

import numpy as np

import squid.logging
import control._def
from control._def import (
    Acquisition,
    SCAN_STABILIZATION_TIME_MS_X,
    SCAN_STABILIZATION_TIME_MS_Y,
    SCAN_STABILIZATION_TIME_MS_Z,
    TriggerMode,
)
from squid.abc import CameraAcquisitionMode
from control.core.acquisition_setup import compute_pixel_size_um
from control.core.multi_point_worker import MultiPointWorkerBase
from control.core.streaming_capture import (
    StreamingCapture,
    ContinuousFrameSource,
    RecordingRouter,
    CountStop,
    RecordingWriter,
)
from control.core.zarr_writer import ZarrAcquisitionConfig
from control.core.record_zstack_controller import (
    RecordZStackAcquisitionParameters,
    frame_count,
    zstack_offsets_um,
    zstack_plane_count,
)
from control.core.job_processing import (
    AcquisitionInfo,
    Job,
    JobRunner,
    SaveZarrJob,
    ZarrWriterInfo,
)
from control.core.backpressure import BackpressureController, BackpressureValues

log = squid.logging.get_logger("RecordZStackWorker")


class RecordZStackWorker(MultiPointWorkerBase):
    """Per-FOV record-and-z-stack acquisition worker.

    Scan coords are supplied as ``{region_id: [(x_mm, y_mm[, z_mm]), ...]}``.
    """

    def __init__(
        self,
        scope,
        live_controller,
        laser_auto_focus_controller,
        objective_store,
        params: RecordZStackAcquisitionParameters,
        callbacks,
        abort_requested_fn: Callable[[], bool],
        request_abort_fn: Callable[[], None],
        scan_region_fov_coords: Dict[object, List[Tuple]],
        prewarmed_job_runner: Optional[JobRunner] = None,
        prewarmed_bp_values: Optional[BackpressureValues] = None,
    ):
        super().__init__(
            scope=scope,
            live_controller=live_controller,
            callbacks=callbacks,
            abort_requested_fn=abort_requested_fn,
            request_abort_fn=request_abort_fn,
        )

        self.params = params
        self.laser_af = laser_auto_focus_controller
        self.objectiveStore = objective_store
        self._scan: Dict[object, List[Tuple]] = scan_region_fov_coords or {}

        # This worker drives the stage directly (no piezo z-stacking), and uses a
        # single time_point at a time — refine the base placeholders.
        self.use_piezo = False
        self.time_point = 0

        # Experiment output layout (mirrors MultiPointWorker.experiment_path).
        self.base_path = params.base_path
        self.experiment_ID = params.experiment_id
        self.experiment_path = os.path.join(self.base_path or "", self.experiment_ID or "")

        # Z-stack channels drive the inherited SaveZarrJob path.
        self.zstack_channels: List = list(params.zstack_channels or [])
        self._NZ = (
            zstack_plane_count(params.z_min_um, params.z_max_um, params.z_step_um) if params.zstack_enabled else 1
        )

        # Pre-compute acquisition-wide metadata (pixel size etc.) for zarr/job info.
        self._pixel_size_um = compute_pixel_size_um(self.objectiveStore, self.camera)

        self._time_increment_s = params.dt_s if params.Nt > 1 and params.dt_s > 0 else None
        self._physical_size_z_um = abs(params.z_step_um) if self._NZ > 1 else None

        # Build the z-stack job runner + backpressure (only when a z-stack will run).
        # Mirrors MultiPointWorker.__init__: per-FOV 5D non-HCS zarr output.
        self.acquisition_info = AcquisitionInfo(
            total_time_points=params.Nt,
            total_z_levels=self._NZ,
            total_channels=len(self.zstack_channels),
            channel_names=[c.name for c in self.zstack_channels],
            experiment_path=self.experiment_path,
            time_increment_s=self._time_increment_s,
            physical_size_z_um=self._physical_size_z_um,
            physical_size_x_um=self._pixel_size_um,
            physical_size_y_um=self._pixel_size_um,
        )

        # Per-acquisition fixed geometry — compute once and reuse for every FOV
        # (z-stack offsets and the recording frame shape don't change mid-run).
        self._zstack_offsets: List[float] = (
            zstack_offsets_um(params.z_min_um, params.z_max_um, params.z_step_um) if params.zstack_enabled else []
        )
        # Probing captures a frame, so it happens in run() after live view stops.
        self._frame_shape: Optional[Tuple[int, int, np.dtype]] = None
        # Set at the top of run(); _wait_for_dt paces timepoint STARTS from it.
        self._acq_start_time: Optional[float] = None

        if params.zstack_enabled and self.zstack_channels:
            self._setup_zstack_job_runner(prewarmed_job_runner, prewarmed_bp_values)
        else:
            # Still need a (disabled) backpressure controller so base methods that
            # reference self._backpressure don't crash if ever reached.
            self._backpressure = BackpressureController(enabled=False)

    # ------------------------------------------------------------------ setup
    def _setup_zstack_job_runner(self, prewarmed_job_runner, prewarmed_bp_values) -> None:
        bp_kwargs = {
            "max_jobs": control._def.ACQUISITION_MAX_PENDING_JOBS,
            "max_mb": control._def.ACQUISITION_MAX_PENDING_MB,
            "timeout_s": control._def.ACQUISITION_THROTTLE_TIMEOUT_S,
            "enabled": control._def.ACQUISITION_THROTTLING_ENABLED,
        }
        if prewarmed_bp_values is not None:
            bp_kwargs["bp_values"] = prewarmed_bp_values
        self._backpressure = BackpressureController(**bp_kwargs)

        # Channel metadata for zarr output.
        channel_names = [c.name for c in self.zstack_channels]
        channel_colors = [c.display_color for c in self.zstack_channels]
        channel_wavelengths: List[Optional[int]] = []
        illumination_config = self.microscope.config_repo.get_illumination_config()
        for c in self.zstack_channels:
            try:
                w = c.get_illumination_wavelength(illumination_config) if illumination_config else None
            except Exception:
                w = None
            channel_wavelengths.append(w)

        # FOV counts per region (non-HCS per-FOV 5D output -> count only used for 6D).
        region_fov_counts = {str(region_id): len(coords) for region_id, coords in self._scan.items()}

        zarr_writer_info = ZarrWriterInfo(
            base_path=self.experiment_path,
            t_size=self.params.Nt,
            c_size=len(self.zstack_channels),
            z_size=self._NZ,
            is_hcs=False,
            use_6d_fov=False,
            region_fov_counts=region_fov_counts,
            pixel_size_um=self._pixel_size_um,
            z_step_um=self._physical_size_z_um,
            time_increment_s=self._time_increment_s,
            channel_names=channel_names,
            channel_colors=channel_colors,
            channel_wavelengths=channel_wavelengths,
        )

        log_file_path = squid.logging.get_current_log_file_path()
        can_use_prewarmed = prewarmed_job_runner is not None and prewarmed_bp_values is not None

        job_runner: Optional[JobRunner] = None
        if Acquisition.USE_MULTIPROCESSING:
            if can_use_prewarmed and prewarmed_job_runner.is_ready():
                log.info("Using pre-warmed job runner for SaveZarrJob jobs")
                job_runner = prewarmed_job_runner
                job_runner.set_acquisition_info(self.acquisition_info)
                job_runner.set_zarr_writer_info(zarr_writer_info)
            else:
                if can_use_prewarmed:
                    log.warning("Pre-warmed job runner not ready; shutting it down and creating a new one")
                    try:
                        prewarmed_job_runner.shutdown(timeout_s=1.0)
                    except Exception as e:
                        log.error(f"Error shutting down hung pre-warmed runner: {e}")
                log.info("Creating job runner for SaveZarrJob jobs")
                job_runner = JobRunner(
                    self.acquisition_info,
                    cleanup_stale_ome_files=False,
                    log_file_path=log_file_path,
                    bp_pending_jobs=self._backpressure.pending_jobs_value,
                    bp_pending_bytes=self._backpressure.pending_bytes_value,
                    bp_capacity_event=self._backpressure.capacity_event,
                    zarr_writer_info=zarr_writer_info,
                )
                job_runner.start()

        self._job_runners = [(SaveZarrJob, job_runner)]

    # -------------------------------------------------------------------- run
    def run(self):
        """Top-level orchestration loop (runs on the acquisition thread).

        Recording manages its own CONTINUOUS streaming/callback via
        ``ContinuousFrameSource``; the z-stack phase manages its own
        software-trigger streaming + frame callback inside ``zstack()``.  The
        camera is left stopped between FOVs/phases, so this loop owns no camera
        callback of its own.
        """
        # Quiesce live view once for the whole acquisition (restored in finally).
        was_live = bool(getattr(self.liveController, "is_live", False))
        # Capture pre-acquisition hardware state so the finally can put the
        # camera/MCU/LiveController back the way the user had them: both phases
        # change the trigger mode, and every z-stack channel apply overwrites
        # the current channel configuration (exposure/gain/illumination).
        prev_trigger_mode = getattr(self.liveController, "trigger_mode", None)
        prev_configuration = getattr(self.liveController, "currentConfiguration", None)
        if was_live:
            try:
                self.liveController.stop_live()
            except Exception:
                log.exception("Failed to stop live view before acquisition")

        try:
            if self.params.recording_enabled:
                # Size the recording datasets from one real processed frame.
                # Deferred to here (not __init__) so the probe capture only
                # touches the camera after live view has been stopped.
                self._frame_shape = self._probe_frame_shape()

            if self.params.zstack_enabled and self._job_runners:
                self._backpressure.reset()

            self._acq_start_time = time.monotonic()
            for t_idx in range(self.params.Nt):
                self.time_point = t_idx
                action = self._pace_timepoint(t_idx)
                if action == "skip":
                    continue
                if action == "abort":
                    break
                if self.abort_requested_fn():
                    break

                for region_id, fovs in self._scan.items():
                    for fov_idx, coord in enumerate(fovs):
                        if self.abort_requested_fn():
                            return
                        self._move_xy(coord)
                        z_ref = self.establish_reference()

                        if self.params.recording_enabled:
                            self.record(t_idx, region_id, fov_idx, z_ref)
                        if self.params.zstack_enabled and self.zstack_channels:
                            self.zstack(t_idx, region_id, fov_idx, z_ref)
        except Exception as e:
            log.exception(e)
            self.request_abort_fn()
        finally:
            # Drain + shut down z-stack job runners (also closes backpressure).
            if self._job_runners:
                try:
                    self._finish_jobs()
                except Exception:
                    log.exception("Error finishing z-stack jobs")
            # Restore pre-acquisition hardware state: channel configuration first
            # (exposure/gain/illumination source), then trigger mode (which for
            # HARDWARE reads currentConfiguration.exposure_time), so camera +
            # LiveController + MCU agree again before live view resumes.
            if prev_configuration is not None:
                try:
                    self.liveController.set_microscope_mode(prev_configuration)
                except Exception:
                    log.exception("Failed to restore channel configuration after acquisition")
            if prev_trigger_mode is not None:
                try:
                    self.liveController.set_trigger_mode(prev_trigger_mode)
                except Exception:
                    log.exception("Failed to restore trigger mode after acquisition")
            # Restart live view once, only if it was running before the acquisition.
            if was_live:
                try:
                    self.liveController.start_live()
                except Exception:
                    log.exception("Failed to restart live view after acquisition")
            # Completion marker for downstream watchers (mirrors multipoint's
            # _on_acquisition_completed). Written on abort too: the directory is
            # final either way, and the zarr attrs record completeness.
            try:
                from control.utils import create_done_file

                if os.path.isdir(self.experiment_path):
                    create_done_file(self.experiment_path)
            except Exception:
                log.exception("Failed to write completion marker (.done)")
            try:
                self.callbacks.signal_acquisition_finished()
            except Exception:
                log.exception("signal_acquisition_finished callback failed")

    # ------------------------------------------------------------- reference
    def establish_reference(self) -> float:
        """Return the Z reference (mm) for this FOV.

        Uses laser AF ``move_to_target(0)`` when requested and a reference is set;
        on failure (raise or soft-fail return) falls back to the current stage Z.
        """
        if self.params.use_laser_af and self.laser_af is not None and self._laser_af_has_reference():
            try:
                ok = self.laser_af.move_to_target(0.0)
                if not ok:
                    log.warning("laser AF move_to_target(0) reported failure; using current Z")
            except Exception as e:
                log.warning(f"laser AF failed at FOV, falling back to current Z: {e}")
        return self.stage.get_pos().z_mm

    def _laser_af_has_reference(self) -> bool:
        try:
            return bool(self.laser_af.laser_af_properties.has_reference)
        except Exception:
            return False

    # ---------------------------------------------------------------- record
    def record(self, t_idx: int, region_id, fov_idx: int, z_ref: float) -> int:
        """Record a continuous stream to a per-FOV recording zarr, then restore Z.

        Returns the number of frames emitted.
        """
        # Apply the recording channel (exposure/gain/illumination settings).
        rec_channel = self.params.recording_channel
        if rec_channel is not None:
            self._select_config(rec_channel)

        # Move to z_ref + recording offset.
        self._move_z_to_offset(z_ref, self.params.recording_z_offset_um)

        # Size the dataset, pacing, and time metadata from the fps the camera can
        # actually deliver: a camera clamped below the requested rate (exposure
        # limit, PRECISE_FRAMERATE max) can never fill fps*duration frames within
        # duration seconds — the run would stall to the timeout and leave the
        # trailing planes blank.  The mode switch happens first because toupcam
        # resets its frame-rate strategy on mode change.
        self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
        effective_fps = self.params.fps
        try:
            achievable_fps = self.camera.set_frame_rate(self.params.fps)
            if achievable_fps and 0 < achievable_fps < self.params.fps:
                log.warning(
                    f"camera cannot deliver {self.params.fps:g} fps "
                    f"(achievable ≈ {achievable_fps:.2f}); recording at the achievable rate"
                )
                effective_fps = achievable_fps
        except Exception:
            log.exception("set_frame_rate probe failed; assuming the requested fps")

        T = max(1, frame_count(effective_fps, self.params.duration_s))
        out = self._recording_path(t_idx, region_id, fov_idx)
        y, x, dtype = self._frame_shape

        rec_channel_name = rec_channel.name if rec_channel is not None else "REC"
        rec_color = rec_channel.display_color if rec_channel is not None else "#FFFFFF"
        cfg = ZarrAcquisitionConfig(
            output_path=out,
            shape=(T, 1, 1, y, x),
            dtype=dtype,
            pixel_size_um=self._pixel_size_um if self._pixel_size_um is not None else 1.0,
            z_step_um=None,
            time_increment_s=(1.0 / effective_fps) if effective_fps and effective_fps > 0 else None,
            channel_names=[rec_channel_name],
            channel_colors=[rec_color],
            channel_wavelengths=[None],
            is_hcs=False,
        )
        writer = RecordingWriter(cfg)
        cap = StreamingCapture(
            ContinuousFrameSource(self.camera, effective_fps),
            RecordingRouter(effective_fps),
            CountStop(T),
            writer,
            abort_fn=self.abort_requested_fn,
        )
        # Generous timeout: enough to gather T frames even at a slow effective rate.
        timeout = self.params.duration_s * 3 + 5

        # The CONTINUOUS stream does not gate illumination per-frame, and
        # set_microscope_mode only energizes illumination when live (we are not
        # live here). Turn illumination on for the whole recording, off in finally,
        # so the recorded frames are not dark.
        self.liveController.turn_on_illumination()
        try:
            emitted = cap.run(timeout=timeout)
        finally:
            self.liveController.turn_off_illumination()
        log.info(f"recording done t={t_idx} region={region_id} fov={fov_idx}: {emitted}/{T} frames")

        # Restore the camera to a software-trigger-friendly state and Z reference.
        try:
            self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
        except Exception:
            log.exception("Failed to restore software-trigger acquisition mode after recording")
        self.stage.move_z_to(z_ref)
        self.wait_till_operation_is_completed()
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
        # Fail fast on write errors OR a wedged drain thread: either way the
        # cause is almost always systematic (full disk, stalled mount), so
        # continuing would burn the timeout at every remaining FOV producing
        # blank data.  A wedged finalize returns before errors are countable,
        # which is why write_error_count alone is not sufficient.
        # run() catches this, aborts, and signals finished.
        if writer.write_error_count > 0 or writer.finalize_wedged:
            raise RuntimeError(
                f"recording failed at t={t_idx} region={region_id} fov={fov_idx} "
                f"(write errors={writer.write_error_count}, drain wedged={writer.finalize_wedged}); "
                f"store sealed incomplete: {out}"
            )
        return emitted

    # ---------------------------------------------------------------- zstack
    def zstack(self, t_idx: int, region_id, fov_idx: int, z_ref: float) -> None:
        """Acquire a software-triggered z-stack via the inherited capture path.

        Each plane/channel goes through ``acquire_camera_image`` -> ``_image_callback``
        -> ``SaveZarrJob`` dispatch.  Restores Z to ``z_ref`` at the end.
        """
        self.time_point = t_idx
        offsets = self._zstack_offsets

        # The inherited capture path (acquire_camera_image) branches on
        # liveController.trigger_mode, so the LiveController and the camera must agree
        # on software-trigger mode.  set_trigger_mode(SOFTWARE) sets both the camera
        # acquisition mode and the microcontroller trigger mode.  No per-FOV restore:
        # run()'s finally restores the user's trigger mode once at the end of the
        # acquisition — restoring per FOV would flip-flop the camera and MCU 2x per
        # FOV for no benefit.  Manage the streaming lifecycle locally so it never
        # interferes with the recording phase's CONTINUOUS streaming.
        try:
            self.liveController.set_trigger_mode(TriggerMode.SOFTWARE)
        except Exception:
            log.exception("Failed to set software-trigger mode for z-stack")
            # Fall back to setting the camera directly so capture can still proceed.
            try:
                self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
                # Keep the LiveController's view of the mode in sync with the
                # camera: the inherited acquire_camera_image branches on
                # liveController.trigger_mode to gate illumination, so a stale
                # mode here would capture the entire z-stack dark.
                self.liveController.trigger_mode = TriggerMode.SOFTWARE
            except Exception:
                log.exception("Failed to set camera software-trigger mode for z-stack")
        self.camera.start_streaming()
        cb_id = self.camera.add_frame_callback(self._image_callback)
        # Make sure the trigger gate starts open.
        self._ready_for_next_trigger.set()

        current_path = self._zstack_dir(region_id)

        try:
            for z_idx, off_um in enumerate(offsets):
                self._move_z_to_offset(z_ref, off_um)
                for c_idx, config in enumerate(self.zstack_channels):
                    if self.abort_requested_fn():
                        return
                    file_id = f"{region_id}_{fov_idx}"
                    self.acquire_camera_image(
                        config=config,
                        file_ID=file_id,
                        current_path=current_path,
                        k=z_idx,
                        region_id=region_id,
                        fov=fov_idx,
                        config_idx=c_idx,
                    )
        finally:
            # Wait for the last in-flight frame, then detach our callback and stop
            # streaming (mirrors ContinuousFrameSource.stop()), so the next FOV's
            # recording phase starts ContinuousFrameSource on a stopped camera.
            self._wait_for_outstanding_callback_images()
            try:
                self.camera.remove_frame_callback(cb_id)
            except Exception:
                log.exception("Failed to remove z-stack frame callback")
            try:
                self.camera.stop_streaming()
            except Exception:
                log.exception("Failed to stop streaming after z-stack")
            self.stage.move_z_to(z_ref)
            self.wait_till_operation_is_completed()
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    # --------------------------------------------------------------- helpers
    def _move_xy(self, coord) -> None:
        self.stage.move_x_to(coord[0])
        self._sleep(SCAN_STABILIZATION_TIME_MS_X / 1000)
        self.stage.move_y_to(coord[1])
        self._sleep(SCAN_STABILIZATION_TIME_MS_Y / 1000)
        # (x, y, z) coords carry a stored per-FOV focus plane (flexible regions,
        # update_fov_z) — honor it like MultiPointWorker.move_to_coordinate, or
        # establish_reference() would reuse the previous FOV's Z on tilted samples.
        if len(coord) > 2 and coord[2] is not None:
            self.stage.move_z_to(coord[2])
            self.wait_till_operation_is_completed()
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def _move_z_to_offset(self, z_ref: float, offset_um: float) -> None:
        """Move to ``z_ref + offset_um`` (offset in µm, z_ref in mm) via the stage."""
        target_mm = z_ref + offset_um / 1000.0
        self.stage.move_z_to(target_mm)
        self.wait_till_operation_is_completed()
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def _probe_frame_shape(self) -> Tuple[int, int, np.dtype]:
        """Return (Y, X, dtype) for the recording dataset from one processed frame.

        ``get_resolution()`` reports the sensor/binned size, but frames delivered
        to callbacks pass through ``_process_raw_frame`` (software crop, rotation,
        ROI).  On cameras where the two differ, a dataset sized from
        ``get_resolution()`` makes every ``write_frame`` fail — a blank recording.
        Capture one real frame and size the dataset from it; fall back to
        ``get_resolution()`` only if the probe capture fails.
        """
        frame = None
        try:
            self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
            self.camera.start_streaming()
            self.camera.send_trigger()
            cam_frame = self.camera.read_camera_frame()
            if cam_frame is not None:
                frame = cam_frame.frame
        except Exception:
            log.exception("probe-frame capture failed; falling back to get_resolution()")
        finally:
            try:
                self.camera.stop_streaming()
            except Exception:
                log.exception("failed to stop streaming after probe frame")
        if frame is not None:
            if frame.ndim != 2:
                # A color frame (Y, X, 3) would silently produce a 2-D dataset
                # that every write then fails against — reject it up front.
                raise ValueError(
                    f"recording supports monochrome frames only; camera delivered shape {frame.shape} "
                    f"(set the camera to a mono pixel format for the recording phase)"
                )
            return int(frame.shape[0]), int(frame.shape[1]), frame.dtype
        log.warning("sizing recording dataset from get_resolution(); may mismatch delivered frames")
        width, height = self.camera.get_resolution()
        # Map pixel format to numpy dtype (MONO8 -> uint8, else uint16).
        try:
            from squid.config import CameraPixelFormat

            fmt = self.camera.get_pixel_format()
            dtype = np.uint8 if fmt == CameraPixelFormat.MONO8 else np.uint16
        except Exception:
            dtype = np.uint16
        return int(height), int(width), np.dtype(dtype)

    def _recording_path(self, t_idx: int, region_id, fov_idx: int) -> str:
        """Per-(t, region, fov) recording dataset path under {experiment}/recording."""
        return os.path.join(
            self.experiment_path,
            "recording",
            f"t{t_idx}",
            str(region_id),
            f"fov_{fov_idx}.ome.zarr",
        )

    def _zstack_dir(self, region_id) -> str:
        """Directory passed as ``current_path`` to the inherited capture path.

        SaveZarrJob ignores this (it builds its own path from ZarrWriterInfo), but
        SaveImageJob/SaveOMETiffJob would use it, so keep it valid.
        """
        path = os.path.join(self.experiment_path, "zstack", str(region_id))
        return path

    def _pace_timepoint(self, t_idx: int) -> str:
        """Decide how to handle time point ``t_idx``: 'run', 'skip', or 'abort'.

        Starts are paced on the absolute grid ``acquisition_start + t_idx*dt``.
        A slot whose start already passed is SKIPPED (grid-preserving, mirrors
        MultiPointWorker's skip loop) — running it late would silently stretch
        the real sampling interval while the recorded ``time_increment_s``
        metadata still claims ``dt``.
        """
        if t_idx == 0 or self.params.dt_s <= 0:
            return "run"
        start = self._acq_start_time if self._acq_start_time is not None else time.monotonic()
        if time.monotonic() > start + t_idx * self.params.dt_s:
            log.warning(
                f"skipping time point {t_idx}: per-timepoint work exceeded dt={self.params.dt_s:g}s "
                f"(grid-preserving skip, mirrors MultiPointWorker)"
            )
            return "skip"
        return "run" if self._wait_for_dt(t_idx) else "abort"

    def _wait_for_dt(self, t_idx: int) -> bool:
        """Sleep until time point ``t_idx``'s scheduled start (abort-aware).

        Uses time.monotonic() so an NTP clock step mid-acquisition cannot skew
        or collapse the remaining intervals.  Returns False on abort.
        """
        start = self._acq_start_time if self._acq_start_time is not None else time.monotonic()
        deadline = start + t_idx * self.params.dt_s
        while time.monotonic() < deadline:
            if self.abort_requested_fn():
                return False
            self._sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return True
