"""Regression tests for multipoint widget experiment-id filtering."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from squid.core.events import AcquisitionStateChanged
from squid.ui.widgets.acquisition.flexible_multipoint import FlexibleMultiPointWidget
from squid.ui.widgets.acquisition.fluidics_multipoint import MultiPointWithFluidicsWidget
from squid.ui.widgets.acquisition.wellplate_multipoint import WellplateMultiPointWidget


WIDGET_CLASSES = (
    WellplateMultiPointWidget,
    FlexibleMultiPointWidget,
    MultiPointWithFluidicsWidget,
)


@pytest.mark.parametrize("widget_cls", WIDGET_CLASSES)
def test_experiment_id_matches_backend_suffix(widget_cls) -> None:
    assert widget_cls._experiment_id_matches(
        "my_experiment",
        "my_experiment_2026-02-11_13-15-42.640408",
    )


@pytest.mark.parametrize("widget_cls", WIDGET_CLASSES)
def test_experiment_id_does_not_match_other_prefix(widget_cls) -> None:
    assert not widget_cls._experiment_id_matches(
        "my_experiment",
        "other_experiment_2026-02-11_13-15-42.640408",
    )


@pytest.mark.parametrize("widget_cls", WIDGET_CLASSES)
def test_state_handler_accepts_backend_suffixed_experiment_id(widget_cls) -> None:
    widget = widget_cls.__new__(widget_cls)
    widget._log = MagicMock()
    widget._active_experiment_id = "my_experiment"
    widget._acquisition_in_progress = False
    widget._acquisition_is_aborting = False
    widget.is_current_acquisition_widget = False
    widget.display_progress_bar = MagicMock()
    widget.acquisition_is_finished = MagicMock()

    widget._on_acquisition_state_changed(
        AcquisitionStateChanged(
            in_progress=True,
            experiment_id="my_experiment_2026-02-11_13-15-42.640408",
            is_aborting=False,
        )
    )

    assert widget._acquisition_in_progress is True
    assert widget._acquisition_is_aborting is False


@pytest.mark.parametrize("widget_cls", WIDGET_CLASSES)
def test_state_handler_ignores_unrelated_experiment_id(widget_cls) -> None:
    widget = widget_cls.__new__(widget_cls)
    widget._log = MagicMock()
    widget._active_experiment_id = "my_experiment"
    widget._acquisition_in_progress = False
    widget._acquisition_is_aborting = False
    widget.is_current_acquisition_widget = False
    widget.display_progress_bar = MagicMock()
    widget.acquisition_is_finished = MagicMock()

    widget._on_acquisition_state_changed(
        AcquisitionStateChanged(
            in_progress=True,
            experiment_id="other_experiment_2026-02-11_13-15-42.640408",
            is_aborting=False,
        )
    )

    assert widget._acquisition_in_progress is False
    assert widget._acquisition_is_aborting is False
