"""
Protocol loader for YAML-based experiment protocols (V2).

Provides loading, validation, and saving of ExperimentProtocol objects.
Handles repeat expansion and resource resolution.
"""

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

import yaml
from pydantic import ValidationError

import squid.core.logging
from squid.core.protocol.imaging_protocol import ImagingProtocol
from squid.core.protocol.schema import ExperimentProtocol

if TYPE_CHECKING:
    from squid.core.config.repository import ConfigRepository

_log = squid.core.logging.get_logger(__name__)


def load_imaging_protocol(path: Union[str, Path]) -> ImagingProtocol:
    """Load a single ImagingProtocol from a YAML file.

    The file should contain a bare ImagingProtocol dict (no wrapper).
    The existing ``upgrade_legacy_shape`` validator handles old flat formats.

    Args:
        path: Path to the YAML file.

    Returns:
        Validated ImagingProtocol.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ProtocolValidationError: If validation fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Imaging protocol file not found: {path}")
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ProtocolValidationError(f"Invalid YAML in {path}: {e}")
    if not isinstance(data, dict):
        raise ProtocolValidationError(
            f"Imaging protocol file must contain a YAML mapping, got {type(data).__name__}"
        )
    try:
        return ImagingProtocol.model_validate(data)
    except ValidationError as e:
        raise ProtocolValidationError(f"Imaging protocol validation failed: {e}")


def save_imaging_protocol(protocol: ImagingProtocol, path: Union[str, Path]) -> None:
    """Save a single ImagingProtocol to a YAML file.

    Serializes with ``exclude_defaults=True`` to keep files minimal.

    Args:
        protocol: Protocol to save.
        path: Output file path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = protocol.model_dump(exclude_defaults=True, mode="json")
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    _log.info(f"Saved imaging protocol to {path}")


class ProtocolValidationError(Exception):
    """Raised when protocol validation fails."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or []


class ProtocolLoader:
    """Loads and validates V2 experiment protocols from YAML files.

    V2 protocols support:
    - Named resources (fluidics_protocols, imaging_protocols)
    - Step-based rounds with discriminated union step types
    - Repeat expansion with {i} substitution
    - External file references for resources
    - Profile-based imaging protocol resolution via ConfigRepository

    Usage:
        loader = ProtocolLoader()

        # Load from file
        protocol = loader.load("path/to/protocol.yaml")

        # Load from string
        protocol = loader.load_from_string(yaml_content)

        # Save protocol
        loader.save(protocol, "path/to/output.yaml")
    """

    def __init__(self, config_repo: Optional["ConfigRepository"] = None):
        """Initialize the protocol loader.

        Args:
            config_repo: Optional ConfigRepository for resolving imaging protocols
                from user profiles. If not provided, only inline definitions are used.
        """
        self._config_repo = config_repo

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

            # Resolve imaging protocol names from profile if not found inline
            if self._config_repo is not None:
                self._resolve_profile_protocols(protocol)

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

    def _resolve_profile_protocols(self, protocol: ExperimentProtocol) -> None:
        """Resolve imaging protocol names from user profile.

        For each ImagingStep whose protocol name is not found in the
        experiment's imaging_protocols dict, attempt to load it from
        the user profile via ConfigRepository.

        Args:
            protocol: The parsed protocol to augment with profile protocols
        """
        if self._config_repo is None:
            return

        from squid.core.protocol.step import ImagingStep

        for round_ in protocol.rounds:
            for step in round_.steps:
                if not isinstance(step, ImagingStep):
                    continue
                name = step.protocol
                if name in protocol.imaging_protocols:
                    continue
                # Try loading from profile
                profile_protocol = self._config_repo.get_imaging_protocol(name)
                if profile_protocol is not None:
                    protocol.imaging_protocols[name] = profile_protocol
                    _log.info(f"Resolved imaging protocol '{name}' from user profile")

    def _resolve_resources(self, data: Dict[str, Any], protocol_dir: Path) -> Dict[str, Any]:
        """Resolve file-path protocol references and make resource paths absolute.

        Imaging protocols are referenced by file path in each ImagingStep's
        ``protocol`` field (e.g. ``protocols/fluorescence_405.yaml``).  This
        method scans all imaging steps, loads each referenced file with
        ``load_imaging_protocol()``, and registers the result in the
        ``imaging_protocols`` dict keyed by the original path string.

        Args:
            data: Raw protocol data
            protocol_dir: Directory containing the experiment protocol file

        Returns:
            Data with file references resolved
        """
        data = copy.deepcopy(data)
        resources = data.setdefault("resources", {})

        # Preserve legacy top-level keys but normalize to resources for the canonical model.
        for field in (
            "imaging_protocols",
            "fluidics_protocols",
            "fluidics_protocols_file",
            "fluidics_config_file",
            "fov_file",
        ):
            if field in data:
                if field not in resources:
                    resources[field] = data.pop(field)
                else:
                    _log.warning(
                        f"Ignoring top-level '{field}' — already defined in resources block"
                    )
                    data.pop(field)

        if "fov_sets" in data or "fov_sets" in resources:
            raise ProtocolValidationError(
                "Named FOV sets are no longer supported; use a single run-level "
                "resources.fov_file."
            )

        # Resolve imaging_protocols with file: references (inline dict with file key)
        for name, config in list(resources.get("imaging_protocols", {}).items()):
            if isinstance(config, dict) and "file" in config:
                file_path = protocol_dir / config["file"]
                if not file_path.exists():
                    raise ProtocolValidationError(
                        f"Imaging protocol file not found: {file_path}"
                    )
                with open(file_path, "r") as f:
                    resources.setdefault("imaging_protocols", {})[name] = yaml.safe_load(f)

        # Resolve fluidics_protocols with file: references
        for name, proto in list(resources.get("fluidics_protocols", {}).items()):
            if isinstance(proto, dict) and "file" in proto:
                file_path = protocol_dir / proto["file"]
                if not file_path.exists():
                    raise ProtocolValidationError(
                        f"Fluidics protocol file not found: {file_path}"
                    )
                with open(file_path, "r") as f:
                    resources.setdefault("fluidics_protocols", {})[name] = yaml.safe_load(f)

        # Resolve resource file paths to absolute
        for field in ("fluidics_protocols_file", "fluidics_config_file", "fov_file"):
            val = resources.get(field)
            if val and not Path(val).is_absolute():
                resources[field] = str(protocol_dir / val)

        output_directory = data.get("output_directory")
        if output_directory:
            expanded_output = Path(output_directory).expanduser()
            if not expanded_output.is_absolute():
                expanded_output = protocol_dir / expanded_output
            data["output_directory"] = str(expanded_output)

        # Resolve imaging protocol file-path references from ImagingStep.protocol fields.
        # Each imaging step's ``protocol`` value is a relative file path (e.g.
        # ``protocols/fluorescence_405.yaml``).  We load the file, register it
        # in the resources dict keyed by the original path string, then leave
        # the step's ``protocol`` field unchanged so validate_references matches.
        imaging_protocols = resources.setdefault("imaging_protocols", {})
        for round_def in data.get("rounds", []):
            for step in round_def.get("steps", []):
                if step.get("step_type") != "imaging":
                    continue
                if "fovs" in step:
                    raise ProtocolValidationError(
                        "ImagingStep.fovs is no longer supported; use the "
                        "orchestrator protocol's run-level resources.fov_file."
                    )
                proto_ref = step.get("protocol")
                if not proto_ref or proto_ref in imaging_protocols:
                    continue
                # Check if it looks like a file path (contains . or /)
                if "." not in proto_ref and "/" not in proto_ref:
                    continue
                proto_path = protocol_dir / proto_ref
                if not proto_path.exists():
                    raise ProtocolValidationError(
                        f"Imaging protocol file not found: {proto_path} "
                        f"(referenced as '{proto_ref}')"
                    )
                loaded = load_imaging_protocol(proto_path)
                imaging_protocols[proto_ref] = loaded.model_dump(mode="json")

        data["resources"] = resources
        return data

    def _expand_repeats(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Expand rounds with repeat: N, substituting {i} in names and step references.

        For a round with repeat: 3, generates 3 rounds with {i} replaced by 1, 2, 3.

        Substitution applies to:
        - Round name field
        - Step protocol references (FluidicsStep and ImagingStep)

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

        # Check channels in each imaging protocol
        for protocol_name, imaging_proto in protocol.imaging_protocols.items():
            for ch in imaging_proto.channels:
                ch_name = ch if isinstance(ch, str) else ch.name
                if ch_name not in available_set:
                    errors.append(
                        f"Imaging protocol '{protocol_name}' channel '{ch_name}' not found"
                    )

        return errors
