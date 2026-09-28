"""Tests for picking the saving job classes from the file saving format in effect."""

import pytest

from control._def import FileSavingOption
from control.core.job_processing import SaveImageJob, SaveOMETiffJob, SaveZarrJob
from control.core.multi_point_worker import save_job_classes_for_format


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
