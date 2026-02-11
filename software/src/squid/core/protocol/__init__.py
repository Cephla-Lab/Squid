# Protocol Definition System for Experiment Orchestration (V2)
#
# This module provides YAML-based protocol definitions for step-based
# multi-round fluidics-imaging experiments.

# Step types (discriminated union)
from squid.core.protocol.step import (
    Step,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
)

# Imaging protocol (canonical module)
from squid.core.protocol.imaging_protocol import (
    ImagingProtocol,
    ChannelConfigOverride,
    ZStackConfig,
    FocusConfig,
    FocusLockConfig,
)

# Error handling
from squid.core.protocol.error_handling import (
    ErrorHandlingConfig,
    FailureAction,
)

# Schema (Round, ExperimentProtocol)
from squid.core.protocol.schema import (
    ExperimentProtocol,
    Round,
    FluidicsCommand,
)

# Fluidics protocols
from squid.core.protocol.fluidics_protocol import (
    FluidicsProtocol,
    FluidicsProtocolStep,
    FluidicsProtocolFile,
)

# Loader
from squid.core.protocol.loader import (
    ProtocolLoader,
    ProtocolValidationError,
)

__all__ = [
    # Step types
    "Step",
    "FluidicsStep",
    "ImagingStep",
    "InterventionStep",
    # Imaging protocol
    "ImagingProtocol",
    "ChannelConfigOverride",
    "ZStackConfig",
    "FocusConfig",
    "FocusLockConfig",
    # Error handling
    "ErrorHandlingConfig",
    "FailureAction",
    # Schema
    "ExperimentProtocol",
    "Round",
    "FluidicsCommand",
    # Fluidics protocols
    "FluidicsProtocol",
    "FluidicsProtocolStep",
    "FluidicsProtocolFile",
    # Loader
    "ProtocolLoader",
    "ProtocolValidationError",
]
