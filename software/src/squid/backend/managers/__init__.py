# Managers: Stateful managers for instrument configuration and navigation

# Configuration managers
from squid.backend.managers.configuration_manager import ConfigurationManager
from squid.backend.managers.channel_configuration_manager import ChannelConfigurationManager
from squid.backend.managers.contrast_manager import ContrastManager

# Navigation managers
from squid.backend.managers.objective_store import ObjectiveStore
from squid.backend.managers.scan_coordinates import ScanCoordinates
from squid.backend.managers.focus_map import FocusMap
from squid.backend.managers.navigation_state_service import NavigationViewerStateService

__all__ = [
    # Configuration
    "ConfigurationManager",
    "ChannelConfigurationManager",
    "ContrastManager",
    # Navigation
    "ObjectiveStore",
    "ScanCoordinates",
    "FocusMap",
    "NavigationViewerStateService",
]
