"""Tests for the orchestrator validation dialog."""

from squid.ui.widgets.orchestrator.validation_dialog import ValidationResultDialog


def test_validation_dialog_deduplicates_warning_messages():
    deduped = ValidationResultDialog._dedupe_messages(
        [
            "focus_lock with multi-plane z-stack pauses corrections during capture; review QC after the run",
            "[Setup] focus_lock with multi-plane z-stack pauses corrections during capture; review QC after the run",
            "focus_lock with multi-plane z-stack pauses corrections during capture; review QC after the run",
        ]
    )

    assert deduped == [
        "focus_lock with multi-plane z-stack pauses corrections during capture; review QC after the run",
        "[Setup] focus_lock with multi-plane z-stack pauses corrections during capture; review QC after the run",
    ]
