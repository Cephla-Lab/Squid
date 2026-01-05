"""
Configuration management for Squid microscope.

This module provides:
- ConfigRepository: Centralized config I/O and caching

Example usage:
    from control.core.config import ConfigRepository

    repo = ConfigRepository()
    repo.set_profile("default")

    general = repo.get_general_config()
    objective = repo.get_objective_config("20x")
"""

from control.core.config.repository import ConfigRepository

__all__ = [
    "ConfigRepository",
]
