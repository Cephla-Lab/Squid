"""
Canonical imaging protocol model for orchestrated experiments.

An imaging protocol describes how a single field of view should be imaged:
channel order, z-stack behavior, focus-gate requirements, and capture retry
policy. Orchestrator steps can then reference a named imaging protocol and
optionally override selected focus/capture policy fields.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from squid.core.events import AutofocusMode


class ChannelConfigOverride(BaseModel):
    """Per-channel acquisition overrides."""

    name: str
    exposure_time_ms: Optional[float] = None
    analog_gain: Optional[float] = None
    illumination_intensity: Optional[float] = None
    z_offset_um: float = 0.0

    @field_validator("exposure_time_ms")
    @classmethod
    def validate_exposure_time(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value <= 0:
            raise ValueError("exposure_time_ms must be > 0")
        return value

    @field_validator("illumination_intensity")
    @classmethod
    def validate_illumination_intensity(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and (value < 0 or value > 100):
            raise ValueError("illumination_intensity must be between 0 and 100")
        return value


class ZStackConfig(BaseModel):
    """Z-stack acquisition settings."""

    planes: int = 1
    step_um: float = 0.5
    direction: Literal["from_center", "from_bottom", "from_top"] = "from_center"

    @field_validator("planes")
    @classmethod
    def validate_planes(cls, value: int) -> int:
        if value < 1:
            raise ValueError("planes must be >= 1")
        return value

    @field_validator("step_um")
    @classmethod
    def validate_step_um(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("step_um must be > 0")
        return value


class FocusLockConfig(BaseModel):
    """Focus-lock settings used by focus-gate evaluation."""

    buffer_length: int = 5
    recovery_attempts: int = 3
    min_spot_snr: float = 10.0
    acquire_threshold_um: float = 0.25
    maintain_threshold_um: float = 0.5
    auto_search_enabled: bool = False
    lock_timeout_s: float = 5.0


class ImagingAcquisitionConfig(BaseModel):
    """Acquisition content for a single FOV."""

    channels: List[Union[str, ChannelConfigOverride]] = Field(default_factory=list)
    z_stack: ZStackConfig = Field(default_factory=ZStackConfig)
    acquisition_order: Literal["channel_first", "z_first"] = "channel_first"
    save_format: Optional[Literal["ome-tiff", "tiff", "zarr-v3"]] = None
    skip_saving: bool = False

    @field_validator("channels")
    @classmethod
    def validate_channels(
        cls,
        value: List[Union[str, ChannelConfigOverride]],
    ) -> List[Union[str, ChannelConfigOverride]]:
        if not value:
            raise ValueError("channels must not be empty")
        return value


class FocusGateConfig(BaseModel):
    """Local focus-gate criteria for deciding whether a FOV can be imaged."""

    mode: AutofocusMode = AutofocusMode.NONE
    channel: Optional[str] = None
    interval_fovs: int = 1
    focus_lock: FocusLockConfig = Field(default_factory=FocusLockConfig)
    require_in_focus: bool = False
    max_focus_error_um: Optional[float] = None
    max_focus_attempts: int = 1
    on_focus_gate_fail: Literal["retry", "skip_fov", "fail_step"] = "skip_fov"

    @field_validator("interval_fovs")
    @classmethod
    def validate_interval_fovs(cls, value: int) -> int:
        if value < 1:
            raise ValueError("interval_fovs must be >= 1")
        return value

    @field_validator("max_focus_attempts")
    @classmethod
    def validate_max_focus_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_focus_attempts must be >= 1")
        return value

    @field_validator("max_focus_error_um")
    @classmethod
    def validate_max_focus_error_um(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value <= 0:
            raise ValueError("max_focus_error_um must be > 0")
        return value


class CapturePolicyConfig(BaseModel):
    """Local capture retry policy for a single FOV."""

    max_capture_attempts: int = 1
    retry_delay_s: float = 0.0
    on_capture_fail: Literal["retry", "skip_fov", "fail_step"] = "fail_step"

    @field_validator("max_capture_attempts")
    @classmethod
    def validate_max_capture_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_capture_attempts must be >= 1")
        return value

    @field_validator("retry_delay_s")
    @classmethod
    def validate_retry_delay(cls, value: float) -> float:
        if value < 0:
            raise ValueError("retry_delay_s must be >= 0")
        return value


# Public alias kept because the rest of the codebase already imports FocusConfig.
FocusConfig = FocusGateConfig


class ImagingProtocol(BaseModel):
    """Reusable recipe for imaging one FOV."""

    description: str = ""
    acquisition: ImagingAcquisitionConfig = Field(default_factory=ImagingAcquisitionConfig)
    focus_gate: FocusGateConfig = Field(default_factory=FocusGateConfig)
    capture_policy: CapturePolicyConfig = Field(default_factory=CapturePolicyConfig)

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_shape(cls, data: object) -> object:
        """Accept the old flat shape but normalize to the canonical nested model."""
        if not isinstance(data, dict):
            return data

        payload = dict(data)

        acquisition = dict(payload.get("acquisition") or {})
        if "channels" in payload:
            acquisition.setdefault("channels", payload.pop("channels"))
        if "z_stack" in payload:
            acquisition.setdefault("z_stack", payload.pop("z_stack"))
        if "acquisition_order" in payload:
            acquisition.setdefault("acquisition_order", payload.pop("acquisition_order"))
        if "save_format" in payload:
            acquisition.setdefault("save_format", payload.pop("save_format"))
        if "skip_saving" in payload:
            acquisition.setdefault("skip_saving", payload.pop("skip_saving"))
        payload["acquisition"] = acquisition

        focus_gate = dict(payload.get("focus_gate") or payload.get("focus") or {})
        if "focus" in payload:
            payload.pop("focus", None)
        payload["focus_gate"] = focus_gate

        capture_policy = dict(payload.get("capture_policy") or {})
        payload["capture_policy"] = capture_policy

        return payload

    @property
    def channels(self) -> List[Union[str, ChannelConfigOverride]]:
        return self.acquisition.channels

    @property
    def z_stack(self) -> ZStackConfig:
        return self.acquisition.z_stack

    @property
    def acquisition_order(self) -> Literal["channel_first", "z_first"]:
        return self.acquisition.acquisition_order

    @property
    def save_format(self) -> Optional[Literal["ome-tiff", "tiff", "zarr-v3"]]:
        return self.acquisition.save_format

    @property
    def skip_saving(self) -> bool:
        return self.acquisition.skip_saving

    @property
    def focus(self) -> FocusGateConfig:
        return self.focus_gate

    def get_channel_names(self) -> List[str]:
        return [ch if isinstance(ch, str) else ch.name for ch in self.acquisition.channels]

    def get_channel_overrides(self) -> List[ChannelConfigOverride]:
        return [ch for ch in self.acquisition.channels if isinstance(ch, ChannelConfigOverride)]
