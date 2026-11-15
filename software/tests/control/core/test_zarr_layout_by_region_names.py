"""The Zarr plate layout is chosen only when every region is a well.

"Select Wells" with no well ticked keeps a stale "current" region; the worker used to take the
xy mode alone as the plate signal and SaveZarrJob died on parse_well_id("current").
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from control.core.multi_point_worker import MultiPointWorker


def _worker(region_names):
    return SimpleNamespace(scan_region_names=region_names, _log=MagicMock())


@pytest.mark.parametrize("names", [["A1"], ["B12", "C3"], ["AA1"]])
def test_all_well_ids_count_as_wells(names):
    assert MultiPointWorker._all_regions_are_wells(_worker(names)) is True


@pytest.mark.parametrize("names", [["current"], ["A1", "current"], [""], [], ["region_0"]])
def test_a_non_well_region_rules_the_plate_layout_out(names):
    assert MultiPointWorker._all_regions_are_wells(_worker(names)) is False
