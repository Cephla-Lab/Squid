import random

import pytest

from tests.sharding import SLOW_FILE_SECONDS, files_for_shard, parse_shard

# The slow files plus 150 small ones, in collection order.
FILES = sorted(SLOW_FILE_SECONDS) + [f"tests/small/test_{i:03d}.py" for i in range(150)]
TESTS_PER_FILE = {f: 1 + i % 40 for i, f in enumerate(FILES)}


def _shards(files, total):
    return [files_for_shard(files, TESTS_PER_FILE, index, total) for index in range(1, total + 1)]


def test_every_file_runs_in_exactly_one_shard():
    shards = _shards(FILES, 3)

    assert set().union(*shards) == set(FILES)
    assert sum(len(s) for s in shards) == len(FILES)


def test_the_split_does_not_depend_on_collection_order():
    shuffled = FILES[:]
    random.Random(0).shuffle(shuffled)

    assert _shards(shuffled, 3) == _shards(FILES, 3)


def test_the_three_slowest_files_go_to_different_shards():
    slowest = sorted(SLOW_FILE_SECONDS, key=SLOW_FILE_SECONDS.get, reverse=True)[:3]
    shards = _shards(FILES, 3)

    assert sorted(next(i for i, s in enumerate(shards) if f in s) for f in slowest) == [0, 1, 2]


def test_one_shard_runs_everything():
    assert files_for_shard(FILES, TESTS_PER_FILE, 1, 1) == set(FILES)


def test_a_shard_spec_reads_as_index_and_total():
    assert parse_shard("2/3") == (2, 3)


@pytest.mark.parametrize("spec", ["0/3", "4/3", "1/0", "3", "a/b", "1/3/5", ""])
def test_a_malformed_shard_spec_is_refused(spec):
    with pytest.raises(ValueError):
        parse_shard(spec)
