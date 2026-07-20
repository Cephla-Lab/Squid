"""Request bodies for the REST API. Responses are plain dicts assembled by the service."""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ZMillimeters(_Strict):
    """Explicit absolute Z baseline (mm) for an acquisition run."""

    z_mm: float


# Z baseline policy for a run: "current" (today's default -- use the stage z at run
# start), "autofocus" (baseline on current z but require a ready AF for this run), or an
# explicit {"z_mm": <float>} absolute position validated against the stage Z limits.
ZReference = Union[Literal["current", "autofocus"], ZMillimeters]


class MoveRequest(_Strict):
    mode: Literal["absolute", "relative"] = "absolute"
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    block_until_complete: bool = True


class ChannelSelectRequest(_Strict):
    name: str


class ExposureRequest(_Strict):
    exposure_ms: float = Field(gt=0, le=10000)
    channel: Optional[str] = None


class IntensityRequest(_Strict):
    channel: str
    intensity: float = Field(ge=0, le=100)


class ObjectiveRequest(_Strict):
    name: str


class AcquireRequest(_Strict):
    channel: Optional[str] = None
    save_path: Optional[str] = None


class AutofocusRunRequest(_Strict):
    mode: Literal["reflection"] = "reflection"
    target_um: float = 0.0


class AutofocusCorrectRequest(_Strict):
    threshold_um: float = Field(default=10.0, gt=0, le=1000)


class LaserAfImageRequest(_Strict):
    save_path: Optional[str] = None
    use_last_frame: bool = True


class InitializeRequest(_Strict):
    home: bool = False


class MethodCreateRequest(_Strict):
    name: str
    config: dict


class MethodUpdateRequest(_Strict):
    config: dict


class AutofocusOverride(_Strict):
    reflection: Optional[bool] = None
    contrast: Optional[bool] = None


class AcquisitionOverrides(_Strict):
    wells: Optional[str] = None
    output_path: Optional[str] = None
    sample_format: Optional[str] = None


class GridSpec(_Strict):
    wells: str
    channels: List[str] = Field(min_length=1)
    nx: int = Field(default=2, ge=1, le=100)
    ny: int = Field(default=2, ge=1, le=100)
    overlap_percent: float = Field(default=10.0, ge=0, le=50)
    wellplate_format: str = "96 well plate"


class AcquisitionRequest(_Strict):
    method: Optional[str] = None
    yaml_path: Optional[str] = None
    grid: Optional[GridSpec] = None
    experiment_id: Optional[str] = None
    operator: Optional[str] = None
    scheduler_job_id: Optional[str] = None
    autofocus: Optional[AutofocusOverride] = None
    overrides: AcquisitionOverrides = Field(default_factory=AcquisitionOverrides)
    z_reference: ZReference = "current"

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "AcquisitionRequest":
        sources = [s for s in (self.method, self.yaml_path, self.grid) if s is not None]
        if len(sources) != 1:
            raise ValueError("Provide exactly one of: method, yaml_path, grid")
        return self


class AbortRequest(_Strict):
    timeout_s: float = Field(default=60.0, ge=0, le=600)


class PythonExecRequest(_Strict):
    code: str


class DebugSettingsRequest(_Strict):
    """URS API-COMPAT-002: REST parity for the legacy TCP view/performance debug
    commands (_cmd_set_view_settings / _cmd_set_performance_mode). All fields are
    optional; only the ones provided are changed.

    Note: `display_plate_view` from the original TCP command set is intentionally
    omitted -- the underlying `control._def.DISPLAY_PLATE_VIEW` flag no longer
    exists in this codebase (plate view was unified into the mosaic view /
    UnifiedMosaicWidget, governed solely by `display_mosaic_view`).
    """

    performance_mode: Optional[bool] = None
    save_downsampled_well_images: Optional[bool] = None
    save_downsampled_overview: Optional[bool] = None
    display_mosaic_view: Optional[bool] = None
