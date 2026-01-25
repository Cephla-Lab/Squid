"""
Protocol loader for YAML-based experiment protocols (V2).

Provides loading, validation, and saving of ExperimentProtocol objects.
Handles repeat expansion and resource resolution.
"""

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import ValidationError

import squid.core.logging
from squid.core.protocol.schema import ExperimentProtocol

_log = squid.core.logging.get_logger(__name__)


class ProtocolValidationError(Exception):
    """Raised when protocol validation fails."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or []


class ProtocolLoader:
    """Loads and validates V2 experiment protocols from YAML files.

    V2 protocols support:
    - Named resources (fluidics_protocols, imaging_configs, fov_sets)
    - Step-based rounds with discriminated union step types
    - Repeat expansion with {i} substitution
    - External file references for resources

    Usage:
        loader = ProtocolLoader()

        # Load from file
        protocol = loader.load("path/to/protocol.yaml")

        # Load from string
        protocol = loader.load_from_string(yaml_content)

        # Save protocol
        loader.save(protocol, "path/to/output.yaml")
    """

    def __init__(self):
        """Initialize the protocol loader."""
        pass

    def load(self, path: Union[str, Path]) -> ExperimentProtocol:
        """Load a V2 protocol from a YAML file.

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

        return self._parse_protocol(data, protocol_dir=path.parent, source=str(path))

    def load_from_string(
        self,
        content: str,
        protocol_dir: Optional[Path] = None,
    ) -> ExperimentProtocol:
        """Load a V2 protocol from a YAML string.

        Args:
            content: YAML content as string
            protocol_dir: Optional directory for resolving relative paths

        Returns:
            Validated ExperimentProtocol

        Raises:
            ProtocolValidationError: If the protocol is invalid
        """
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ProtocolValidationError(f"Invalid YAML: {e}")

        return self._parse_protocol(
            data,
            protocol_dir=protocol_dir or Path.cwd(),
            source="<string>",
        )

    def _parse_protocol(
        self,
        data: Dict[str, Any],
        protocol_dir: Path,
        source: str = "<unknown>",
    ) -> ExperimentProtocol:
        """Parse and validate V2 protocol data.

        Args:
            data: Raw protocol data dict
            protocol_dir: Directory containing the protocol file (for relative paths)
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
            # Resolve external file references
            data = self._resolve_resources(data, protocol_dir)

            # Expand rounds with repeat: N
            data = self._expand_repeats(data)

            # Parse with Pydantic
            protocol = ExperimentProtocol.model_validate(data)

            # Validate references
            ref_errors = protocol.validate_references()
            if ref_errors:
                raise ProtocolValidationError(
                    "Invalid resource references in protocol",
                    errors=ref_errors,
                )

            _log.info(f"Loaded protocol '{protocol.name}' from {source}")
            return protocol

        except ValidationError as e:
            errors = [str(err) for err in e.errors()]
            raise ProtocolValidationError(
                f"Protocol validation failed: {e}",
                errors=errors,
            )

    def _resolve_resources(self, data: Dict[str, Any], protocol_dir: Path) -> Dict[str, Any]:
        """Resolve file: references and make FOV paths absolute.

        Handles patterns like:
            imaging_configs:
              fish_standard:
                file: configs/fish.yaml

            fov_sets:
              main_grid: positions/main.csv  # relative path made absolute

        Args:
            data: Raw protocol data
            protocol_dir: Directory containing the protocol file

        Returns:
            Data with file references resolved
        """
        data = copy.deepcopy(data)

        # Resolve imaging_configs with file: references
        for name, config in data.get("imaging_configs", {}).items():
            if isinstance(config, dict) and "file" in config:
                file_path = protocol_dir / config["file"]
                if not file_path.exists():
                    raise ProtocolValidationError(
                        f"Imaging config file not found: {file_path}"
                    )
                with open(file_path, "r") as f:
                    data["imaging_configs"][name] = yaml.safe_load(f)

        # Resolve fluidics_protocols with file: references
        for name, proto in data.get("fluidics_protocols", {}).items():
            if isinstance(proto, dict) and "file" in proto:
                file_path = protocol_dir / proto["file"]
                if not file_path.exists():
                    raise ProtocolValidationError(
                        f"Fluidics protocol file not found: {file_path}"
                    )
                with open(file_path, "r") as f:
                    data["fluidics_protocols"][name] = yaml.safe_load(f)

        # Make FOV set paths absolute
        for name, csv_path in data.get("fov_sets", {}).items():
            if csv_path and not Path(csv_path).is_absolute():
                data["fov_sets"][name] = str(protocol_dir / csv_path)

        return data

    def _expand_repeats(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Expand rounds with repeat: N, substituting {i} in names and step references.

        For a round with repeat: 3, generates 3 rounds with {i} replaced by 1, 2, 3.

        Substitution applies to:
        - Round name field
        - Step protocol references (FluidicsStep)
        - Step config and fovs references (ImagingStep)

        Args:
            data: Protocol data with potential repeat fields

        Returns:
            Data with repeats expanded

        Raises:
            ProtocolValidationError: If {i} is found in a non-repeated round
        """
        data = copy.deepcopy(data)
        expanded_rounds: List[Dict[str, Any]] = []

        for round_idx, round_def in enumerate(data.get("rounds", [])):
            repeat = round_def.pop("repeat", None)

            if repeat is None:
                # Validate that {i} is not used in non-repeated rounds
                if self._contains_substitution(round_def):
                    round_name = round_def.get("name", f"round {round_idx + 1}")
                    raise ProtocolValidationError(
                        f"Round '{round_name}' contains '{{i}}' substitution but has no 'repeat' field. "
                        f"The '{{i}}' placeholder is only valid in rounds with repeat: N."
                    )
                expanded_rounds.append(round_def)
            else:
                if not isinstance(repeat, int):
                    raise ProtocolValidationError(
                        f"repeat must be an integer, got {type(repeat).__name__}"
                    )
                if repeat < 1:
                    raise ProtocolValidationError("repeat must be >= 1")
                for i in range(1, repeat + 1):
                    expanded_rounds.append(self._substitute(copy.deepcopy(round_def), i))

        data["rounds"] = expanded_rounds
        return data

    def _contains_substitution(self, obj: Any) -> bool:
        """Check if an object contains {i} substitution placeholder.

        Args:
            obj: Object to check (dict, list, or scalar)

        Returns:
            True if {i} is found in any string value
        """
        if isinstance(obj, str):
            return "{i}" in obj
        elif isinstance(obj, dict):
            return any(self._contains_substitution(v) for v in obj.values())
        elif isinstance(obj, list):
            return any(self._contains_substitution(item) for item in obj)
        return False

    def _substitute(self, obj: Any, i: int) -> Any:
        """Replace {i} with round index recursively.

        Args:
            obj: Object to process (dict, list, or scalar)
            i: Round index to substitute

        Returns:
            Object with {i} substituted
        """
        if isinstance(obj, str):
            return obj.replace("{i}", str(i))
        elif isinstance(obj, dict):
            return {k: self._substitute(v, i) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute(item, i) for item in obj]
        return obj

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
        # Note: mode="json" includes all fields needed for re-parsing (including step_type discriminator)
        data = protocol.model_dump(exclude_none=True, mode="json")

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

        # Check channels in each imaging config
        for config_name, config in protocol.imaging_configs.items():
            for ch in config.channels:
                ch_name = ch if isinstance(ch, str) else ch.name
                if ch_name not in available_set:
                    errors.append(
                        f"Imaging config '{config_name}' channel '{ch_name}' not found"
                    )

        return errors
