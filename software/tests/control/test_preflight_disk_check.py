"""Tests for the pre-flight "not enough disk space" check.

The decision of what to offer the operator is a pure function (``preflight_disk_options``); the
QMessageBox lives in ``preflight_disk_dialog``, which these tests monkeypatch so the wrapper can be
exercised headlessly.
"""

import logging
from types import SimpleNamespace

import pytest

import control._def
import control.widgets


@pytest.fixture
def test_logger():
    return logging.getLogger("test_preflight_disk_check")


@pytest.fixture
def controller(tmp_path):
    """Minimal stand-in for MultiPointController with the attributes the check reads."""
    calls = []
    split_calls = []
    return SimpleNamespace(
        base_path=str(tmp_path),
        Nt=1,
        get_estimated_acquisition_disk_storage=lambda: 1000,
        get_acquisition_image_count=lambda: 42,
        set_large_acquisition_mode=lambda enabled: calls.append(enabled),
        large_acquisition_mode_calls=calls,
        set_split_ome_timepoints=lambda enabled: split_calls.append(enabled),
        split_ome_timepoints_calls=split_calls,
    )


@pytest.fixture
def free_space(monkeypatch):
    """Control what ``utils.get_available_disk_space`` reports."""

    def _set(available_bytes):
        monkeypatch.setattr(control.widgets.utils, "get_available_disk_space", lambda path: available_bytes)

    return _set


@pytest.fixture
def dialog_action(monkeypatch):
    """Replace the QMessageBox with a stub returning a fixed action; records the calls."""
    recorded = []

    def _set(action):
        def fake_dialog(message, options):
            recorded.append((message, options))
            return action

        monkeypatch.setattr(control.widgets, "preflight_disk_dialog", fake_dialog)
        return recorded

    return _set


class TestPreflightDiskOptions:
    """The pure decision function."""

    def test_enough_space_needs_no_dialog(self):
        options = control.widgets.preflight_disk_options(space_required=100.0, available=200, mode_on=False)
        assert options.needs_dialog is False
        assert options.offer_enable_mode is False
        assert options.offer_split_ome is False

    def test_exactly_enough_space_needs_no_dialog(self):
        options = control.widgets.preflight_disk_options(space_required=200.0, available=200, mode_on=False)
        assert options.needs_dialog is False

    def test_not_enough_space_with_mode_off_offers_enable(self):
        options = control.widgets.preflight_disk_options(space_required=300.0, available=200, mode_on=False)
        assert options.needs_dialog is True
        assert options.offer_enable_mode is True
        assert options.offer_split_ome is False

    def test_not_enough_space_with_mode_on_does_not_offer_enable(self):
        options = control.widgets.preflight_disk_options(space_required=300.0, available=200, mode_on=True)
        assert options.needs_dialog is True
        assert options.offer_enable_mode is False

    def test_enough_space_never_offers_enable_even_with_mode_off(self):
        options = control.widgets.preflight_disk_options(space_required=1.0, available=200, mode_on=False)
        assert options.offer_enable_mode is False


class TestPreflightOfferSplitOme:
    """The OME-TIFF per-timepoint split is only worth offering for a multi-timepoint OME-TIFF run."""

    @staticmethod
    def _options(**overrides):
        kwargs = dict(
            space_required=300.0,
            available=200,
            mode_on=False,
            file_saving_option=control._def.FileSavingOption.OME_TIFF,
            nt=3,
            split_on=False,
        )
        kwargs.update(overrides)
        return control.widgets.preflight_disk_options(**kwargs)

    def test_ome_tiff_multi_timepoint_with_split_off_offers_the_split(self):
        assert self._options().offer_split_ome is True

    def test_single_timepoint_does_not_offer_the_split(self):
        assert self._options(nt=1).offer_split_ome is False

    def test_split_already_on_does_not_offer_it_again(self):
        assert self._options(split_on=True).offer_split_ome is False

    @pytest.mark.parametrize(
        "file_saving_option",
        [
            control._def.FileSavingOption.ZARR_V3,
            control._def.FileSavingOption.INDIVIDUAL_IMAGES,
            control._def.FileSavingOption.MULTI_PAGE_TIFF,
            None,
        ],
    )
    def test_other_formats_do_not_offer_the_split(self, file_saving_option):
        assert self._options(file_saving_option=file_saving_option).offer_split_ome is False

    def test_enough_space_does_not_offer_the_split(self):
        assert self._options(space_required=1.0).offer_split_ome is False

    def test_the_split_is_offered_even_when_the_mode_is_already_on(self):
        assert self._options(mode_on=True).offer_split_ome is True

    def test_defaults_keep_the_pre_split_behaviour(self):
        """Callers that pass only the original three arguments never see the split offer."""
        assert control.widgets.preflight_disk_options(300.0, 200, False).offer_split_ome is False


class TestCheckSpaceAvailable:
    """The wrapper, with the dialog stubbed out."""

    def test_enough_space_returns_true_without_dialog(self, controller, test_logger, free_space, dialog_action):
        free_space(10_000)
        recorded = dialog_action("cancel")

        assert control.widgets.check_space_available_with_error_dialog(controller, test_logger) is True
        assert recorded == []
        assert controller.large_acquisition_mode_calls == []

    def test_enable_turns_the_mode_on_for_this_run(
        self, controller, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
        free_space(10)
        recorded = dialog_action("enable")

        assert control.widgets.check_space_available_with_error_dialog(controller, test_logger) is True
        assert controller.large_acquisition_mode_calls == [True]
        assert controller.split_ome_timepoints_calls == []
        assert len(recorded) == 1
        message, options = recorded[0]
        assert options.offer_enable_mode is True
        assert "42" in message  # image count
        assert "Large acquisition mode" in message

    def test_cancel_returns_false_and_leaves_the_mode_alone(
        self, controller, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
        free_space(10)
        dialog_action("cancel")

        assert control.widgets.check_space_available_with_error_dialog(controller, test_logger) is False
        assert controller.large_acquisition_mode_calls == []
        assert controller.split_ome_timepoints_calls == []

    def test_mode_already_on_offers_continue(self, controller, test_logger, free_space, dialog_action, monkeypatch):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", True)
        free_space(10)
        recorded = dialog_action("continue")

        assert control.widgets.check_space_available_with_error_dialog(controller, test_logger) is True
        # The mode is already on in Settings, so there is nothing to enable for this run.
        assert controller.large_acquisition_mode_calls == []
        assert recorded[0][1].offer_enable_mode is False

    def test_mode_already_on_cancel_returns_false(
        self, controller, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", True)
        free_space(10)
        dialog_action("cancel")

        assert control.widgets.check_space_available_with_error_dialog(controller, test_logger) is False
        assert controller.large_acquisition_mode_calls == []


class TestCheckSpaceAvailableSplitOme:
    """The wrapper's OME-TIFF split offer, with the dialog stubbed out."""

    @pytest.fixture
    def ome_multi_timepoint(self, controller, monkeypatch):
        """A run that would produce a multi-timepoint OME-TIFF with the split off."""
        monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)
        monkeypatch.setattr(control._def, "OME_TIFF_SPLIT_TIMEPOINTS", False)
        controller.Nt = 3
        return controller

    def test_split_sets_both_flags_for_this_run(
        self, ome_multi_timepoint, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
        free_space(10)
        recorded = dialog_action("split")

        assert control.widgets.check_space_available_with_error_dialog(ome_multi_timepoint, test_logger) is True
        assert ome_multi_timepoint.split_ome_timepoints_calls == [True]
        # Splitting is only useful if the run also pauses instead of dying when the disk fills up.
        assert ome_multi_timepoint.large_acquisition_mode_calls == [True]
        assert recorded[0][1].offer_split_ome is True

    def test_split_with_the_mode_already_on_only_sets_the_split(
        self, ome_multi_timepoint, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", True)
        free_space(10)
        recorded = dialog_action("split")

        assert control.widgets.check_space_available_with_error_dialog(ome_multi_timepoint, test_logger) is True
        assert ome_multi_timepoint.split_ome_timepoints_calls == [True]
        assert ome_multi_timepoint.large_acquisition_mode_calls == []
        assert recorded[0][1].offer_split_ome is True

    def test_cancel_leaves_the_split_alone(
        self, ome_multi_timepoint, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
        free_space(10)
        dialog_action("cancel")

        assert control.widgets.check_space_available_with_error_dialog(ome_multi_timepoint, test_logger) is False
        assert ome_multi_timepoint.split_ome_timepoints_calls == []
        assert ome_multi_timepoint.large_acquisition_mode_calls == []

    def test_single_timepoint_ome_run_is_not_offered_the_split(
        self, ome_multi_timepoint, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
        ome_multi_timepoint.Nt = 1
        free_space(10)
        recorded = dialog_action("enable")

        assert control.widgets.check_space_available_with_error_dialog(ome_multi_timepoint, test_logger) is True
        assert recorded[0][1].offer_split_ome is False

    def test_split_already_on_globally_is_not_offered_again(
        self, ome_multi_timepoint, test_logger, free_space, dialog_action, monkeypatch
    ):
        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
        monkeypatch.setattr(control._def, "OME_TIFF_SPLIT_TIMEPOINTS", True)
        free_space(10)
        recorded = dialog_action("enable")

        assert control.widgets.check_space_available_with_error_dialog(ome_multi_timepoint, test_logger) is True
        assert recorded[0][1].offer_split_ome is False
