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


import abc
import time
from typing import Optional

import pydantic

import squid.logging
from squid.config import AxisConfig, StageConfig
from squid.exceptions import SquidTimeout


class Pos(pydantic.BaseModel):
    x_mm: float
    y_mm: float
    z_mm: float
    # NOTE/TODO(imo): If essentially none of our stages have a theta, this is probably fine.  But If it's a mix we probably want a better way of handling the "maybe has theta" case.
    theta_rad: Optional[float]


class StageStage(pydantic.BaseModel):
    busy: bool


class AbstractStage(metaclass=abc.ABCMeta):
    def __init__(self, stage_config: StageConfig):
        self._config = stage_config
        self._log = squid.logging.get_logger(self.__class__.__name__)

    @abc.abstractmethod
    def move_x(self, rel_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_y(self, rel_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_z(self, rel_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_x_to(self, abs_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_y_to(self, abs_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_z_to(self, abs_mm: float, blocking: bool = True):
        pass

    # TODO(imo): We need a stop or halt or something along these lines
    # @abc.abstractmethod
    # def stop(self, blocking: bool=True):
    #     pass

    @abc.abstractmethod
    def get_pos(self) -> Pos:
        pass

    @abc.abstractmethod
    def get_state(self) -> StageStage:
        pass

    @abc.abstractmethod
    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        pass

    @abc.abstractmethod
    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        pass

    @abc.abstractmethod
    def set_limits(
        self,
        x_pos_mm: Optional[float] = None,
        x_neg_mm: Optional[float] = None,
        y_pos_mm: Optional[float] = None,
        y_neg_mm: Optional[float] = None,
        z_pos_mm: Optional[float] = None,
        z_neg_mm: Optional[float] = None,
        theta_pos_rad: Optional[float] = None,
        theta_neg_rad: Optional[float] = None,
    ):
        pass

    def get_config(self) -> StageConfig:
        return self._config

    def wait_for_idle(self, timeout_s):
        start_time = time.time()
        while time.time() < start_time + timeout_s:
            if not self.get_state().busy:
                return
            # Sleep some small amount of time so we can yield to other threads if needed
            # while waiting.
            time.sleep(0.001)

        error_message = f"Timed out waiting after {timeout_s:0.3f} [s]"
        self._log.error(error_message)

        raise SquidTimeout(error_message)
