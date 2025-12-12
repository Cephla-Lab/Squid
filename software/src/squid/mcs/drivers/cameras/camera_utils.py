"""Camera utilities and factory function.

This module provides:
- camera_registry: Plugin-style registry for camera implementations
- get_camera(): Factory function to create camera instances based on config
"""

from typing import Optional, Callable

import squid.core.logging
from squid.core.config import CameraConfig, CameraVariant
from squid.core.abc import AbstractCamera
from squid.core.registry import Registry

_log = squid.core.logging.get_logger("squid.camera.utils")

# Camera registry for plugin-style camera implementations
camera_registry = Registry[AbstractCamera]("camera")

# Import simulated cameras to trigger registration
from squid.mcs.drivers.cameras.simulated import (  # noqa: F401, E402
    SimulatedMainCamera as SimulatedCamera,
)
import squid.mcs.drivers.cameras.simulated  # noqa: F401, E402


def get_camera(
    config: CameraConfig,
    simulated: bool = False,
    hw_trigger_fn: Optional[Callable[[Optional[float]], bool]] = None,
    hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]] = None,
) -> AbstractCamera:
    """
    Try to import, and then build, the requested camera.  We import on a case-by-case basis
    because some cameras require system level installations, and so in many cases camera
    driver imports will fail.

    If you're using a camera implementation with hardware trigger mode, you'll need to provide the functions for
    sending a hardware trigger and setting the strobe delay.

    NOTE(imo): While we transition to AbstractCamera, we need to do some hacks here to make the non-transitioned
    drivers still work.  Hence the embedded helpers here.
    """

    def open_if_needed(camera):
        try:
            camera.open()
        except AttributeError:
            pass

    if simulated:
        # Select appropriate simulated camera based on config
        camera_type = "simulated_focus" if config.is_focus_camera else "simulated_main"
        return camera_registry.create(
            camera_type,
            config,
            hw_trigger_fn=hw_trigger_fn,
            hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
        )

    try:
        if config.camera_type == CameraVariant.TOUPCAM:
            import squid.mcs.drivers.cameras.toupcam

            camera = squid.mcs.drivers.cameras.toupcam.ToupcamCamera(
                config,
                hw_trigger_fn=hw_trigger_fn,
                hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
            )
        elif config.camera_type == CameraVariant.FLIR:
            import squid.mcs.drivers.cameras.flir

            camera = squid.mcs.drivers.cameras.flir.Camera(config)
        elif config.camera_type == CameraVariant.HAMAMATSU:
            import squid.mcs.drivers.cameras.hamamatsu

            camera = squid.mcs.drivers.cameras.hamamatsu.HamamatsuCamera(
                config,
                hw_trigger_fn=hw_trigger_fn,
                hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
            )
        elif config.camera_type == CameraVariant.IDS:
            import squid.mcs.drivers.cameras.ids

            camera = squid.mcs.drivers.cameras.ids.Camera(config)
        elif config.camera_type == CameraVariant.TUCSEN:
            import squid.mcs.drivers.cameras.tucsen

            camera = squid.mcs.drivers.cameras.tucsen.TucsenCamera(
                config,
                hw_trigger_fn=hw_trigger_fn,
                hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
            )
        elif config.camera_type == CameraVariant.PHOTOMETRICS:
            import squid.mcs.drivers.cameras.photometrics

            camera = squid.mcs.drivers.cameras.photometrics.PhotometricsCamera(
                config,
                hw_trigger_fn=hw_trigger_fn,
                hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
            )
        elif config.camera_type == CameraVariant.ANDOR:
            import squid.mcs.drivers.cameras.andor

            camera = squid.mcs.drivers.cameras.andor.AndorCamera(
                config,
                hw_trigger_fn=hw_trigger_fn,
                hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
            )
        elif config.camera_type == CameraVariant.TIS:
            import squid.mcs.drivers.cameras.tis

            camera = squid.mcs.drivers.cameras.tis.Camera(config)
        else:
            import squid.mcs.drivers.cameras.base

            camera = squid.mcs.drivers.cameras.base.DefaultCamera(
                config,
                hw_trigger_fn=hw_trigger_fn,
                hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
            )

        # NOTE(imo): All of these things are hacks before complete migration to AbstractCamera impls.  They can
        # be removed once all the cameras conform to the AbstractCamera interface.
        open_if_needed(camera)

        return camera
    except ImportError as e:
        _log.warning(
            f"Camera of type: '{config.camera_type}' failed to import.  Falling back to default camera impl."
        )
        _log.warning(e)

        import squid.mcs.drivers.cameras.base

        return squid.mcs.drivers.cameras.base.DefaultCamera(
            config,
            hw_trigger_fn=hw_trigger_fn,
            hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn,
        )
