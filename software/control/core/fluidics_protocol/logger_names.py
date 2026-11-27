"""The loggers whose records are fluidics, for the logs that keep fluidics apart from the rest of Squid's
logging: the protocol run's run.log and the GUI's Fluidics Log pane. No Qt and no fluidics library import, so
both work when the library is not installed."""

import logging
from typing import List

import squid.logging

# The fluidics library's own loggers. control.fluidics_system's logging bridge hands their records to the squid
# root logger's handlers, and the records keep these names.
LIBRARY_LOGGER_NAMES = ("fluidics", "XCaliburD")

# Squid's fluidics packages, under the squid root logger.
SQUID_FLUIDICS_LOGGER_NAMES = ("control.widgets_fluidics", "control.core.fluidics_protocol", "control.fluidics_system")


def fluidics_loggers() -> List[logging.Logger]:
    """The top fluidics loggers; a record is fluidics when its logger is one of these or below one."""
    return [logging.getLogger(name) for name in LIBRARY_LOGGER_NAMES] + [
        squid.logging.get_logger(name) for name in SQUID_FLUIDICS_LOGGER_NAMES
    ]
