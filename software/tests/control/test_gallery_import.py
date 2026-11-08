"""Importing saved acquisition folders and protocol runs into the acquisition history."""

import os
import shutil
from datetime import datetime

import pytest

from control.gallery_import import (
    RECORDED_METADATA_KEYS,
    import_folder,
    is_snap_folder,
    metadata_from_folder,
    protocol_rounds,
)
from squid.acquisition_history import HistoryStore, entry_id_for
from tests.control.gallery_fixtures import saving_root, write_acquisition, write_protocol_run  # noqa: F401

START = 1762596000.0


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history")


def test_import_adds_each_acquisition_once(store, saving_root):
    a = write_acquisition(saving_root, "a_2026-11-08_10-00-00.000000", start_time=START)
    b = write_acquisition(saving_root, "b_2026-11-08_11-00-00.000000", start_time=START + 3600)

    first = import_folder(store, str(saving_root), include_snaps=False)
    second = import_folder(store, str(saving_root), include_snaps=False)

    assert sorted(first.added) == sorted([entry_id_for(str(a)), entry_id_for(str(b))])
    assert first.skipped_existing == 0 and first.errors == []
    assert second.added == [] and second.skipped_existing == 2
    assert len(store.load()) == 2
    e = store.entry(entry_id_for(str(a)))
    assert e.source == "imported" and e.kind == "run" and e.outcome == "complete" and e.started_at == START
    assert e.metadata["objective"] == "20x" and e.metadata["image_format"] == "TIFF images"


def test_import_skips_snaps_unless_asked(store, saving_root):
    snap = write_acquisition(saving_root, "snapped images_2026-11-08_10-00-00.000000", start_time=START)
    assert is_snap_folder(str(snap)) and not is_snap_folder(str(saving_root))

    skipped = import_folder(store, str(saving_root), include_snaps=False)
    assert skipped.added == [] and skipped.skipped_snaps == 1 and store.load() == []

    included = import_folder(store, str(saving_root), include_snaps=True)
    assert included.skipped_snaps == 0 and len(included.added) == 1
    assert store.load()[0].kind == "snap"


def test_import_dates_old_folders_from_their_name(store, saving_root):
    folder = write_acquisition(saving_root, "old_2026-05-05_13-02-12.243959", with_yaml=False)

    import_folder(store, str(saving_root), include_snaps=False)

    e = store.entry(entry_id_for(str(folder)))
    assert e.started_at == pytest.approx(datetime(2026, 5, 5, 13, 2, 12, 243959).timestamp())


def test_import_of_a_folder_with_no_images_is_unknown(store, saving_root):
    folder = write_acquisition(saving_root, "empty_2026-11-08_10-00-00.000000", start_time=START)
    shutil.rmtree(folder / "0")
    shutil.rmtree(folder / "1")
    import_folder(store, str(saving_root), include_snaps=False)
    assert store.entry(entry_id_for(str(folder))).outcome == "unknown"


def test_import_accepts_an_acquisition_folder_itself(store, saving_root):
    folder = write_acquisition(saving_root, "solo_2026-11-08_10-00-00.000000", start_time=START)
    result = import_folder(store, str(folder), include_snaps=False)
    assert result.added == [entry_id_for(str(folder))]


def test_import_groups_a_protocol_run(store, saving_root):
    run = write_protocol_run(saving_root, "run_7", rounds=3, status="finished")

    result = import_folder(store, str(saving_root), include_snaps=False)

    assert result.errors == []
    entries = store.load()
    assert len(entries) == 1
    parent = entries[0]
    assert parent.kind == "protocol_run" and parent.name == "run_7" and parent.id == entry_id_for(str(run))
    assert len(parent.rounds) == 3 and all(r.parent == parent.id for r in parent.rounds)
    assert parent.outcome == "complete" and not parent.is_running
    assert sorted(r.protocol["round"] for r in parent.rounds) == [1, 2, 3]
    assert all(r.source == "imported" for r in parent.rounds)
    assert len(result.added) == 4


def test_a_protocol_run_already_in_the_history_is_skipped_whole(store, saving_root):
    write_protocol_run(saving_root, "run_7", rounds=2)
    import_folder(store, str(saving_root), include_snaps=False)
    again = import_folder(store, str(saving_root), include_snaps=False)
    assert again.added == [] and again.skipped_existing == 1


@pytest.mark.parametrize(
    "status, outcome, reason",
    [
        ("stopped", "aborted", "stopped"),
        ("failed", "failed", "failed"),
        ("running", "complete", "derived"),  # never closed, but every round is complete
        ("paused", "complete", "derived"),
        (None, "complete", "derived"),  # no manifest
    ],
)
def test_imported_protocol_outcome_comes_from_the_manifest_or_the_rounds(store, saving_root, status, outcome, reason):
    run = write_protocol_run(saving_root, "run_7", rounds=2, status=status)
    import_folder(store, str(saving_root), include_snaps=False)
    parent = store.entry(entry_id_for(str(run)))
    assert parent.outcome == outcome and parent.reason == reason and not parent.is_running


def test_a_derived_protocol_outcome_is_aborted_when_a_round_is_incomplete(store, saving_root):
    run = write_protocol_run(saving_root, "run_7", rounds=2, status=None)
    shutil.rmtree(run / "round_2_image" / "0")
    shutil.rmtree(run / "round_2_image" / "1")
    import_folder(store, str(saving_root), include_snaps=False)
    parent = store.entry(entry_id_for(str(run)))
    assert parent.outcome == "aborted" and parent.reason == "derived"


def test_import_calls_make_preview_for_added_entries(store, saving_root):
    a = write_acquisition(saving_root, "a_2026-11-08_10-00-00.000000", start_time=START)
    run = write_protocol_run(saving_root, "run_7", rounds=2)
    calls = []

    result = import_folder(store, str(saving_root), include_snaps=False, make_preview=lambda f, i: calls.append((f, i)))

    called = [(os.path.normpath(f), i) for f, i in calls]
    assert (os.path.normpath(str(a)), entry_id_for(str(a))) in called
    for r in store.entry(entry_id_for(str(run))).rounds:
        assert (os.path.normpath(r.folder), r.id) in called
    assert len(calls) == 3 and {i for _, i in calls} <= set(result.added)
    calls.clear()
    import_folder(store, str(saving_root), include_snaps=False, make_preview=lambda f, i: calls.append((f, i)))
    assert calls == []


def test_protocol_rounds_orders_by_round_and_attempt(saving_root):
    run = write_protocol_run(saving_root, "run_7", rounds=[(2, 1), (1, 2), (1, 1), (3, 1)])
    names = [os.path.basename(p) for p in protocol_rounds(str(run))]
    assert names == ["round_1_image", "round_1_image_attempt2", "round_2_image", "round_3_image"]
    plain = write_acquisition(saving_root, "plain")
    assert protocol_rounds(str(plain)) == [] and protocol_rounds(str(saving_root / "missing")) == []


def test_metadata_from_folder_matches_the_recorder_key_set(saving_root):
    folder = write_acquisition(saving_root, "plate", start_time=START)
    md = metadata_from_folder(str(folder))
    assert set(md) == set(RECORDED_METADATA_KEYS) and tuple(md) == RECORDED_METADATA_KEYS
    assert md["channels"] == ["BF LED matrix full", "Fluorescence 488 nm Ex"]
    assert md["num_regions"] == 2 and md["num_fovs"] == 8 and md["nz"] == 3 and md["nt"] == 2
    assert md["dz_um"] == pytest.approx(1.0) and md["start_time"] == START and md["protocol"] is None
    bare = metadata_from_folder(str(saving_root / "nothing"))
    assert set(bare) == set(RECORDED_METADATA_KEYS) and bare["channels"] == []


def test_import_of_an_unreadable_root_reports_an_error_not_an_exception(store, tmp_path):
    result = import_folder(store, str(tmp_path / "does_not_exist"), include_snaps=False)
    assert result.added == [] and len(result.errors) == 1


def test_a_raising_make_preview_is_reported_and_never_leaves_a_run_open(store, saving_root):
    plain = write_acquisition(saving_root, "a_2026-11-08_10-00-00.000000", start_time=START)
    run = write_protocol_run(saving_root, "run_7", rounds=3)

    def boom(folder, entry_id):
        raise RuntimeError("no preview")

    result = import_folder(store, str(saving_root), include_snaps=False, make_preview=boom)

    assert len(result.added) == 5 and len(result.errors) == 4  # plain + run + 3 rounds; one error per preview
    parent = store.entry(entry_id_for(str(run)))
    assert parent.outcome == "complete" and not parent.is_running and len(parent.rounds) == 3
    assert store.entry(entry_id_for(str(plain))) is not None


def test_a_root_that_is_a_protocol_run_is_imported_as_one_grouped_run(store, saving_root):
    run = write_protocol_run(saving_root, "run_7", rounds=2)

    result = import_folder(store, str(run), include_snaps=False)

    entries = store.load()
    assert len(entries) == 1 and entries[0].kind == "protocol_run" and len(entries[0].rounds) == 2
    assert len(result.added) == 3


def test_a_run_whose_rounds_are_all_known_is_skipped_whole(store, saving_root):
    run = write_protocol_run(saving_root, "run_7", rounds=2)
    for r in protocol_rounds(str(run)):  # the rounds are in the history, but not their parent run
        import_folder(store, r, include_snaps=False)
    before = len(store.events())

    result = import_folder(store, str(saving_root), include_snaps=False)

    assert result.added == [] and result.skipped_existing == 1 and len(store.events()) == before


def test_a_partly_known_run_imports_only_the_unknown_rounds_under_the_parent(store, saving_root):
    run = write_protocol_run(saving_root, "run_7", rounds=3, status=None)
    first = protocol_rounds(str(run))[0]
    import_folder(store, first, include_snaps=False)

    result = import_folder(store, str(saving_root), include_snaps=False)

    assert result.skipped_existing == 1 and len(result.added) == 3  # the parent and two rounds
    parent = store.entry(entry_id_for(str(run)))
    assert len(parent.rounds) == 2 and entry_id_for(first) not in [r.id for r in parent.rounds]
    assert parent.outcome == "complete" and parent.reason == "derived"
