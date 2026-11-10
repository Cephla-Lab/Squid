"""RecordZStackWorker — per-FOV orchestration for "Time at XY" acquisitions.

For each (time point, region, FOV) the worker:
  1. moves XY to the FOV (and to the focus-map z when the coordinate carries one),
  2. establishes a Z reference: laser AF when ticked and referenced, else contrast
     AF when ticked and the stack is a single plane or centred (Relative), else
     the current stage Z,
  3. (Zi) acquires a software-triggered z-stack over the initial-stack channels,
  4. (R)  records one continuous high-fps stream per recording channel, in table
          order, at the selected subset (Bottom / Mid / Top / explicit) of the
          same stack planes,
  5. (Zf) acquires a software-triggered z-stack over the final-stack channels.

One ``ZStackPlan`` defines the planes for all three phases.  Output layout under
``{experiment}``::

    zstack_initial/zarr/{region}/fov_{k}.ome.zarr      (Zi; via SaveZarrJob)
    recording/t{t}/{region}/{channel}/fov_{k}_z{i}.ome.zarr   (R; one store per recorded
                                                               plane, i = stack plane index)
    zstack_final/zarr/{region}/fov_{k}.ome.zarr        (Zf; via SaveZarrJob)

A row can pin its own binning, centred crop and pixel format
(``control.core.camera_geometry``). Stack channels whose geometry differs can't
share one (T, C, Z, Y, X) store, so a phase is split into one group per
geometry, acquired one group after another. With more than one group, each
store gains a level: ``zstack_{phase}/{group}/zarr/...``, where ``group`` is
e.g. ``bin2_1024x1024_mono8``. A single group keeps the layout above.

The recording phase reuses the ``StreamingCapture`` primitive
(``ContinuousFrameSource`` + ``RecordingRouter`` + ``CountStop`` +
``RecordingWriter``).  The z-stack phases reuse ``MultiPointWorkerBase``'s
shared single-frame capture + frame callback + job dispatch machinery, so the
stacks are saved in this process by an ``InProcessZarrRunner`` behind the same
``BackpressureController`` ``MultiPointWorker`` uses.  Every z-stack store
shares that one runner: its ``ZarrWriterInfo`` is swapped between groups and
phases (``_activate_zarr_writer_info``), which is safe because each group's
capture waits for every in-flight dispatch before returning, and the runner
keeps one open writer per store until the end of the run seals them all.
"""

import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

import squid.logging
import control._def  # module access: MULTIPOINT_AUTOFOCUS_CHANNEL is rebound at runtime by Preferences
from control._def import (
    SCAN_STABILIZATION_TIME_MS_X,
    SCAN_STABILIZATION_TIME_MS_Y,
    SCAN_STABILIZATION_TIME_MS_Z,
    TriggerMode,
)
from squid.abc import CameraAcquisitionMode
from control.core.acquisition_setup import compute_pixel_size_um
from control.core.camera_geometry import (
    CameraGeometry,
    CameraSnapshot,
    ResolvedGeometry,
    apply_geometry,
    pixel_size_for,
    resolve,
    restore_snapshot,
    snapshot_camera,
)
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
    ChannelPlan,
    RecordZStackAcquisitionParameters,
    ZSTACK_PHASE_FINAL,
    ZSTACK_PHASE_INITIAL,
    contrast_af_applies,
    frame_count,
    recording_dirnames,
    recording_offsets_for_plan,
    resolve_effective_fps,
    zstack_offsets_for_mode,
    zstack_plane_count_for_plan,
    zstack_subdir,
)
from control.core.job_processing import (
    AcquisitionInfo,
    SaveZarrJob,
    ZarrWriterInfo,
)
from control.core.backpressure import BackpressureController

log = squid.logging.get_logger("RecordZStackWorker")


@dataclass
class StackGroup:
    """Stack channels that share one camera geometry, and the store they are written to."""

    geometry: Optional[ResolvedGeometry]  # None when the camera state could not be read
    channels: List
    info: ZarrWriterInfo
    subdir: str  # experiment-relative directory of this group's stores


class RecordZStackWorker(MultiPointWorkerBase):
    """Per-FOV Zi-stack / recordings / Zf-stack acquisition worker.

    Scan coords are supplied as ``{region_id: [(x_mm, y_mm[, z_mm]), ...]}``.
    """

    # How long the end of the run waits for a recording whose drain outlived its finalize() before
    # declaring the store not final: the same budget finalize() itself had.
    DRAIN_SETTLE_TIMEOUT_S = RecordingWriter.FINALIZE_TIMEOUT_S
    # How long the frame-shape probe waits for a frame delivered by its own stream before settling
    # for the camera's latest one.
    PROBE_FRESH_FRAME_TIMEOUT_S = 2.0

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
        auto_focus_controller=None,
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
        self.autofocusController = auto_focus_controller
        self._region_laser_af_offsets: Dict[object, float] = dict(params.region_laser_af_offsets or {})
        # Counted by establish_reference(); run() resets it at the start of each run.
        self.laser_af_fallbacks = 0
        self.objectiveStore = objective_store
        self._scan: Dict[object, List[Tuple]] = scan_region_fov_coords or {}

        # Experiment output layout (mirrors MultiPointWorker.experiment_path).
        self.base_path = params.base_path
        self.experiment_ID = params.experiment_id
        self.experiment_path = os.path.join(self.base_path or "", self.experiment_ID or "")

        # Per-phase channel lists, in table order.
        self.zi_channels: List = list(params.zi_channels)
        self.r_plans: List[ChannelPlan] = list(params.r_plans)
        self.zf_channels: List = list(params.zf_channels)
        any_stack = bool(self.zi_channels or self.zf_channels)

        # Camera geometry at the start of the run: rows that leave binning / crop /
        # pixel format unset inherit it, and run() puts it back at the end.
        self._camera_snapshot: Optional[CameraSnapshot] = None
        try:
            self._camera_snapshot = snapshot_camera(self.camera)
        except Exception:
            if any(not p.geometry.is_inherit for p in params.channels if p.active):
                raise RuntimeError("could not read the camera binning/crop/format, so per-channel settings can't apply")
            log.exception("Could not snapshot the camera geometry; using it as is")
        self._inherit_geometry: Optional[ResolvedGeometry] = self._resolve(CameraGeometry())
        # What the camera is set to now; _apply_geometry skips a no-op change.
        self._applied_geometry: Optional[ResolvedGeometry] = self._inherit_geometry

        # Every phase shares one plane count (the plan is z_ref-independent);
        # the per-FOV offsets are computed in zstack()/record() because Set
        # Range depends on the FOV's z reference.
        self._NZ = zstack_plane_count_for_plan(params.zstack) if (any_stack or self.r_plans) else 1
        self._record_slices: List[int] = params.record_slices.resolve(self._NZ)

        # Pre-compute acquisition-wide metadata (pixel size etc.) for zarr/job info.
        self._pixel_size_um = compute_pixel_size_um(self.objectiveStore, self.camera)

        self._time_increment_s = params.dt_s if params.Nt > 1 and params.dt_s > 0 else None
        self._physical_size_z_um = abs(params.zstack.dz_um) if self._NZ > 1 else None

        # AcquisitionInfo is only consumed by the OME-TIFF jobs; build it once
        # over the union of stack channels so it stays informative.
        union_names: List[str] = []
        for c in self.zi_channels + self.zf_channels:
            if c.name not in union_names:
                union_names.append(c.name)
        self.acquisition_info = AcquisitionInfo(
            total_time_points=params.Nt,
            total_z_levels=self._NZ,
            total_channels=len(union_names),
            channel_names=union_names,
            experiment_path=self.experiment_path,
            time_increment_s=self._time_increment_s,
            physical_size_z_um=self._physical_size_z_um,
            physical_size_x_um=self._pixel_size_um,
            physical_size_y_um=self._pixel_size_um,
        )

        # One ZarrWriterInfo per stack phase and geometry group (separate stores);
        # the single job runner is pointed at the right one before each group.
        self._zi_groups: List[StackGroup] = self._make_stack_groups(
            [p for p in params.channels if p.zi], ZSTACK_PHASE_INITIAL
        )
        self._zf_groups: List[StackGroup] = self._make_stack_groups(
            [p for p in params.channels if p.zf], ZSTACK_PHASE_FINAL
        )

        # Recording output directory per R row (sanitized, unique, row order).
        self._recording_dirnames: List[str] = recording_dirnames([p.name for p in self.r_plans])

        # Probing captures a frame, so it happens in record() after live view stops
        # and the row's geometry is applied. One probe per geometry.
        self._frame_shape: Optional[Tuple[int, int, np.dtype]] = None
        self._frame_shapes: Dict[Optional[ResolvedGeometry], Tuple[int, int, np.dtype]] = {}
        self._recording_pixel_size_um: Optional[float] = self._pixel_size_um
        # Recordings whose drain thread outlived their finalize(): see _settle_draining_recordings.
        self._draining_writers: List[RecordingWriter] = []
        # Rate each R row records at, by channel name: resolved on the first FOV the
        # row records at (its exposure and geometry are fixed for the acquisition, so
        # the camera's clamp can't change between FOVs) and re-asserted afterwards.
        # Whether the camera paces itself at that rate is NOT decided here: that is
        # read from the running stream, per recording, inside StreamingCapture.run.
        self._fps_cache: Dict[str, float] = {}
        # Set at the top of run(); _wait_for_dt paces timepoint STARTS from it.
        self._acq_start_time: Optional[float] = None

        if any_stack:
            self._setup_zstack_job_runner()
        else:
            # Still need a (disabled) backpressure controller so base methods that
            # reference self._backpressure don't crash if ever reached.
            self._backpressure = BackpressureController(enabled=False)

    # ------------------------------------------------------------------ setup
    def _resolve(self, geometry: CameraGeometry) -> Optional[ResolvedGeometry]:
        if self._camera_snapshot is None:
            return None
        return resolve(geometry, self._camera_snapshot)

    def _pixel_size_for(self, resolved: Optional[ResolvedGeometry]) -> Optional[float]:
        if resolved is None or resolved.binning == self._camera_snapshot.binning[0]:
            return self._pixel_size_um
        return pixel_size_for(self.objectiveStore, self.camera, resolved.binning)

    def _make_stack_groups(self, plans: List[ChannelPlan], phase: str) -> List[StackGroup]:
        """Split one phase's stack rows into geometry groups, in order of first appearance."""
        by_geometry: Dict[Optional[ResolvedGeometry], List] = {}
        for plan in plans:
            by_geometry.setdefault(self._resolve(plan.geometry), []).append(plan.channel)
        phase_dir = zstack_subdir(phase)
        groups = []
        for geometry, channels in by_geometry.items():
            subdir = phase_dir if len(by_geometry) == 1 else os.path.join(phase_dir, geometry.dir_name())
            info = self._make_zarr_writer_info(channels, subdir, self._pixel_size_for(geometry))
            groups.append(StackGroup(geometry=geometry, channels=channels, info=info, subdir=subdir))
        if len(groups) > 1:
            log.info(
                f"{phase} z-stack: {len(groups)} camera geometries, saved to separate stores: "
                + "; ".join(f"{g.subdir} ({', '.join(c.name for c in g.channels)})" for g in groups)
            )
        return groups

    def _apply_geometry(self, resolved: Optional[ResolvedGeometry]) -> None:
        """Put the camera in *resolved* geometry. Camera must be stopped; no-op if already there."""
        if resolved is None or resolved == self._applied_geometry:
            return
        apply_geometry(self.camera, resolved, self._camera_snapshot)
        self._applied_geometry = resolved
        # A binning / pixel-format change can reset what the camera holds for the
        # channel (exposure, gain), so the next image re-applies its channel.
        self._applied_config = None

    def _make_zarr_writer_info(
        self, channels: List, subdir: str, pixel_size_um: Optional[float] = None
    ) -> ZarrWriterInfo:
        """ZarrWriterInfo for one stack phase, rooted at ``{experiment}/{subdir}``.

        SaveZarrJob derives the store path as ``{base_path}/zarr/{region}/fov_{k}.ome.zarr``
        (control.utils.build_per_fov_zarr_path), so the ``zarr/`` level is implied.
        """
        channel_names, channel_colors, channel_wavelengths = self._channel_zarr_metadata(channels)
        # FOV counts per region (non-HCS per-FOV 5D output -> count only used for 6D).
        region_fov_counts = {str(region_id): len(coords) for region_id, coords in self._scan.items()}
        return ZarrWriterInfo(
            base_path=os.path.join(self.experiment_path, subdir),
            t_size=self.params.Nt,
            c_size=len(channels),
            z_size=self._NZ,
            is_hcs=False,
            use_6d_fov=False,
            region_fov_counts=region_fov_counts,
            pixel_size_um=pixel_size_um if pixel_size_um is not None else self._pixel_size_um,
            z_step_um=self._physical_size_z_um,
            time_increment_s=self._time_increment_s,
            channel_names=channel_names,
            channel_colors=channel_colors,
            channel_wavelengths=channel_wavelengths,
        )

    def _setup_zstack_job_runner(self) -> None:
        self._backpressure = self._create_backpressure()
        first_group = (self._zi_groups or self._zf_groups)[0]
        self._job_runners = [(SaveZarrJob, self._create_in_process_zarr_runner(first_group.info))]

    def _activate_zarr_writer_info(self, info: ZarrWriterInfo) -> None:
        """Point the (single) job runner at the store for the group about to run.

        Only called between groups / phases: ``_zstack_group`` waits for every
        in-flight dispatch before returning, so no frame of the previous group can
        still be waiting to pick up the swapped info.
        """
        for _job_cls, runner in self._job_runners:
            if runner is not None:
                runner.set_zarr_writer_info(info)

    # -------------------------------------------------------------------- run
    def run(self):
        """Top-level orchestration loop (runs on the acquisition thread).

        Recording manages its own CONTINUOUS streaming/callback via
        ``ContinuousFrameSource``; the z-stack phases manage their own
        software-trigger streaming + frame callback inside ``zstack()``.  The
        camera is left stopped between FOVs/phases, so this loop owns no camera
        callback of its own.
        """
        run_t0 = time.monotonic()
        # Quiesce live view once for the whole acquisition (restored in finally).
        was_live = bool(self.liveController.is_live)
        # Capture pre-acquisition hardware state so the finally can put the
        # camera/MCU/LiveController back the way the user had them: every phase
        # changes the trigger mode, and every channel apply overwrites the
        # current channel configuration (exposure/gain/illumination).
        prev_trigger_mode = self.liveController.trigger_mode
        prev_configuration = self.liveController.currentConfiguration
        if was_live:
            try:
                self.liveController.stop_live()
            except Exception:
                log.exception("Failed to stop live view before acquisition")

        self.laser_af_fallbacks = 0
        try:
            # Size the recording datasets from one real processed frame per camera setting,
            # before anything is acquired.  Deferred to here (not __init__) so the probe
            # capture only touches the camera after live view has been stopped.
            self._probe_recording_frame_shapes()

            if self._job_runners:
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
                        z_ref = self.establish_reference(region_id)
                        # Whatever ran to find the reference (laser AF, the contrast AF
                        # channel) may have changed the channel behind our back.
                        self._applied_config = None
                        if self.abort_requested_fn():
                            return

                        if self.zi_channels:
                            self.zstack(t_idx, region_id, fov_idx, z_ref, phase=ZSTACK_PHASE_INITIAL)
                            # Drain the finished save results after every stack (non-blocking): a failed write
                            # requests the abort (_summarize_job_result) and the abort checks below stop the run.
                            self._summarize_runner_outputs(drain_all=True)
                        for rec_idx, plan in enumerate(self.r_plans):
                            if self.abort_requested_fn():
                                return
                            self.record(t_idx, region_id, fov_idx, z_ref, plan, rec_idx)
                        if self.zf_channels:
                            if self.abort_requested_fn():
                                return
                            self.zstack(t_idx, region_id, fov_idx, z_ref, phase=ZSTACK_PHASE_FINAL)
                            self._summarize_runner_outputs(drain_all=True)
                        self._timepoint_fov_count += 1
                        self._run_state_beat()
        except Exception as e:
            log.exception(e)
            self._abort_due_to_error()  # tags the cause, so the run ends as "error" rather than a user abort
        finally:
            if self.laser_af_fallbacks:
                log.warning(
                    f"Laser AF fell back to the current Z at {self.laser_af_fallbacks} FOV visit(s); "
                    "those planes were referenced to the carried Z, not to the AF reference"
                )
            # Drain + shut down z-stack job runners (also closes backpressure).
            if self._job_runners:
                try:
                    self._finish_jobs()
                except Exception:
                    log.exception("Error finishing z-stack jobs")
            # Put back the binning / crop / pixel format the user had, before live
            # view restarts (and before a smart-ring survey reuses the camera).
            if self._applied_geometry != self._inherit_geometry:
                try:
                    self.camera.stop_streaming()
                    restore_snapshot(self.camera, self._camera_snapshot)
                    self._applied_geometry = self._inherit_geometry
                except Exception:
                    log.exception("Failed to restore the camera binning/crop/pixel format after acquisition")
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

    # ------------------------------------------------------------- reference
    def establish_reference(self, region_id=None) -> float:
        """Return the Z reference (mm) for this FOV.

        ``_move_xy`` has already applied any focus-map z carried by the coordinate.
        Laser AF (``move_to_target(0)`` plus the region's offset) takes precedence
        over contrast AF, as on the multipoint tabs; contrast AF only runs when the
        stack is a single plane or centred (Relative).  Any failure logs and falls
        back to the current stage Z.
        """
        if self.params.use_laser_af and self.laser_af is not None and self._laser_af_has_reference():
            try:
                # Anchor closed-loop at the reference (displacement 0), then apply the
                # per-region offset as an open-loop relative move, exactly as
                # MultiPointWorker.perform_autofocus does.
                ok = self.laser_af.move_to_target(0.0)
                if ok:
                    offset_um = float(self._region_laser_af_offsets.get(region_id, 0.0) or 0.0)
                    if offset_um:
                        log.info(f"applying per-region laser AF offset for region '{region_id}': {offset_um:+.2f} µm")
                        self.laser_af.apply_relative_offset_um(offset_um)
                else:
                    self.laser_af_fallbacks += 1
                    log.warning("laser AF move_to_target(0) reported failure; using current Z")
            except Exception as e:
                self.laser_af_fallbacks += 1
                log.warning(f"laser AF failed at FOV, falling back to current Z: {e}")
        elif self._contrast_af_enabled():
            self._run_contrast_autofocus()
        return self.stage.get_pos().z_mm

    def _laser_af_has_reference(self) -> bool:
        return bool(self.laser_af.laser_af_properties.has_reference)

    def _contrast_af_enabled(self) -> bool:
        return self.autofocusController is not None and contrast_af_applies(self.params)

    def _run_contrast_autofocus(self) -> bool:
        """Run the shared AutoFocusController's contrast sweep at this FOV.

        ``AutofocusWorker`` triggers and reads frames itself, branching on
        ``liveController.trigger_mode``, and real cameras only deliver frames
        while streaming — so the camera is put in SOFTWARE-trigger mode, the AF
        channel is applied, streaming is started for the sweep and stopped
        again afterwards (every phase expects a stopped camera).  The sweep
        parks the stage on the best-focus plane and leaves illumination off.
        Runs at every FOV visit (no NUMBER_OF_FOVS_PER_AF throttle: this tab has
        few positions and long dwell).  Returns True when a sweep ran.
        """
        objective = self.objectiveStore.current_objective
        af_channel_name = control._def.MULTIPOINT_AUTOFOCUS_CHANNEL
        try:
            config_af = self.liveController.get_channel_by_name(objective, af_channel_name)
        except Exception:
            log.exception("contrast AF: could not look up the AF channel")
            config_af = None
        if config_af is None:
            log.error(f"contrast AF channel {af_channel_name!r} missing for objective {objective!r}; skipping AF")
            return False
        if self.liveController.trigger_mode != TriggerMode.SOFTWARE:
            try:
                self.liveController.set_trigger_mode(TriggerMode.SOFTWARE)
            except Exception:
                log.exception("contrast AF: failed to set software-trigger mode; skipping AF")
                return False
        # The sweep runs at the user's own camera geometry, not the last row's.
        self._apply_geometry(self._inherit_geometry)
        self._select_config(config_af)
        try:
            with self._camera_streaming():
                self.autofocusController.autofocus(True)
                self.autofocusController.wait_till_autofocus_has_completed()
        except Exception:
            log.exception("contrast autofocus failed; using current Z")
            return False
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
        return True

    # ---------------------------------------------------------------- record
    def _probe_recording_frame_shapes(self) -> None:
        """Probe the delivered frame shape for every R row, one probe per distinct camera setting.

        Run before the first FOV so a camera that cannot deliver a usable frame at a row's
        settings (no frame, or a colour frame) fails the run with a clear reason up front,
        not after the first initial z-stack has been acquired.
        """
        for plan in self.r_plans:
            resolved = self._resolve(plan.geometry)
            if resolved not in self._frame_shapes:
                self._apply_geometry(resolved)
                self._frame_shapes[resolved] = self._probe_frame_shape()

    def _apply_recording_rate(self, plan: ChannelPlan) -> float:
        """Apply *plan*'s recording frame rate to the camera and return the rate it records at.

        Sizes the dataset, pacing, and time metadata from the fps the camera can
        actually deliver: a camera clamped below the requested rate (exposure
        limit, PRECISE_FRAMERATE max) can never fill fps*duration frames within
        duration seconds — the run would stall to the timeout and leave the
        trailing planes blank.  The probe runs once per row per acquisition (a
        row's exposure and geometry are fixed, so the achievable rate can't change
        between FOVs); later FOVs just re-assert the cached rate after their own
        mode switch.  Every plane of a row records at the same fps.  The caller has
        already applied the row's geometry and channel and switched the camera to
        CONTINUOUS mode (toupcam resets its frame-rate strategy on mode change).
        """
        cached = self._fps_cache.get(plan.name)
        if cached is not None:
            try:
                self.camera.set_frame_rate(cached)
            except Exception:
                log.exception("failed to re-apply cached frame rate")
            return cached

        # The rate was resolved by the controller before the run started
        # (plan.effective_fps, also in acquisition.yaml).  A row that pins its own
        # camera geometry could only be resolved now that the camera is in it, and
        # a caller that built the params by hand gets the same resolution here.
        # Applying the hint is a safety net: the camera may do even less than resolved.
        target_fps = plan.effective_fps
        if target_fps is None:
            exposure_ms = plan.channel.camera_settings.exposure_time_ms
            target_fps = resolve_effective_fps(self.camera, plan.fps, exposure_ms)
            if target_fps < plan.fps * (1 - 1e-6):
                log.warning(
                    f"{plan.name}: camera cannot deliver {plan.fps:g} fps at {exposure_ms:g} ms exposure "
                    f"(max {target_fps:.2f}); recording at {target_fps:.2f} fps"
                )
        effective_fps = target_fps
        try:
            achievable_fps = self.camera.set_frame_rate(target_fps)
            if achievable_fps and 0 < achievable_fps < target_fps * (1 - 0.01):
                log.warning(
                    f"{plan.name}: camera reports {achievable_fps:.2f} fps, below the resolved {target_fps:g} fps; "
                    f"recording at {achievable_fps:.2f} fps (acquisition.yaml has the resolved value)"
                )
                effective_fps = achievable_fps
        except Exception:
            log.exception("set_frame_rate failed; assuming the resolved fps")
        self._fps_cache[plan.name] = effective_fps
        log.info(f"{plan.name}: recording at {effective_fps:g} fps")
        return effective_fps

    def record(self, t_idx: int, region_id, fov_idx: int, z_ref: float, plan: ChannelPlan, rec_idx: int) -> int:
        """Record per-plane continuous streams for one R row at this FOV, then restore Z.

        The planes are the selected subset of the shared stack planes
        (``recording_offsets_for_plan``), one Zarr store per plane.  The
        channel, achievable fps, and dataset frame count are all established
        once per call and shared across every plane.  The stage moves to the
        first plane with illumination OFF (the sample must not be illuminated
        during the Z move + settle), illumination then turns on for the
        remainder of this recording (not toggled between planes), and the
        camera-mode + stage-Z restore always runs on the way out — normal
        completion, an abort between planes, a plane raising the fail-fast
        ``RuntimeError`` below, or the first move / illumination raising — so
        the next recording or stack starts from a clean state.  Returns the
        total number of frames emitted across all planes.
        """
        planes = recording_offsets_for_plan(self.params.zstack, self.params.record_slices, z_ref)
        if not planes:
            log.warning(f"{plan.name}: no recording planes selected; skipping recording")
            return 0
        n_planes = len(planes)

        resolved = self._resolve(plan.geometry)
        self._apply_geometry(resolved)
        # This row's datasets are sized from one real processed frame at its geometry:
        # probed at the start of run() (see _probe_recording_frame_shapes), or here for
        # a caller that records without run(); cached, since a row's geometry is fixed.
        shape = self._frame_shapes.get(resolved)
        if shape is None:
            shape = self._frame_shapes[resolved] = self._probe_frame_shape()
        self._frame_shape = shape
        self._recording_pixel_size_um = self._pixel_size_for(resolved)
        # Apply the recording channel (exposure/gain/illumination settings).
        self._select_config(plan.channel)

        # The mode switch happens first because toupcam resets its frame-rate
        # strategy on mode change.
        self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
        effective_fps = self._apply_recording_rate(plan)
        T = max(1, frame_count(effective_fps, plan.duration_s))

        # Move to the first plane with illumination OFF (the sample must not be
        # illuminated during the Z move + settle). The CONTINUOUS stream does not gate
        # illumination per-frame, and set_microscope_mode only energizes
        # illumination when live (we are not live here), so illumination is
        # turned on explicitly here and stays on across all planes of this
        # recording (no flicker between planes, fewer MCU commands);
        # _record_one_plane assumes it is already energized.
        total_emitted = 0
        try:
            self._move_z_to_offset(z_ref, planes[0][1])
            self.liveController.turn_on_illumination()
            for order, (plane_index, plane_offset_um) in enumerate(planes):
                if order > 0:
                    if self.abort_requested_fn():
                        log.info(f"abort requested; skipping recording planes {order}..{n_planes - 1}")
                        break
                    self._move_z_to_offset(z_ref, plane_offset_um)
                total_emitted += self._record_one_plane(
                    t_idx, region_id, fov_idx, plane_index, n_planes, plane_offset_um, effective_fps, T, plan, rec_idx
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

    def _record_one_plane(
        self,
        t_idx: int,
        region_id,
        fov_idx: int,
        plane_idx: int,
        n_planes: int,
        plane_offset_um: float,
        effective_fps: float,
        T: int,
        plan: ChannelPlan,
        rec_idx: int,
    ) -> int:
        """Record one plane's continuous stream to its own Zarr store.

        ``plane_idx`` is the index into the shared stack (so the store lines up
        with the corresponding z-stack plane); ``n_planes`` is how many planes
        this recording visits.  Assumes illumination is already on, the
        fps/mode/T setup already ran in ``record()``, and the stage is already
        at the plane.  Returns the number of frames emitted for this plane.
        """
        self._settle_draining_recordings(timeout_s=0.0)  # an earlier plane's late write error fails the run here
        out = self._recording_path(t_idx, region_id, fov_idx, self._recording_dirnames[rec_idx], plane_idx)
        y, x, dtype = self._frame_shape

        rec_channel = plan.channel
        # requested vs effective fps travel with the data: the store is sized and
        # timed from the rate the camera can deliver, which may be below what the
        # user typed (the GUI warns before starting; this is the on-disk record).
        extra_attrs = {
            "requested_fps": float(plan.fps),
            "effective_fps": float(effective_fps),
            "plane_index": plane_idx,
            "plane_z_offset_um": plane_offset_um,
            "n_planes": n_planes,
            "stack_planes": self._NZ,
        }
        cfg = ZarrAcquisitionConfig(
            output_path=out,
            shape=(T, 1, 1, y, x),
            dtype=dtype,
            pixel_size_um=self._recording_pixel_size_um if self._recording_pixel_size_um is not None else 1.0,
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
        timeout = plan.duration_s * 3 + 5
        emitted = cap.run(timeout=timeout)
        log.info(
            f"recording done t={t_idx} region={region_id} fov={fov_idx} channel={plan.name} "
            f"plane={plane_idx + 1}/{self._NZ}: {emitted}/{T} frames"
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
                f"recording failed at t={t_idx} region={region_id} fov={fov_idx} channel={plan.name} "
                f"plane={plane_idx} (write errors={writer.write_error_count}, dropped={writer.dropped_count}, "
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
    def zstack(self, t_idx: int, region_id, fov_idx: int, z_ref: float, phase: str) -> None:
        """Acquire one software-triggered z-stack (``phase`` = "initial" | "final").

        Each plane/channel goes through ``acquire_camera_image`` -> ``_image_callback``
        -> ``SaveZarrJob`` dispatch into that phase's store.  Restores Z to
        ``z_ref`` at the end.
        """
        if phase == ZSTACK_PHASE_INITIAL:
            groups = self._zi_groups
        elif phase == ZSTACK_PHASE_FINAL:
            groups = self._zf_groups
        else:
            raise ValueError(f"unknown z-stack phase {phase!r}")
        if not groups:
            return

        offsets = zstack_offsets_for_mode(self.params.zstack, z_ref)

        # The inherited capture path (acquire_camera_image) branches on
        # liveController.trigger_mode, so the LiveController and the camera must agree
        # on software-trigger mode.  set_trigger_mode(SOFTWARE) sets both the camera
        # acquisition mode and the microcontroller trigger mode.  Skipped once the
        # mode is already SOFTWARE (every phase after the first): the camera side is
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
        # Groups run one after another: each needs its own geometry and store, and
        # the camera must be stopped to change geometry. A single group is exactly
        # the old one-pass stack.
        for group in groups:
            if self.abort_requested_fn():
                return
            self._zstack_group(group, offsets, region_id, fov_idx, z_ref, phase)

    def _zstack_group(self, group: StackGroup, offsets, region_id, fov_idx: int, z_ref: float, phase: str) -> None:
        """Acquire one geometry group's stack (z outer, channel inner) into its store."""
        self._activate_zarr_writer_info(group.info)
        self._apply_geometry(group.geometry)
        current_path = self._zstack_dir(region_id, phase, group.subdir)
        try:
            # The stream is stopped again on the way out (after the last in-flight frame has been
            # dispatched), so the next group, phase or recording starts on a stopped camera and no
            # dispatch of this group can still be waiting when the writer info is swapped.
            with self._camera_streaming(self._image_callback):
                # Make sure the trigger gate starts open.
                self._ready_for_next_trigger.set()
                for z_idx, off_um in enumerate(offsets):
                    self._move_z_to_offset(z_ref, off_um)
                    for c_idx, config in enumerate(group.channels):
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

        The probe runs once per camera setting, so the camera's "latest frame" can
        be one from before this stream started, with the previous setting's shape
        (the simulated camera keeps its last frame across a binning change).  A
        frame stamped before the stream started is therefore passed over, for up
        to ``PROBE_FRESH_FRAME_TIMEOUT_S``; after that the latest frame is used as
        it was before, rather than failing on a camera whose timestamps are not
        host time.
        """
        frame = None
        try:
            self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
            stream_start = time.time()
            deadline = time.monotonic() + self.PROBE_FRESH_FRAME_TIMEOUT_S
            with self._camera_streaming():
                while True:
                    cam_frame = self.camera.read_camera_frame()
                    if cam_frame is None:
                        break
                    frame = cam_frame.frame
                    timestamp = getattr(cam_frame, "timestamp", None)
                    if timestamp is None or timestamp >= stream_start or time.monotonic() >= deadline:
                        break
                    self._sleep(0.005)
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

    def _recording_path(self, t_idx: int, region_id, fov_idx: int, channel_dirname: str, plane_idx: int) -> str:
        """Per-(t, region, channel, fov, plane) recording dataset path under {experiment}/recording.

        ``plane_idx`` is the shared stack's plane index, so ``fov_{k}_z{i}`` lines
        up with plane ``i`` of the Zi/Zf stacks.  The channel directory is always
        present so several R rows never collide.
        """
        return os.path.join(
            self.experiment_path,
            "recording",
            f"t{t_idx}",
            str(region_id),
            channel_dirname,
            f"fov_{fov_idx}_z{plane_idx}.ome.zarr",
        )

    def _zstack_dir(self, region_id, phase: str, subdir: Optional[str] = None) -> str:
        """Directory passed as ``current_path`` to the inherited capture path.

        SaveZarrJob ignores this (it builds its own path from ZarrWriterInfo), but
        SaveImageJob/SaveOMETiffJob would use it, so keep it valid and per group.
        """
        return os.path.join(self.experiment_path, subdir or zstack_subdir(phase), str(region_id))

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
