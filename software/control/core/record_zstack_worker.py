"""RecordZStackWorker — per-FOV orchestration for "Record + Z-Stack" acquisitions.

For each (time point, region, FOV) the worker:
  1. moves XY to the FOV,
  2. establishes a Z reference (laser AF if requested+available, else current Z),
  3. (optional) records a continuous high-fps stream to a per-FOV recording zarr,
  4. (optional) acquires a software-triggered z-stack saved via the inherited
     ``SaveZarrJob`` dispatch path.

The recording phase reuses the ``StreamingCapture`` primitive
(``ContinuousFrameSource`` + ``RecordingRouter`` + ``CountStop`` +
``RecordingWriter``).  The z-stack phase reuses ``MultiPointWorkerBase``'s
shared single-frame capture + frame callback + job dispatch machinery, so the
z-stack is saved in this process by an ``InProcessZarrRunner`` behind the same
``BackpressureController`` ``MultiPointWorker`` uses.
"""

import os
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

import squid.logging
from control._def import (
    SCAN_STABILIZATION_TIME_MS_X,
    SCAN_STABILIZATION_TIME_MS_Y,
    SCAN_STABILIZATION_TIME_MS_Z,
    TriggerMode,
)
from squid.abc import CameraAcquisitionMode
from control.core.acquisition_setup import compute_pixel_size_um
from control.core.multi_point_worker import MultiPointWorkerBase
from control.core.streaming_capture import (
    ContinuousFrameSource,
    CountStop,
    RecordingRouter,
    RecordingWriter,
    StreamingCapture,
)
from control.core.zarr_writer import ZarrAcquisitionConfig
from control.core.record_zstack_controller import (
    RecordZStackAcquisitionParameters,
    frame_count,
    recording_plane_offsets_um,
    zstack_offsets_um,
    zstack_plane_count,
)
from control.core.job_processing import (
    AcquisitionInfo,
    SaveZarrJob,
    ZarrWriterInfo,
)
from control.core.backpressure import BackpressureController

log = squid.logging.get_logger("RecordZStackWorker")


class RecordZStackWorker(MultiPointWorkerBase):
    """Per-FOV record-and-z-stack acquisition worker.

    Scan coords are supplied as ``{region_id: [(x_mm, y_mm[, z_mm]), ...]}``.
    """

    # How long the end of the run waits for a recording whose drain outlived its finalize() before
    # declaring the store not final: the same budget finalize() itself had.
    DRAIN_SETTLE_TIMEOUT_S = RecordingWriter.FINALIZE_TIMEOUT_S

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
        display_frame_fn: Optional[Callable] = None,
        display_fps: float = 0.0,
        run_state_writer=None,
    ):
        super().__init__(
            scope=scope,
            live_controller=live_controller,
            callbacks=callbacks,
            abort_requested_fn=abort_requested_fn,
            request_abort_fn=request_abort_fn,
            run_state_writer=run_state_writer,
        )

        self.params = params
        # Plain-callable live-preview tap (Qt-free; supplied by the GUI wrapper).
        self._display_frame_fn = display_frame_fn
        self._display_fps = display_fps
        self.laser_af = laser_auto_focus_controller
        self.objectiveStore = objective_store
        self._scan: Dict[object, List[Tuple]] = scan_region_fov_coords or {}

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
        # Recordings whose drain thread outlived their finalize(): see _settle_draining_recordings.
        self._draining_writers: List[RecordingWriter] = []
        # Achievable recording fps, probed once on the first FOV (the recording
        # exposure is fixed, so the camera's clamp can't change between FOVs).
        self._effective_fps: Optional[float] = None
        # True when the camera reported it will deliver at (or below) the requested
        # rate — it paces itself to the hint, or the request is at its free-run
        # maximum — so every frame is wanted and the RecordingRouter fills slots
        # sequentially instead of downsampling by arrival time.
        # Set at the top of run(); _wait_for_dt paces timepoint STARTS from it.
        self._acq_start_time: Optional[float] = None

        if params.zstack_enabled and self.zstack_channels:
            self._setup_zstack_job_runner()
        else:
            # Still need a (disabled) backpressure controller so base methods that
            # reference self._backpressure don't crash if ever reached.
            self._backpressure = BackpressureController(enabled=False)

    # ------------------------------------------------------------------ setup
    def _setup_zstack_job_runner(self) -> None:
        self._backpressure = self._create_backpressure()

        # Channel metadata for zarr output.
        channel_names, channel_colors, channel_wavelengths = self._channel_zarr_metadata(self.zstack_channels)

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
        self._job_runners = [(SaveZarrJob, self._create_in_process_zarr_runner(zarr_writer_info))]

    # -------------------------------------------------------------------- run
    def run(self):
        """Top-level orchestration loop (runs on the acquisition thread).

        Recording manages its own CONTINUOUS streaming/callback via
        ``ContinuousFrameSource``; the z-stack phase manages its own
        software-trigger streaming + frame callback inside ``zstack()``.  The
        camera is left stopped between FOVs/phases, so this loop owns no camera
        callback of its own.
        """
        run_t0 = time.monotonic()
        # Quiesce live view once for the whole acquisition (restored in finally).
        was_live = bool(self.liveController.is_live)
        # Capture pre-acquisition hardware state so the finally can put the
        # camera/MCU/LiveController back the way the user had them: both phases
        # change the trigger mode, and every z-stack channel apply overwrites
        # the current channel configuration (exposure/gain/illumination).
        prev_trigger_mode = self.liveController.trigger_mode
        prev_configuration = self.liveController.currentConfiguration
        if was_live:
            try:
                self.liveController.stop_live()
            except Exception:
                log.exception("Failed to stop live view before acquisition")
        if self.params.zstack_enabled and self.liveController.trigger_mode != TriggerMode.SOFTWARE:
            self._switch_to_software_trigger_streaming()

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
                self._timepoint_fov_count = 0
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
                            # Drain the finished save results after every FOV (non-blocking): a failed write
                            # requests the abort (_summarize_job_result) and the loop's abort check stops the run.
                            self._summarize_runner_outputs(drain_all=True)
                        self._timepoint_fov_count += 1
                        self._run_state_beat()
        except Exception as e:
            log.exception(e)
            self._abort_due_to_error()  # tags the cause, so the run ends as "error" rather than a user abort
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
            # Recordings whose drain outlived finalize(): wait for them to seal, so .done means the
            # directory is final, and fail the run on a write error that surfaced after finalize().
            try:
                self._settle_draining_recordings(self.DRAIN_SETTLE_TIMEOUT_S)
                if self._draining_writers:
                    raise RuntimeError(
                        f"{len(self._draining_writers)} recording store(s) still being written "
                        f"{self.DRAIN_SETTLE_TIMEOUT_S:g}s after the run ended; not writing .done"
                    )
            except RuntimeError as e:
                log.error(str(e))
                self._abort_due_to_error()
            # Completion marker for downstream watchers (mirrors multipoint's
            # _on_acquisition_completed). Written on abort too: the directory is
            # final either way, and the zarr attrs record completeness.
            if not self._draining_writers:
                try:
                    from control.utils import create_done_file

                    if os.path.isdir(self.experiment_path):
                        create_done_file(self.experiment_path)
                except Exception:
                    log.exception("Failed to write completion marker (.done)")
            self._end_run_state(time.monotonic() - run_t0)
            try:
                self.callbacks.signal_acquisition_finished()
            except Exception:
                log.exception("signal_acquisition_finished callback failed")

    def _switch_to_software_trigger_streaming(self) -> None:
        """Put the z-stack's software trigger in place once, at the start, with the camera streaming.

        A ToupTek camera moves its trigger source off the hardware input only when the trigger mode
        changes on a running stream. Every later mode write in this run happens on a stopped stream
        (each phase stops the camera on the way out), so a hardware-to-software switch made there
        leaves the source on the hardware input and every z-stack trigger is ignored. zstack()'s own
        switch stays as the fallback if this one fails.
        """
        try:
            if not self.camera.get_is_streaming():
                self.camera.start_streaming()
            self.liveController.set_trigger_mode(TriggerMode.SOFTWARE)
        except Exception:
            log.exception("Failed to switch to software trigger before the acquisition")

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
        return bool(self.laser_af.laser_af_properties.has_reference)

    # ---------------------------------------------------------------- record
    def record(self, t_idx: int, region_id, fov_idx: int, z_ref: float) -> int:
        """Record per-plane continuous streams for this FOV, then restore Z.

        Plane j records at ``z_ref + recording_bottom_z_offset_um + j*recording_dz_um``
        (one Zarr store per plane).  The recording channel, achievable fps, and
        dataset frame count are all established once per FOV and shared across
        every plane.  The stage moves to the first plane with illumination OFF,
        illumination then turns on for the remainder of the FOV
        (not toggled between planes), and the camera-mode + stage-Z restore
        always runs on the way out — normal completion, an abort between
        planes, a plane raising the fail-fast ``RuntimeError`` below, or the
        first move / illumination raising.
        Returns the total number of frames emitted across all planes.
        """
        # Apply the recording channel (exposure/gain/illumination settings).
        self._select_config(self.params.recording_channel)

        # The mode switch happens first because toupcam resets its frame-rate
        # strategy on mode change.
        self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
        effective_fps = self._apply_recording_rate()
        T = max(1, frame_count(effective_fps, self.params.duration_s))

        offsets = recording_plane_offsets_um(
            self.params.recording_bottom_z_offset_um, self.params.recording_Nz, self.params.recording_dz_um
        )
        n_planes = len(offsets)

        # Move to the first plane with illumination OFF (the sample must not be
        # illuminated during the Z move + settle). The CONTINUOUS stream does not gate
        # illumination per-frame, and set_microscope_mode only energizes
        # illumination when live (we are not live here), so illumination is
        # turned on explicitly here and stays on across all planes of this FOV
        # (no flicker between planes, fewer MCU commands); _record_one_plane
        # assumes it is already energized.
        total_emitted = 0
        try:
            self._move_z_to_offset(z_ref, offsets[0])
            self.liveController.turn_on_illumination()
            for plane_idx, plane_offset_um in enumerate(offsets):
                if plane_idx > 0:
                    if self.abort_requested_fn():
                        log.info(f"abort requested; skipping recording planes {plane_idx}..{n_planes - 1}")
                        break
                    self._move_z_to_offset(z_ref, plane_offset_um)
                total_emitted += self._record_one_plane(
                    t_idx, region_id, fov_idx, plane_idx, n_planes, plane_offset_um, T
                )
        finally:
            self.liveController.turn_off_illumination()
            # Restore the camera to a software-trigger-friendly state and Z
            # reference. Runs on every exit path — normal completion, the
            # abort break above, and a plane raising the fail-fast
            # RuntimeError below — so the stage is never left at a plane
            # offset. Exceptions here are only logged so they don't mask an
            # in-flight RuntimeError from _record_one_plane.
            try:
                self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
            except Exception:
                log.exception("Failed to restore software-trigger acquisition mode after recording")
            self.move_to_z_level(z_ref)
        return total_emitted

    def _apply_recording_rate(self) -> float:
        """Apply the recording frame rate to the camera and return the rate the recording runs at.

        Sizes the dataset, pacing, and time metadata from the fps the camera can
        actually deliver: a camera clamped below the requested rate (exposure
        limit, PRECISE_FRAMERATE max) can never fill fps*duration frames within
        duration seconds — the run would stall to the timeout and leave the
        trailing planes blank.  The probe runs once per acquisition (the recording
        exposure is fixed, so the achievable rate can't change between FOVs); later
        FOVs just re-assert the cached rate after their own mode switch.  Every
        plane records at the same fps.
        """
        if self._effective_fps is not None:
            try:
                self.camera.set_frame_rate(self._effective_fps)
            except Exception:
                log.exception("failed to re-apply cached frame rate")
            return self._effective_fps

        # The rate was resolved by the controller before the run started
        # (params.effective_fps, also in acquisition.yaml); a caller that built
        # the params by hand falls back to the requested rate.  Applying the
        # hint tells us whether the camera paces itself and, as a safety net,
        # whether it can do even less than resolved.
        target_fps = self.params.effective_fps or self.params.fps
        effective_fps = target_fps
        try:
            achievable_fps = self.camera.set_frame_rate(target_fps)
            if achievable_fps and 0 < achievable_fps < target_fps * (1 - 0.01):
                log.warning(
                    f"camera reports {achievable_fps:.2f} fps, below the resolved {target_fps:g} fps; "
                    f"recording at {achievable_fps:.2f} fps (acquisition.yaml has the resolved value)"
                )
                effective_fps = achievable_fps
        except Exception:
            log.exception("set_frame_rate failed; assuming the resolved fps")
        self._effective_fps = effective_fps
        log.info(f"recording at {effective_fps:g} fps")
        return effective_fps

    def _record_one_plane(
        self,
        t_idx: int,
        region_id,
        fov_idx: int,
        plane_idx: int,
        n_planes: int,
        plane_offset_um: float,
        T: int,
    ) -> int:
        """Record one plane's continuous stream to its own Zarr store.

        Assumes illumination is already on, the fps/mode/T setup already ran in
        ``record()``, and the stage is already at the plane.  Returns the number
        of frames emitted for this plane.
        """
        self._settle_draining_recordings(timeout_s=0.0)  # an earlier plane's late write error fails the run here
        rec_channel = self.params.recording_channel
        effective_fps = self._effective_fps
        out = self._recording_path(t_idx, region_id, fov_idx, plane_idx=plane_idx, n_planes=n_planes)
        y, x, dtype = self._frame_shape
        # requested vs effective fps travel with the data: the store is sized and
        # timed from the rate the camera can deliver, which may be below what the
        # user typed (the GUI warns before starting; this is the on-disk record).
        extra_attrs = {"requested_fps": float(self.params.fps), "effective_fps": float(effective_fps)}
        if n_planes > 1:
            extra_attrs.update(
                {
                    "plane_index": plane_idx,
                    "plane_z_offset_um": plane_offset_um,
                    "n_planes": n_planes,
                }
            )
        cfg = ZarrAcquisitionConfig(
            output_path=out,
            shape=(T, 1, 1, y, x),
            dtype=dtype,
            pixel_size_um=self._pixel_size_um if self._pixel_size_um is not None else 1.0,
            z_step_um=None,
            time_increment_s=(1.0 / effective_fps) if effective_fps and effective_fps > 0 else None,
            channel_names=[rec_channel.name],
            channel_colors=[rec_channel.display_color],
            channel_wavelengths=[None],
            is_hcs=False,
            extra_squid_attrs=extra_attrs,
        )
        writer = RecordingWriter(cfg)
        cap = StreamingCapture(
            ContinuousFrameSource(self.camera, effective_fps, already_configured=True),
            RecordingRouter(effective_fps),  # paced or not is decided from the running-stream rate at start
            CountStop(T),
            writer,
            abort_fn=self.abort_requested_fn,
            display_fn=self._display_frame_fn,
            display_fps=self._display_fps,
        )
        # Generous timeout: enough to gather T frames even at a slow effective rate.
        timeout = self.params.duration_s * 3 + 5
        emitted = cap.run(timeout=timeout)
        log.info(
            f"recording done t={t_idx} region={region_id} fov={fov_idx} "
            f"plane={plane_idx + 1}/{n_planes}: {emitted}/{T} frames"
        )
        if not writer.wait_sealed(0.0):
            # finalize() gave up its join: the drain seals the store when it finishes, and write errors
            # that surface after this point are picked up by the next plane or before .done is written.
            self._draining_writers.append(writer)
        # Fail fast on write errors OR a wedged drain thread OR dropped frames:
        # either way the cause is almost always systematic (full disk, stalled
        # mount), so continuing would burn the timeout at every remaining
        # plane/FOV producing blank data.  A wedged finalize returns before
        # errors are countable, which is why write_error_count alone is not
        # sufficient.  record()'s finally still restores illumination/camera
        # mode/stage-Z before this propagates; run() then catches it, aborts,
        # and signals finished.
        if writer.write_error_count > 0 or writer.finalize_wedged or writer.dropped_count > 0:
            raise RuntimeError(
                f"recording failed at t={t_idx} region={region_id} fov={fov_idx} plane={plane_idx} "
                f"(write errors={writer.write_error_count}, dropped={writer.dropped_count}, "
                f"drain wedged={writer.finalize_wedged}); store sealed incomplete: {out}"
            )
        return emitted

    def _settle_draining_recordings(self, timeout_s: float) -> None:
        """Pick up the recordings whose drain thread outlived their finalize().

        Waits up to timeout_s for them to seal (0 just checks), forgets the sealed ones, and raises
        on one that sealed with write errors: finalize() returned before those surfaced, so this is
        where they fail the run.  Stores still being written stay listed.
        """
        deadline = time.monotonic() + timeout_s
        still_open, failed = [], []
        for writer in self._draining_writers:
            if not writer.wait_sealed(max(0.0, deadline - time.monotonic())):
                still_open.append(writer)
            elif writer.write_error_count > 0:
                failed.append(writer)
        self._draining_writers = still_open
        if failed:
            raise RuntimeError(
                f"{len(failed)} recording(s) failed after finalize() returned "
                f"(first: {failed[0].output_path}, write errors={failed[0].write_error_count}); stores sealed incomplete"
            )

    # ---------------------------------------------------------------- zstack
    def zstack(self, t_idx: int, region_id, fov_idx: int, z_ref: float) -> None:
        """Acquire a software-triggered z-stack via the inherited capture path.

        Each plane/channel goes through ``acquire_camera_image`` -> ``_image_callback``
        -> ``SaveZarrJob`` dispatch.  Restores Z to ``z_ref`` at the end.
        """
        offsets = self._zstack_offsets

        # The inherited capture path (acquire_camera_image) branches on
        # liveController.trigger_mode, so the LiveController and the camera must agree
        # on software-trigger mode.  set_trigger_mode(SOFTWARE) sets both the camera
        # acquisition mode and the microcontroller trigger mode.  Skipped once the
        # mode is already SOFTWARE (every FOV after the first): the camera side is
        # re-asserted by record()'s finally, and re-sending the MCU command per FOV
        # would be a pure no-op round trip.  No per-FOV restore either: run()'s
        # finally restores the user's trigger mode once at the end of the
        # acquisition.  Manage the streaming lifecycle locally so it never
        # interferes with the recording phase's CONTINUOUS streaming.
        if self.liveController.trigger_mode != TriggerMode.SOFTWARE:
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
        current_path = self._zstack_dir(region_id)
        try:
            # The stream is stopped again on the way out, so the next FOV's recording phase
            # starts ContinuousFrameSource on a stopped camera.
            with self._camera_streaming(self._image_callback):
                # Make sure the trigger gate starts open.
                self._ready_for_next_trigger.set()
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
            self.move_to_z_level(z_ref)

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
        """Move to ``z_ref + offset_um`` (offset in µm, z_ref in mm) via the stage.

        Delegates to the inherited ``move_to_z_level`` (blocking stage move +
        Z stabilization sleep, same as MultiPointWorker's scan loop).
        """
        self.move_to_z_level(z_ref + offset_um / 1000.0)

    def _probe_frame_shape(self) -> Tuple[int, int, np.dtype]:
        """Return (Y, X, dtype) for the recording dataset from one real processed frame.

        Frames delivered to callbacks pass through ``_process_raw_frame`` (software
        crop, rotation, ROI), so their shape can differ from ``get_resolution()``
        (the raw binned sensor size).  Sizing the dataset from the sensor size makes
        every ``write_frame`` fail on cropped cameras — a blank recording.

        Probe in CONTINUOUS mode — the same mode the recording phase uses — and read
        one free-run frame, so the probed shape is exactly what recording writes.
        CONTINUOUS needs no ``send_trigger``, so a Live view left in SOFTWARE-trigger
        mode (with an outstanding trigger that makes ``get_ready_for_trigger`` reject
        an immediate trigger) can't block the probe — the failure that previously
        dropped it onto the wrong ``get_resolution`` size and failed every write.
        """
        frame = None
        try:
            self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
            with self._camera_streaming():
                cam_frame = self.camera.read_camera_frame()
                if cam_frame is not None:
                    frame = cam_frame.frame
        except Exception:
            log.exception("probe-frame capture failed")
        if frame is None:
            # No usable frame: sizing from get_resolution() (raw sensor size) would
            # mismatch the cropped delivery and fail every write, so abort with a
            # clear reason instead of grinding out a blank, incomplete recording.
            raise RuntimeError(
                "recording could not capture a probe frame to size the dataset "
                "(camera delivered no frame in CONTINUOUS mode); aborting recording"
            )
        if frame.ndim != 2:
            # A color frame (Y, X, 3) would silently produce a 2-D dataset that
            # every write then fails against — reject it up front.
            raise ValueError(
                f"recording supports monochrome frames only; camera delivered shape {frame.shape} "
                f"(set the camera to a mono pixel format for the recording phase)"
            )
        return int(frame.shape[0]), int(frame.shape[1]), frame.dtype

    def _recording_path(self, t_idx: int, region_id, fov_idx: int, plane_idx: int = 0, n_planes: int = 1) -> str:
        """Per-(t, region, fov[, plane]) recording dataset path under {experiment}/recording.

        Single-plane recordings (n_planes == 1) are named ``fov_{k}.ome.zarr``;
        multi-plane recordings get one store per plane: ``fov_{k}_z{j}.ome.zarr``.
        """
        fov_name = f"fov_{fov_idx}.ome.zarr" if n_planes == 1 else f"fov_{fov_idx}_z{plane_idx}.ome.zarr"
        return os.path.join(self.experiment_path, "recording", f"t{t_idx}", str(region_id), fov_name)

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
        # run() sets _acq_start_time before the timepoint loop, so pacing is
        # always anchored to the acquisition start.
        deadline = self._acq_start_time + t_idx * self.params.dt_s
        if time.monotonic() > deadline:
            log.warning(
                f"skipping time point {t_idx}: per-timepoint work exceeded dt={self.params.dt_s:g}s "
                f"(grid-preserving skip, mirrors MultiPointWorker)"
            )
            return "skip"
        return "run" if self._wait_for_dt(deadline) else "abort"

    def _wait_for_dt(self, deadline: float) -> bool:
        """Sleep until *deadline* (a time.monotonic() timestamp), abort-aware.

        Uses time.monotonic() so an NTP clock step mid-acquisition cannot skew
        or collapse the remaining intervals.  Returns False on abort.
        """
        while time.monotonic() < deadline:
            if self.abort_requested_fn():
                return False
            self._sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return True
