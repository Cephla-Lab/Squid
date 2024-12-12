from abc import ABC, abstractmethod
from typing import Tuple

class LightSource(ABC):
    """Abstract base class defining the interface for different light sources."""
    
    @abstractmethod
    def __init__(self):
        """Initialize the light source and establish communication."""
        pass

    @abstractmethod
    def initialize(self):
        """
        Initialize the connection and settings for the light source.
        Returns True if successful, False otherwise.
        """
        pass

    @abstractmethod
    def set_intensity_control_mode(self, mode):
        """
        Set intensity control mode.
        
        Args:
            mode: IntensityControlMode(Enum)
        """
        pass
    
    @abstractmethod
    def get_intensity_control_mode(self):
        """
        Get current intensity control mode.
        
        Returns:
            IntensityControlMode(Enum)
        """
        pass

    @abstractmethod
    def set_shutter_control_mode(self, mode):
        """
        Set shutter control mode.
        
        Args:
            mode: ShutterControlMode(Enum)
        """
        pass
    
    @abstractmethod
    def get_shutter_control_mode(self):
        """
        Get current shutter control mode.
        
        Returns:
            ShutterControlMode(Enum)
        """
        pass
    
    @abstractmethod
    def set_shutter_state(self, channel, state):
        """
        Turn a specific channel on or off.
        
        Args:
            channel: Channel ID
            state: True to turn on, False to turn off
        """
        pass
    
    @abstractmethod
    def get_shutter_state(self, channel):
        """
        Get the current shutter state of a specific channel.
        
        Args:
            channel: Channel ID
            
        Returns:
            bool: True if channel is on, False if off
        """
        pass
    
    @abstractmethod
    def set_intensity(self, channel, intensity):
        """
        Set the intensity for a specific channel.
        
        Args:
            channel: Channel ID
            intensity: Intensity value (0-100)
        """
        pass
    
    @abstractmethod
    def get_intensity(self, channel) -> float:
        """
        Get the current intensity of a specific channel.
        
        Args:
            channel: Channel ID
            
        Returns:
            float: Current intensity value
        """
        pass
    
    @abstractmethod
    def get_intensity_range(self) -> Tuple[float, float]:
        """
        Get the valid intensity range.
        
        Returns:
            Tuple[float, float]: (minimum intensity, maximum intensity)
        """
        pass

    
    @abstractmethod
    def shut_down(self):
        """Safely shut down the light source."""
        pass
    
