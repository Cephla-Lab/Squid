"""
Protocol loader for YAML-based experiment protocols.

Provides loading, validation, and saving of ExperimentProtocol objects.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import ValidationError

import squid.core.logging
from squid.core.protocol.schema import (
    ExperimentProtocol,
    ImagingStep,
    Round,
    RoundType,
)

_log = squid.core.logging.get_logger(__name__)


class ProtocolValidationError(Exception):
    """Raised when protocol validation fails."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or []


class ProtocolLoader:
    """Loads and validates experiment protocols from YAML files.

    Usage:
        loader = ProtocolLoader()

        # Load from file
        protocol = loader.load("path/to/protocol.yaml")

        # Load from string
        protocol = loader.load_from_string(yaml_content)

        # Validate channels against available configurations
        errors = loader.validate_channels(protocol, available_channels)

        # Save protocol
        loader.save(protocol, "path/to/output.yaml")
    """

    def __init__(self):
        """Initialize the protocol loader."""
        pass

    def load(self, path: Union[str, Path]) -> ExperimentProtocol:
        """Load a protocol from a YAML file.

        Args:
            path: Path to the YAML protocol file

        Returns:
            Validated ExperimentProtocol

        Raises:
            ProtocolValidationError: If the protocol is invalid
            FileNotFoundError: If the file doesn't exist
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Protocol file not found: {path}")

        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ProtocolValidationError(f"Invalid YAML: {e}")

        return self._parse_protocol(data, source=str(path))

    def load_from_string(self, content: str) -> ExperimentProtocol:
        """Load a protocol from a YAML string.

        Args:
            content: YAML content as string

        Returns:
            Validated ExperimentProtocol

        Raises:
            ProtocolValidationError: If the protocol is invalid
        """
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ProtocolValidationError(f"Invalid YAML: {e}")

        return self._parse_protocol(data, source="<string>")

    def _parse_protocol(
        self,
        data: Dict[str, Any],
        source: str = "<unknown>",
    ) -> ExperimentProtocol:
        """Parse and validate protocol data.

        Args:
            data: Raw protocol data dict
            source: Source identifier for error messages

        Returns:
            Validated ExperimentProtocol

        Raises:
            ProtocolValidationError: If validation fails
        """
        if not isinstance(data, dict):
            raise ProtocolValidationError(
                f"Protocol must be a YAML mapping, got {type(data).__name__}"
            )

        try:
            # Parse rounds with type coercion
            if "rounds" in data:
                data["rounds"] = [
                    self._parse_round(r) for r in data["rounds"]
                ]

            protocol = ExperimentProtocol.model_validate(data)
            _log.info(f"Loaded protocol '{protocol.name}' from {source}")
            return protocol

        except ValidationError as e:
            errors = [str(err) for err in e.errors()]
            raise ProtocolValidationError(
                f"Protocol validation failed: {e}",
                errors=errors,
            )

    def _parse_round(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a single round, handling type conversions."""
        result = dict(data)

        # Convert type string to enum
        if "type" in result and isinstance(result["type"], str):
            try:
                result["type"] = RoundType(result["type"])
            except ValueError:
                pass  # Let Pydantic handle the error

        # Reject legacy inline fluidics steps (use fluidics_protocol instead).
        if "fluidics" in result:
            round_name = result.get("name", "<unknown>")
            raise ProtocolValidationError(
                f"Round '{round_name}' uses legacy 'fluidics' steps; "
                "use 'fluidics_protocol' to reference named protocols."
            )

        # fluidics_protocol is just a string reference, no parsing needed

        # Parse imaging step
        if "imaging" in result and result["imaging"] is not None:
            result["imaging"] = self._parse_imaging_step(result["imaging"])

        return result

    def _parse_imaging_step(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse an imaging step, handling type conversions."""
        return dict(data)

    def validate_channels(
        self,
        protocol: ExperimentProtocol,
        available_channels: List[str],
    ) -> List[str]:
        """Validate that all protocol channels exist in available channels.

        Args:
            protocol: Protocol to validate
            available_channels: List of available channel names

        Returns:
            List of error messages (empty if valid)
        """
        errors: List[str] = []
        available_set = set(available_channels)

        # Check default channels
        for ch in protocol.defaults.imaging.channels:
            if ch not in available_set:
                errors.append(f"Default channel '{ch}' not found in available channels")

        # Check per-round channels
        for round_ in protocol.rounds:
            if round_.imaging is not None:
                for ch in round_.imaging.channels:
                    if ch not in available_set:
                        errors.append(
                            f"Round '{round_.name}' channel '{ch}' not found"
                        )

        return errors

    def save(
        self,
        protocol: ExperimentProtocol,
        path: Union[str, Path],
    ) -> None:
        """Save a protocol to a YAML file.

        Args:
            protocol: Protocol to save
            path: Output file path
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict and serialize
        data = protocol.model_dump(exclude_none=True, exclude_defaults=True)

        # Convert enums to strings
        data = self._serialize_for_yaml(data)

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        _log.info(f"Saved protocol '{protocol.name}' to {path}")

    def _serialize_for_yaml(self, data: Any) -> Any:
        """Convert data for YAML serialization (enums to strings, etc.)."""
        if isinstance(data, dict):
            return {k: self._serialize_for_yaml(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._serialize_for_yaml(v) for v in data]
        elif hasattr(data, "value"):
            # Enum
            return data.value
        else:
            return data

    def create_from_template(
        self,
        name: str,
        num_rounds: int,
        channels: List[str],
        *,
        include_wash: bool = True,
        z_planes: int = 1,
        z_step_um: float = 0.5,
    ) -> ExperimentProtocol:
        """Create a protocol from a template.

        Generates a basic multi-round protocol with alternating
        imaging and wash rounds.

        Args:
            name: Protocol name
            num_rounds: Number of imaging rounds
            channels: List of channel names
            include_wash: Include wash rounds between imaging
            z_planes: Number of z-planes
            z_step_um: Z step size

        Returns:
            Generated ExperimentProtocol
        """
        rounds: List[Round] = []

        for i in range(1, num_rounds + 1):
            # Imaging round with fluidics protocol reference
            rounds.append(
                Round(
                    name=f"Round {i}",
                    type=RoundType.IMAGING,
                    fluidics_protocol=f"probe_delivery_{i}",
                    imaging=ImagingStep(
                        channels=channels,
                        z_planes=z_planes,
                        z_step_um=z_step_um,
                    ),
                )
            )

            # Optional wash round
            if include_wash and i < num_rounds:
                rounds.append(
                    Round(
                        name=f"Wash {i}",
                        type=RoundType.WASH,
                        fluidics_protocol="standard_wash",
                        imaging=None,
                    )
                )

        return ExperimentProtocol(
            name=name,
            version="1.0",
            description=f"Auto-generated {num_rounds}-round protocol",
            rounds=rounds,
        )
