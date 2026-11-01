"""Split the test suite into CI shards by whole file, balanced by expected run time.

CI runs SQUID_TEST_SHARD=1/3, 2/3 and 3/3 in parallel jobs (see tests/conftest.py). Whole files stay
together, so tests in one file keep sharing one process in the same order as a full run.
"""

from typing import Dict, Iterable, Set, Tuple

# Seconds per file on CI (ubuntu-22.04 runner, master run 41276185771, 2026-11-01), for files over 10 s.
# Other files count DEFAULT_SECONDS_PER_TEST per test. These numbers only steer how evenly the shards
# balance, never which tests run: a stale or missing entry makes one shard a little longer, nothing more.
SLOW_FILE_SECONDS: Dict[str, float] = {
    "tests/control/test_aion_hw_trigger.py": 60,
    "tests/control/test_MultiPointController.py": 55,
    "tests/squid/test_filter_wheel_restart.py": 40,
    "tests/control/test_acquisition_duty_cycle.py": 37,
    "tests/control/test_widgets.py": 35,
    "tests/control/test_laser_engine_v2_wiring.py": 30,
    "tests/control/test_microscope.py": 21,
    "tests/control/test_microcontroller.py": 19,
    "tests/control/test_napari_multichannel_widget.py": 16,
    "tests/squid/test_filter_wheel.py": 16,
    "tests/control/test_laser_engine_v2_bench.py": 14,
    "tests/control/core/test_scan_coordinates.py": 14,
    "tests/control/test_filter_wheel_tuning_dialog.py": 11,
    "tests/control/core/test_acquisition_settings.py": 11,
    "tests/squid/test_stage.py": 11,
}
DEFAULT_SECONDS_PER_TEST = 0.15


def parse_shard(spec: str) -> Tuple[int, int]:
    """'2/3' -> (2, 3). Raises ValueError unless it is 'index/total' with 1 <= index <= total."""
    parts = spec.split("/")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise ValueError(f"SQUID_TEST_SHARD must look like '1/3', got {spec!r}")
    index, total = int(parts[0]), int(parts[1])
    if not 1 <= index <= total:
        raise ValueError(f"SQUID_TEST_SHARD index must be 1..total, got {spec!r}")
    return index, total


def files_for_shard(files: Iterable[str], tests_per_file: Dict[str, int], index: int, total: int) -> Set[str]:
    """The test files shard `index` (1-based) of `total` runs.

    Longest first, each file goes to the shard with the least expected time so far. Ties break on the file
    name, so every shard computes the same split whatever order the files were collected in.
    """

    def seconds(f):
        return SLOW_FILE_SECONDS.get(f, DEFAULT_SECONDS_PER_TEST * tests_per_file.get(f, 1))

    loads = [0.0] * total
    shard_of = {}
    for f in sorted(set(files), key=lambda f: (-seconds(f), f)):
        lightest = loads.index(min(loads))
        shard_of[f] = lightest
        loads[lightest] += seconds(f)
    return {f for f, shard in shard_of.items() if shard == index - 1}
