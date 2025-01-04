# parameters.py
from dataclasses import dataclass
from typing import Dict, Any
import json
import os

@dataclass
class StitchingParameters:
    """Parameters for microscopy image stitching operations."""
    # Required parameters
    input_folder: str
    
    # Output configuration
    output_format: str = '.ome.zarr'
    
    # Image processing options
    apply_flatfield: bool = False
    
    # Registration options
    use_registration: bool = False
    registration_channel: str = ''  # Will use first available channel if empty
    registration_z_level: int = 0
    dynamic_registration: bool = False
    
    # Scanning and stitching configuration
    scan_pattern: str = 'Unidirectional'  # or 'S-Pattern'
    merge_timepoints: bool = False
    merge_hcs_regions: bool = False

    def __post_init__(self):
        """Validate and process parameters after initialization."""
        # Convert relative path to absolute
        self.input_folder = os.path.abspath(self.input_folder)
            
    def validate(self) -> None:
        """
        Validate parameters and raise appropriate errors.
        
        Raises:
            ValueError: If parameters are invalid or incompatible
        """
        # Validate input folder
        if not os.path.exists(self.input_folder):
            raise ValueError(f"Input folder does not exist: {self.input_folder}")
        
        # Validate output format
        if self.output_format not in ['.ome.zarr', '.ome.tiff']:
            raise ValueError("Output format must be either .ome.zarr or .ome.tiff")
            
        # Validate scan pattern
        if self.scan_pattern not in ['Unidirectional', 'S-Pattern']:
            raise ValueError("Scan pattern must be either 'Unidirectional' or 'S-Pattern'")
            
        # Validate registration settings
        if self.use_registration:
            if self.registration_z_level < 0:
                raise ValueError("Registration Z-level must be non-negative")
            
            # Note: registration_channel can be empty - will use first available
    
    @property
    def stitched_folder(self) -> str:
        """Path to folder containing stitched outputs."""
        return os.path.join(self.input_folder + "_stitched")
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StitchingParameters':
        """
        Create parameters from a dictionary.
        
        Args:
            data: Dictionary containing parameter values
            
        Returns:
            StitchingParameters: New instance with values from dictionary
        """
        valid_fields = {k for k in cls.__dataclass_fields__}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)
    
    @classmethod
    def from_json(cls, json_path: str) -> 'StitchingParameters':
        """
        Create parameters from a JSON file.
        
        Args:
            json_path: Path to JSON file containing parameters
            
        Returns:
            StitchingParameters: New instance with values from JSON
        """
        with open(json_path) as f:
            return cls.from_dict(json.load(f))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert parameters to a dictionary."""
        return {k: getattr(self, k) for k in self.__dataclass_fields__}
    
    def to_json(self, json_path: str) -> None:
        """
        Save parameters to a JSON file.
        
        Args:
            json_path: Path where JSON file should be saved
        """
        with open(json_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
