"""
Application context for dependency management.

Centralizes creation of microscope and controllers, replacing the
pattern where GUI creates and owns everything.

Usage:
    context = ApplicationContext(simulation=True)
    gui = context.create_gui()
    gui.show()

    # Later:
    context.shutdown()
"""
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import squid.logging

if TYPE_CHECKING:
    from control.microscope import Microscope
    from control.core.live_controller import LiveController
    from control.core.stream_handler import StreamHandler
    from control.core.multi_point_controller import MultiPointController
    from control.core.channel_configuration_mananger import ChannelConfigurationManager
    from control.core.objective_store import ObjectiveStore


@dataclass
class Controllers:
    """
    Container for all controllers.

    This replaces the pattern where GUI has 20+ instance variables
    for different controllers.
    """
    live: "LiveController"
    stream_handler: "StreamHandler"
    multipoint: Optional["MultiPointController"] = None
    channel_config_manager: Optional["ChannelConfigurationManager"] = None
    objective_store: Optional["ObjectiveStore"] = None


class ApplicationContext:
    """
    Application-level context that owns all components.

    This replaces the pattern where GUI creates everything.
    Now: Application creates everything, GUI just displays.

    Example:
        # Create context
        context = ApplicationContext(simulation=True)

        # Create and show GUI
        gui = context.create_gui()
        gui.show()

        # When done
        context.shutdown()
    """

    def __init__(self, simulation: bool = False):
        """
        Initialize the application context.

        Args:
            simulation: If True, use simulated hardware
        """
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._simulation = simulation
        self._microscope: Optional["Microscope"] = None
        self._controllers: Optional[Controllers] = None
        self._gui = None

        self._log.info(f"Creating ApplicationContext (simulation={simulation})")

        # Build components
        self._build_microscope()
        self._build_controllers()

    def _build_microscope(self):
        """Build the microscope from configuration."""
        from control.microscope import Microscope

        self._log.info("Building microscope...")
        self._microscope = Microscope.build_from_global_config(
            simulated=self._simulation
        )
        self._log.info("Microscope built successfully")

    def _build_controllers(self):
        """
        Build controllers container from microscope's existing controllers.

        Note: Currently the Microscope creates some controllers internally.
        This method wraps those in a Controllers dataclass. Future work
        will move controller creation entirely into ApplicationContext.
        """
        self._log.info("Building controllers...")

        # For now, expose the controllers that Microscope creates
        # Future: create controllers here with explicit dependency injection
        self._controllers = Controllers(
            live=self._microscope.live_controller,
            stream_handler=self._microscope.stream_handler,
            channel_config_manager=self._microscope.channel_configuration_manager,
            objective_store=self._microscope.objective_store,
        )

        self._log.info("Controllers built successfully")

    @property
    def microscope(self) -> "Microscope":
        """Get the microscope instance."""
        if self._microscope is None:
            raise RuntimeError("Microscope not initialized")
        return self._microscope

    @property
    def controllers(self) -> Controllers:
        """Get the controllers container."""
        if self._controllers is None:
            raise RuntimeError("Controllers not initialized")
        return self._controllers

    @property
    def is_simulation(self) -> bool:
        """Check if running in simulation mode."""
        return self._simulation

    def create_gui(self):
        """
        Create the GUI with pre-built controllers.

        Returns:
            HighContentScreeningGui instance
        """
        # Import here to avoid circular imports
        from control.gui_hcs import HighContentScreeningGui

        self._log.info("Creating GUI...")
        # For now, pass microscope directly - GUI still creates some things
        # Future: pass Controllers dataclass only
        self._gui = HighContentScreeningGui(
            is_simulation=self._simulation,
        )
        self._log.info("GUI created successfully")
        return self._gui

    def shutdown(self):
        """Clean shutdown of all components."""
        self._log.info("Shutting down application...")

        if self._gui:
            self._gui.close()
            self._gui = None

        # Shutdown controllers
        if self._controllers:
            if self._controllers.live:
                self._controllers.live.stop_live()
            # StreamHandler doesn't have a stop method currently

        # Shutdown microscope
        if self._microscope:
            self._microscope.close()
            self._microscope = None

        self._controllers = None

        self._log.info("Application shutdown complete")
