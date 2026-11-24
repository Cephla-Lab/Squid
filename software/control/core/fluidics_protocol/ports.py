"""The two engines as the runner sees them. typing.Protocols so tests script fakes and phase 2 adds Qt adapters."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from control.models.fluidics_protocol import CoordinatesBlock, SettingsBlock
from control.models.fluidics_run import TecState


@dataclass(frozen=True)
class FluidicsOutcome:
    outcome: str  # finished | stopped | failed (the library's RunEnded.outcome)
    message: Optional[str]
    elapsed_seconds: float
    position: Optional[int]  # index into the plan handed to start(); None when finished
    run_id: Optional[str]
    reagent_used_ul: Dict[int, float] = field(default_factory=dict)


class FluidicsTicket(Protocol):
    run_id: Optional[str]
    position: Optional[int]  # plan index of the sequence in flight, relative to the plan handed to start()

    def wait(self, timeout: float) -> Optional[FluidicsOutcome]: ...

    def pause(self) -> bool: ...

    def resume(self) -> bool: ...

    def abort(self) -> bool: ...

    def at_rest(self) -> bool: ...


class FluidicsPort(Protocol):
    def validate(self, rows: List[dict]) -> None: ...  # raises ValueError

    def plan(self, rows: List[dict]) -> tuple: ...  # entries expose .duration_seconds

    def start(
        self, rows: List[dict], plan: Optional[tuple] = None, after_sequence: Optional[Callable[[int], None]] = None
    ) -> FluidicsTicket:
        """after_sequence(position): called on the engine's thread each time a plan entry (relative to the plan
        handed in) has completed, before the next one starts - the next one waits for it to return. The runner
        passes it only for imaging.focus_log."""

    def make_safe(self) -> List[Exception]: ...

    def tec_state(self) -> Optional[TecState]: ...

    def restore_tec(self, state: TecState) -> None: ...


@dataclass
class ImagingRequest:
    folder: str  # session folder name inside run_dir (attempt suffix already applied)
    run_dir: str
    settings: SettingsBlock
    coordinates: CoordinatesBlock
    step_index: int
    attempt: int
    protocol: Dict[str, Any]  # written to acquisition.yaml's protocol: section


@dataclass(frozen=True)
class ImagingResult:
    end_reason: str  # completed | completed_with_errors | user_abort | error | failed_to_start
    image_count: int
    folder: str


class ImagingStartError(RuntimeError):
    """The acquisition could not be started (bad settings, folder exists, hardware mismatch)."""


class ImagingHandle(Protocol):
    def wait(self, timeout: float) -> Optional[ImagingResult]: ...

    def abort(self) -> None: ...


class ImagingPort(Protocol):
    def start(self, request: ImagingRequest) -> ImagingHandle: ...  # raises ImagingStartError

    def measure_focus_um(self) -> Optional[float]:
        """One laser AF displacement reading where the stage is, without moving z: None when laser AF cannot be
        used at all, nan when this reading found no spot."""

    def stage_position(self) -> Optional[Dict[str, float]]:
        """Where the stage is, read only: x_mm, y_mm, z_um (and piezo_um with a piezo); None when unknown."""


def plan_seconds(plan: Sequence[Any]) -> float:
    return float(sum(getattr(entry, "duration_seconds", 0.0) for entry in plan))
