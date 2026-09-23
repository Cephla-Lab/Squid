from control.core.fluidics_protocol.events import HoldAction, RunnerState
from control.core.fluidics_protocol.ports import FluidicsOutcome, ImagingRequest, ImagingResult, plan_seconds
from control.models.fluidics_protocol import CoordinatesBlock, SettingsBlock


def test_dataclasses_and_enums_exist():
    assert RunnerState.HELD.value == "HELD" and HoldAction.END.value == "end"
    outcome = FluidicsOutcome("stopped", None, 1.5, 2, "run-1", {1: 500.0})
    assert outcome.position == 2 and outcome.reagent_used_ul == {1: 500.0}
    request = ImagingRequest("R01_image", "/run", SettingsBlock(), CoordinatesBlock(), 0, 1, {"round": "R01"})
    assert request.folder == "R01_image"
    assert ImagingResult("completed", 12, "R01_image").image_count == 12


def test_plan_seconds_sums_duration_seconds_of_any_plan_entries():
    class Entry:
        def __init__(self, s):
            self.duration_seconds = s

    assert plan_seconds((Entry(1.0), Entry(2.5))) == 3.5
