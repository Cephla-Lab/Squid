"""Tests for picking the saving job classes from the file saving format in effect."""

import inspect

import pytest

import control._def
from control._def import FileSavingOption
from control.core.job_processing import SaveImageJob, SaveOMETiffJob, SaveZarrJob
from control.core.multi_point_worker import MultiPointWorker, save_job_classes_for_format


@pytest.mark.parametrize(
    "file_saving_option, expected",
    [
        (FileSavingOption.INDIVIDUAL_IMAGES, [SaveImageJob]),
        (FileSavingOption.MULTI_PAGE_TIFF, [SaveImageJob]),
        (FileSavingOption.OME_TIFF, [SaveOMETiffJob]),
        (FileSavingOption.ZARR_V3, [SaveZarrJob]),
    ],
)
def test_save_job_classes_for_format(file_saving_option, expected):
    assert save_job_classes_for_format(file_saving_option) == expected


def test_worker_reads_the_format_through_the_def_module():
    """The worker must not use the star-imported FILE_SAVING_OPTION.

    ``multi_point_worker`` does ``from control._def import *``, which binds a copy of the value at
    import time, so a format picked in Preferences (which assigns control._def.FILE_SAVING_OPTION)
    never reached the bare name.  This is checked against the source because building a
    MultiPointWorker requires a full microscope.
    """
    source = inspect.getsource(MultiPointWorker.__init__)

    assert "control._def.FILE_SAVING_OPTION" in source
    assert "= FILE_SAVING_OPTION" not in source
    assert "FILE_SAVING_OPTION ==" not in source


def test_def_module_still_exposes_the_format():
    # Guards the assertion above against a rename of the setting.
    assert isinstance(control._def.FILE_SAVING_OPTION, FileSavingOption)
