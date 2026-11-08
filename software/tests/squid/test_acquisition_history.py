import os

import squid.acquisition_history as ah


def _started(id, at, name=None, folder="x", **extra):
    event = {
        "type": "started",
        "id": id,
        "kind": "run",
        "name": name or id,
        "folder": folder,
        "at": at,
        "metadata": {},
        "parent": None,
        "protocol": None,
    }
    event.update(extra)
    return event


def _finished(id, at, outcome="complete", **extra):
    event = {
        "type": "finished",
        "id": id,
        "at": at,
        "outcome": outcome,
        "reason": "completed",
        "fraction_done": 1.0,
        "images": 1,
        "ended_at": at,
    }
    event.update(extra)
    return event


def test_history_dir_honours_the_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv(ah.ENV_VAR, str(tmp_path / "h"))
    assert ah.history_dir() == tmp_path / "h"


def test_entry_id_differs_by_folder_and_is_stable(tmp_path):
    name = "plate_3_2026-11-06_09-12-33"
    one = os.path.join(str(tmp_path), "data", name)
    two = os.path.join(str(tmp_path), "other", name)
    a = ah.entry_id_for(one)
    b = ah.entry_id_for(two)
    assert a != b and a.startswith(name + "-")
    assert a == ah.entry_id_for(one)


def test_folder_availability(tmp_path):
    assert ah.folder_availability(str(tmp_path)) == "online"
    assert ah.folder_availability(str(tmp_path / "gone")) == "folder_missing"
    gone = "Q:/data/run" if os.name == "nt" else "/mnt/gone/run"
    assert ah.folder_availability(gone, exists=lambda p: False) == "drive_offline"


def test_started_then_finished_folds_into_one_complete_entry(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("r-1", 100.0, name="plate_3", folder=str(tmp_path / "plate_3"), metadata={"nz": 5}))
    [running] = store.load()
    assert running.is_running and running.outcome == "running" and running.started_at == 100.0
    store.append(_finished("r-1", 160.0, images=40))
    store.append(
        {
            "type": "preview",
            "id": "r-1",
            "at": 161.0,
            "file": "r-1.png",
            "channels": ["BF LED matrix full"],
            "bytes_on_disk": 2048,
        }
    )
    [done] = store.load()
    assert (done.outcome, done.ended_at, done.images, done.preview, done.preview_channels, done.bytes_on_disk) == (
        "complete",
        160.0,
        40,
        "r-1.png",
        ["BF LED matrix full"],
        2048,
    )
    assert done.metadata == {"nz": 5} and done.source == "recorded"


def test_truncated_last_line_is_dropped_and_later_appends_stay_valid(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("a", 1.0, folder="x"))
    with open(store.path, "a", encoding="utf-8") as f:
        f.write('{"type": "finished", "id": "a", "at": 2.0, "outc')  # cut mid-write, no newline
    assert [e.id for e in store.load()] == ["a"] and store.load()[0].outcome == "running"
    store.append(_finished("a", 3.0))
    store.append({"type": "preview", "id": "a", "at": 4.0, "file": "a.png", "channels": [], "bytes_on_disk": 1})
    [a] = store.load()
    assert a.outcome == "complete" and a.preview == "a.png"  # the cut line was terminated, not extended
    assert len(store.events()) == 3 and store.path.read_text(encoding="utf-8").count("\n") == 4


def test_malformed_line_in_the_middle_is_skipped_and_warned_once(tmp_path, caplog):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("a", 1.0))
    with open(store.path, "a", encoding="utf-8") as f:
        f.write("not json\n\n[1, 2]\n")
    store.append(_finished("a", 2.0))
    with caplog.at_level("WARNING"):
        events = store.events()
    assert [e["type"] for e in events] == ["started", "finished"]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "2" in warnings[0].getMessage()  # "not json" and "[1, 2]"; blank line is silent


def test_newest_first_and_removed_entries_disappear(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("a", 10.0))
    store.append(_started("b", 20.0))
    store.append(
        {
            "type": "imported",
            "id": "c",
            "at": 500.0,
            "kind": "run",
            "name": "c",
            "folder": "cx",
            "metadata": {},
            "started_at": 15.0,
            "outcome": "complete",
            "parent": None,
            "protocol": None,
        }
    )
    assert [e.id for e in store.load()] == ["b", "c", "a"]
    store.append({"type": "removed", "id": "b", "at": 600.0})
    entries = store.load()
    assert [e.id for e in entries] == ["c", "a"]
    assert entries[0].source == "imported" and entries[0].outcome == "complete"
    assert store.entry("b") is None and store.entry("a").id == "a"


def test_relocated_changes_the_folder(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("a", 1.0, folder="old"))
    store.append({"type": "relocated", "id": "a", "at": 2.0, "folder": str(tmp_path / "new")})
    assert store.load()[0].folder == str(tmp_path / "new")


def test_rounds_group_under_a_protocol_run_and_the_parent_summarises_them(tmp_path):
    store = ah.HistoryStore(tmp_path)
    proto = {"protocol": "seqFISH", "run_name": "run_7", "round": 1, "step": "image", "attempt": 1}
    run_dir = str(tmp_path / "run_7")
    parent_id = ah.entry_id_for(run_dir)
    store.append(
        _started(
            "r1",
            10.0,
            name="round_1_image",
            folder=os.path.join(run_dir, "round_1_image"),
            parent=parent_id,
            protocol=proto,
        )
    )
    store.append(_finished("r1", 20.0, images=8))
    store.append({"type": "preview", "id": "r1", "at": 21.0, "file": "r1.png", "channels": ["488"]})
    store.append(
        _started(
            "r2",
            30.0,
            name="round_2_image",
            folder=os.path.join(run_dir, "round_2_image"),
            parent=parent_id,
            protocol={**proto, "round": 2},
        )
    )
    [parent] = store.load()
    assert parent.kind == "protocol_run" and parent.name == "run_7" and parent.folder == run_dir
    assert [r.id for r in parent.rounds] == ["r1", "r2"] and parent.outcome == "running"
    assert parent.started_at == 10.0 and parent.preview == "r1.png"
    store.append(_finished("r2", 40.0, outcome="aborted", reason="user_abort", fraction_done=0.5, images=4))
    assert store.load()[0].outcome == "aborted" and store.load()[0].ended_at == 40.0


def test_a_recorded_protocol_run_follows_its_own_lifecycle_not_its_rounds(tmp_path):
    store = ah.HistoryStore(tmp_path)
    proto = {"protocol": "seqFISH", "run_name": "run_7", "round": 1, "step": "image", "attempt": 1}
    run_dir = str(tmp_path / "run_7")
    parent_id = ah.entry_id_for(run_dir)
    store.append(
        {
            "type": "protocol_started",
            "id": parent_id,
            "at": 5.0,
            "name": "run_7",
            "folder": run_dir,
            "protocol": {"protocol": "seqFISH", "run_name": "run_7"},
            "pid": 99,
            "pid_started_at": 1.5,
            "machine": "scope-1",
        }
    )
    [parent] = store.load()
    assert parent.kind == "protocol_run" and parent.outcome == "running" and parent.started_at == 5.0
    assert (parent.pid, parent.pid_started_at, parent.machine) == (99, 1.5, "scope-1")
    store.append(
        _started(
            "r1",
            10.0,
            name="round_1_image",
            folder=os.path.join(run_dir, "round_1_image"),
            parent=parent_id,
            protocol=proto,
        )
    )
    store.append(_finished("r1", 20.0))
    [parent] = store.load()
    assert parent.outcome == "running" and [r.id for r in parent.rounds] == ["r1"]  # fluidics continues
    store.append(
        {
            "type": "protocol_finished",
            "id": parent_id,
            "at": 90.0,
            "outcome": "failed",
            "reason": "boom",
            "ended_at": 90.0,
        }
    )
    [parent] = store.load()
    assert (parent.outcome, parent.ended_at, parent.reason) == ("failed", 90.0, "boom")
    assert parent.rounds[0].outcome == "complete" and parent.started_at == 5.0


def test_finished_without_started_is_ignored(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_finished("ghost", 2.0))
    assert store.load() == [] and store.entry("ghost") is None


def test_started_carries_pid_and_machine(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("a", 1.0, pid=4321, pid_started_at=1700000000.5, machine="scope-1"))
    [e] = store.load()
    assert e.pid == 4321 and e.pid_started_at == 1700000000.5 and e.machine == "scope-1"


def test_a_later_preview_event_replaces_the_earlier_one(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("a", 1.0))
    store.append({"type": "preview", "id": "a", "at": 2.0, "file": "a.png", "channels": ["x"], "bytes_on_disk": 1})
    store.append({"type": "preview", "id": "a", "at": 3.0, "file": "b.png", "channels": ["y"], "bytes_on_disk": 2})
    [e] = store.load()
    assert e.preview == "b.png" and e.preview_channels == ["y"] and e.bytes_on_disk == 2


def test_restarting_an_id_keeps_the_earlier_preview(tmp_path):
    store = ah.HistoryStore(tmp_path)
    store.append(_started("a", 1.0, name="old"))
    store.append({"type": "preview", "id": "a", "at": 2.0, "file": "a.png", "channels": [], "bytes_on_disk": 1})
    store.append(_started("a", 5.0, name="new"))
    [e] = store.load()
    assert e.name == "new" and e.started_at == 5.0 and e.preview == "a.png" and e.outcome == "running"


def test_implicit_parent_is_complete_when_every_round_completed(tmp_path):
    store = ah.HistoryStore(tmp_path)
    run_dir = str(tmp_path / "run_9")
    proto = {"protocol": "p", "run_name": "run_9", "round": 1, "step": "image", "attempt": 1}
    for i, t in enumerate((10.0, 30.0), start=1):
        store.append(
            _started(
                f"r{i}", t, folder=os.path.join(run_dir, f"round_{i}"), parent=ah.entry_id_for(run_dir), protocol=proto
            )
        )
        store.append(_finished(f"r{i}", t + 5))
    [parent] = store.load()
    assert parent.outcome == "complete" and parent.ended_at == 35.0


def test_removing_a_parent_removes_its_rounds(tmp_path):
    store = ah.HistoryStore(tmp_path)
    run_dir = str(tmp_path / "run_9")
    pid = ah.entry_id_for(run_dir)
    proto = {"protocol": "p", "run_name": "run_9"}
    store.append(_started("r1", 10.0, folder=os.path.join(run_dir, "r1"), parent=pid, protocol=proto))
    store.append({"type": "removed", "id": pid, "at": 20.0})
    assert store.load() == []
