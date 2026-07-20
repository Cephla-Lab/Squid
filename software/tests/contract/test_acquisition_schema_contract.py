"""Contract tests: acquisition-YAML schema + persistence, frozen on THIS branch.

These tests PIN the acquisition-YAML schema exactly as it exists on PR #578
(wells-by-name + z_reference). They must NOT include fov_pattern / well_z_offsets_um
/ z_plan -- those belong to PR #593 and must fail loudly here if they ever land
without a deliberate re-pin.

Each test pins a CONTRACT: an exact key set, an exact value, or an exact error
message -- not merely a behavior. The file is intentionally self-contained: it
defines its own fixtures and does not rely on the (concurrently authored)
tests/contract/conftest.py.
"""

import dataclasses
import datetime as _dt
import json
import threading

import pytest
import yaml

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.acquisition_yaml_loader import (
    AcquisitionYAMLData,
    parse_acquisition_yaml,
    validate_hardware,
)
from control.core.multi_point_controller import NoOpCallbacks
from squid_service import faults as F
from squid_service.jobs import JobOutcome, JobResult, JobStore
from squid_service.methods import MethodRegistry
from squid_service.wells import (
    index_to_row,
    parse_well_names,
    row_to_index,
    well_center_mm,
)

_PLATE_96 = "96 well plate"


def _write(tmp_path, body: str) -> str:
    path = tmp_path / "acq.yaml"
    path.write_text(body)
    return str(path)


# =====================================================================
# 1. Full-document parse golden
# =====================================================================


def test_full_document_parse_golden(tmp_path):
    # Pins: every AcquisitionYAMLData field for a maximal wellplate YAML, incl. mm->um.
    body = """
acquisition:
  widget_type: wellplate
  xy_mode: "Select Wells"
objective:
  name: "X"
  magnification: 20
  pixel_size_um: 0.33
  camera_binning: [2, 2]
z_stack:
  nz: 5
  delta_z_mm: 0.002
  config: "FROM CENTER"
  use_piezo: true
time_series:
  nt: 3
  delta_t_s: 1.5
channels:
  - name: "BF LED matrix full"
  - name: "Fluorescence 488 nm Ex"
autofocus:
  contrast_af: true
  laser_af: false
wellplate_scan:
  wells: "A1:B3"
  scan_size_mm: 0.8
  overlap_percent: 15
"""
    r = parse_acquisition_yaml(_write(tmp_path, body))

    assert r.widget_type == "wellplate"
    assert r.xy_mode == "Select Wells"
    assert r.objective_name == "X"
    assert r.objective_magnification == 20
    assert r.objective_pixel_size_um == 0.33
    assert r.camera_binning == (2, 2)
    assert r.nz == 5
    assert r.delta_z_um == 2.0  # 0.002 mm * 1000 -> um conversion pinned
    assert r.z_stacking_config == "FROM CENTER"
    assert r.use_piezo is True
    assert r.nt == 3
    assert r.delta_t_s == 1.5
    assert r.channel_names == ["BF LED matrix full", "Fluorescence 488 nm Ex"]  # order preserved
    assert r.contrast_af is True
    assert r.laser_af is False
    assert r.scan_size_mm == 0.8
    assert r.overlap_percent == 15
    assert r.scan_shape is None  # no regions -> no first-region shape
    assert r.wellplate_regions is None
    assert r.wells == "A1:B3"  # string preserved verbatim (not expanded)


# =====================================================================
# 2. Defaults golden
# =====================================================================


def test_defaults_golden(tmp_path):
    # Pins: every schema default when a minimal regions-only wellplate YAML is parsed.
    body = """
acquisition:
  widget_type: wellplate
channels:
  - name: "BF LED matrix full"
wellplate_scan:
  regions:
    - name: "R"
      center_mm: [10, 10, 1]
      shape: "Square"
"""
    r = parse_acquisition_yaml(_write(tmp_path, body))

    assert r.nz == 1
    assert r.delta_z_um == 1.0  # default delta_z_mm 0.001 * 1000
    assert r.z_stacking_config == "FROM BOTTOM"
    assert r.use_piezo is False
    assert r.nt == 1
    assert r.delta_t_s == 0.0
    assert r.overlap_percent == 10.0
    assert r.contrast_af is False
    assert r.laser_af is False
    assert r.xy_mode == "Select Wells"
    assert r.wells is None
    assert r.scan_size_mm is None
    assert r.scan_shape == "Square"  # taken from the first region
    assert r.wellplate_regions[0]["center_mm"] == [10, 10, 1]


# =====================================================================
# 3. Wells-grammar contract (squid_service.wells)
# =====================================================================


def test_parse_well_names_range_expansion():
    # Pins: row-major expansion order of a rectangular well range.
    assert parse_well_names("A1:B3") == ["A1", "A2", "A3", "B1", "B2", "B3"]


def test_parse_well_names_whitespace_and_case():
    # Pins: whitespace tolerance and lowercase->uppercase normalization.
    assert parse_well_names(" A1 , B2 ") == ["A1", "B2"]
    assert parse_well_names("a1") == ["A1"]


def test_multi_letter_rows_roundtrip():
    # Pins: base-26 row arithmetic for multi-letter rows (AA == 26).
    assert parse_well_names("AA1") == ["AA1"]
    assert row_to_index("AA") == 26
    assert index_to_row(26) == "AA"


def test_reversed_range_rejected():
    # Pins: descending ranges are a hard error with a specific message.
    with pytest.raises(ValueError, match="Range end before start"):
        parse_well_names("B2:A1")


def test_empty_and_malformed_well_names_rejected():
    # Pins: empty/blank selections and digit-first tokens are rejected.
    with pytest.raises(ValueError):
        parse_well_names("")
    with pytest.raises(ValueError):
        parse_well_names("  ")
    with pytest.raises(ValueError):
        parse_well_names("1A")


def test_loader_wells_and_regions_mutually_exclusive(tmp_path):
    # Pins: supplying both wells and non-empty regions is rejected ("not both").
    body = """
acquisition:
  widget_type: wellplate
wellplate_scan:
  wells: "A1"
  regions:
    - name: A1
      center_mm: [14.3, 11.36, 0.5]
      shape: Square
"""
    with pytest.raises(ValueError, match="not both"):
        parse_acquisition_yaml(_write(tmp_path, body))


def test_loader_wells_list_form_joined_with_comma(tmp_path):
    # Pins: a YAML list of well names normalizes to a comma-joined string.
    body = """
acquisition:
  widget_type: wellplate
wellplate_scan:
  wells:
    - A1
    - B2
"""
    assert parse_acquisition_yaml(_write(tmp_path, body)).wells == "A1,B2"


def test_well_center_mm_matches_plate_definition():
    # Pins: well_center_mm == a1 + index*spacing + runtime WELLPLATE_OFFSET (no magic numbers).
    settings = control._def.get_wellplate_settings(_PLATE_96)
    off_x = getattr(control._def, "WELLPLATE_OFFSET_X_mm", 0.0)
    off_y = getattr(control._def, "WELLPLATE_OFFSET_Y_mm", 0.0)
    spacing = settings["well_spacing_mm"]

    a1 = well_center_mm("A1", settings)
    assert a1 == (settings["a1_x_mm"] + off_x, settings["a1_y_mm"] + off_y)

    b2 = well_center_mm("B2", settings)
    assert b2[0] == pytest.approx(a1[0] + spacing, abs=1e-9)
    assert b2[1] == pytest.approx(a1[1] + spacing, abs=1e-9)


def test_well_center_mm_out_of_plate_rejected():
    # Pins: wells outside the plate's row/col extent raise (col overflow and row overflow).
    settings = control._def.get_wellplate_settings(_PLATE_96)
    with pytest.raises(ValueError):
        well_center_mm("H13", settings)  # 96-plate has 12 columns -> col 13 invalid
    with pytest.raises(ValueError):
        well_center_mm("I1", settings)  # 96-plate has 8 rows (A-H) -> row I invalid


# =====================================================================
# 4. Unknown-field policy characterization
# =====================================================================


def test_unknown_fields_silently_ignored(tmp_path):
    # Pins: CURRENT lenient policy -- unknown keys at any level parse without error.
    body = """
frobnicate: 1
acquisition:
  widget_type: wellplate
z_stack:
  nz: 1
  delta_z_mm: 0.001
  bogus: true
wellplate_scan:
  wells: "A1"
  scan_size_mm: 0.5
  well_z_offset_um:
    A1: 3
"""
    r = parse_acquisition_yaml(_write(tmp_path, body))
    # CHARACTERIZATION, not endorsement: the loader currently IGNORES unknown keys
    # silently (a typo like `well_z_offset_um` changes acquisition behavior with no
    # error). The 2026-07-19 schema-v2 contract review recommends strict rejection
    # inside wellplate_scan; when that lands, flip these assertions deliberately.
    assert r.wells == "A1"
    assert r.nz == 1
    assert r.scan_size_mm == 0.5
    assert not hasattr(r, "well_z_offset_um")  # typo'd key never reaches the dataclass


# =====================================================================
# 5. Structural validation goldens
# =====================================================================


def test_invalid_widget_type_names_valid_set(tmp_path):
    # Pins: an unknown widget_type error message enumerates the valid set.
    body = "acquisition:\n  widget_type: banana\n"
    with pytest.raises(ValueError) as exc:
        parse_acquisition_yaml(_write(tmp_path, body))
    msg = str(exc.value)
    assert "Invalid widget_type" in msg
    assert "wellplate" in msg and "flexible" in msg


def test_empty_yaml_file_rejected(tmp_path):
    # Pins: a truly empty document is rejected (not silently defaulted).
    path = tmp_path / "empty.yaml"
    path.write_text("")
    with pytest.raises(ValueError, match="empty or invalid"):
        parse_acquisition_yaml(str(path))


def test_flexible_widget_defaults(tmp_path):
    # Pins: flexible widget with no flexible_scan section uses flexible defaults.
    body = "acquisition:\n  widget_type: flexible\n"
    r = parse_acquisition_yaml(_write(tmp_path, body))
    assert r.widget_type == "flexible"
    assert r.nx == 1
    assert r.ny == 1
    assert r.delta_x_mm == 0.9
    assert r.delta_y_mm == 0.9
    assert r.flexible_positions is None


# =====================================================================
# 6. validate_hardware contract
# =====================================================================


def test_validate_hardware_objective_mismatch_message_has_both_names():
    # Pins: objective mismatch flips is_valid/objective_mismatch and names both objectives.
    data = AcquisitionYAMLData(widget_type="wellplate", objective_name="PlanApo-40x", camera_binning=(1, 1))
    res = validate_hardware(data, current_objective="Air-10x", current_binning=(1, 1))
    assert res.is_valid is False
    assert res.objective_mismatch is True
    assert res.binning_mismatch is False
    assert "PlanApo-40x" in res.message and "Air-10x" in res.message


def test_validate_hardware_binning_mismatch_flag():
    # Pins: a binning-only mismatch sets binning_mismatch (objective still matches).
    data = AcquisitionYAMLData(widget_type="wellplate", objective_name="20x", camera_binning=(4, 4))
    res = validate_hardware(data, current_objective="20x", current_binning=(1, 1))
    assert res.is_valid is False
    assert res.binning_mismatch is True
    assert res.objective_mismatch is False


def test_validate_hardware_full_match_has_empty_message():
    # Pins: a full match is valid with an empty message string.
    data = AcquisitionYAMLData(widget_type="wellplate", objective_name="20x", camera_binning=(2, 2))
    res = validate_hardware(data, current_objective="20x", current_binning=(2, 2))
    assert res.is_valid is True
    assert res.message == ""


def test_validate_hardware_absent_fields_not_checked():
    # Pins: when YAML omits BOTH objective and binning, nothing is validated (is_valid True).
    data = AcquisitionYAMLData(widget_type="wellplate", objective_name=None, camera_binning=None)
    res = validate_hardware(data, current_objective="20x", current_binning=(2, 2))
    assert res.is_valid is True
    assert res.objective_mismatch is False
    assert res.binning_mismatch is False


# =====================================================================
# 7. Acquisition-record round-trip (writer -> loader), via the sim controller
# =====================================================================


@pytest.fixture(scope="module")
def contract_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


@pytest.fixture(scope="module")
def saved_acquisition(contract_scope, tmp_path_factory):
    """Run ONE tiny sim acquisition and return the saved acquisition.yaml artifacts.

    Mirrors the sim run/wait idiom of tests/control/test_MultiPointController.py
    (callbacks + Event.wait), which is proven to terminate for tiny acquisitions.
    """
    tmp = tmp_path_factory.mktemp("contract_rt")

    finished = threading.Event()
    callbacks = dataclasses.replace(NoOpCallbacks, signal_acquisition_finished=finished.set)
    mpc = ts.get_test_multi_point_controller(contract_scope, callbacks=callbacks)

    mpc.set_base_path(str(tmp))
    mpc.start_new_experiment("contract_rt")

    settings = control._def.get_wellplate_settings(_PLATE_96)
    cx, cy = well_center_mm("A1", settings)

    mpc.scanCoordinates.clear_regions()
    mpc.scanCoordinates.add_region(
        well_id="A1", center_x=cx, center_y=cy, scan_size_mm=0.5, overlap_percent=10, shape="Square"
    )
    channel = mpc.liveController.get_channels(mpc.objectiveStore.current_objective)[0].name
    mpc.set_selected_configurations([channel])
    mpc.set_NZ(1)
    mpc.set_Nt(1)

    mpc.run_acquisition()
    assert finished.wait(120.0), "sim acquisition did not finish within the bounded wait"

    matches = list(tmp.rglob("acquisition.yaml"))
    assert matches, "writer did not emit acquisition.yaml under the experiment dir"
    saved = str(matches[0])

    with open(saved, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return {
        "path": saved,
        "raw": raw,
        "data1": parse_acquisition_yaml(saved),
        "data2": parse_acquisition_yaml(saved),
        "center": (cx, cy),
        "channel": channel,
    }


def test_roundtrip_select_wells_saves_regions_not_wells(saved_acquisition):
    # Pins: Select-Wells acquisitions save explicit regions; wells stays None.
    data = saved_acquisition["data1"]
    # PR #593 changes Select-Wells saves to wells+fov_pattern; this pin makes that break deliberate.
    assert data.wells is None
    assert data.wellplate_regions is not None
    assert len(data.wellplate_regions) == 1
    assert data.wellplate_regions[0]["name"] == "A1"


def test_roundtrip_region_center_and_shape(saved_acquisition):
    # Pins: the saved region center round-trips the configured X/Y and shape.
    data = saved_acquisition["data1"]
    cx, cy = saved_acquisition["center"]
    region = data.wellplate_regions[0]
    assert region["center_mm"][0] == pytest.approx(cx, abs=1e-6)
    assert region["center_mm"][1] == pytest.approx(cy, abs=1e-6)
    assert region["shape"] == "Square"
    assert data.scan_shape == "Square"


def test_roundtrip_channels_and_counts(saved_acquisition):
    # Pins: channel_names, nz and nt round-trip the configured single-channel single-plane run.
    data = saved_acquisition["data1"]
    assert data.channel_names == [saved_acquisition["channel"]]
    assert data.nz == 1
    assert data.nt == 1


def test_roundtrip_is_deterministic(saved_acquisition):
    # Pins: parsing the same saved file twice yields equal dataclasses.
    assert saved_acquisition["data1"] == saved_acquisition["data2"]


def test_roundtrip_raw_toplevel_key_set(saved_acquisition):
    # Pins: the EXACT top-level key set the writer emits (observed on this branch).
    assert set(saved_acquisition["raw"].keys()) == {
        "acquisition",
        "objective",
        "sample",
        "z_stack",
        "time_series",
        "autofocus",
        "channels",
        "wellplate_scan",
        "mosaic_view",
        "plate",
        "fluidics",
    }


# =====================================================================
# 8. Method-registry storage contract
# =====================================================================


def _valid_method_config(channel="BF LED matrix full"):
    return {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": channel}],
        "autofocus": {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {
            "scan_size_mm": 0.5,
            "overlap_percent": 10,
            "regions": [{"name": "A1", "center_mm": [14.3, 11.36, 0.5], "shape": "Square"}],
        },
    }


def test_method_save_get_roundtrip_exact_equality(tmp_path):
    # Pins: get() returns a saved nested config (unicode/floats/lists) byte-for-byte equal.
    reg = MethodRegistry(tmp_path)
    cfg = _valid_method_config(channel="Fluorescence 488 nm Ex — üm")
    reg.save("round", cfg, overwrite=False)
    assert reg.get("round")["config"] == cfg


@pytest.mark.parametrize("good_name", ["daily-scan_2", "A1"])
def test_method_valid_names_accepted(tmp_path, good_name):
    # Pins: letters/digits/underscore/hyphen names are accepted by save().
    reg = MethodRegistry(tmp_path)
    reg.save(good_name, _valid_method_config(), overwrite=False)
    assert reg.exists(good_name)


@pytest.mark.parametrize("bad_name", ["", "../evil", "a b", ".hidden", "a/b"])
def test_method_invalid_names_rejected(tmp_path, bad_name):
    # Pins: names with path separators, spaces, leading dot, or empty are INVALID_PARAM.
    reg = MethodRegistry(tmp_path)
    with pytest.raises(F.FaultError) as exc:
        reg.save(bad_name, _valid_method_config(), overwrite=False)
    assert exc.value.fault.category == F.FaultCategory.INVALID_PARAM


def test_method_duplicate_save_faults_bad_value(tmp_path):
    # Pins: re-saving an existing name without overwrite -> INVALID_PARAM_BAD_VALUE (2002).
    reg = MethodRegistry(tmp_path)
    reg.save("dup", _valid_method_config(), overwrite=False)
    with pytest.raises(F.FaultError) as exc:
        reg.save("dup", _valid_method_config(), overwrite=False)
    assert exc.value.fault.category == F.FaultCategory.INVALID_PARAM
    assert exc.value.fault.code == 2002


def test_method_overwrite_missing_faults_protocol_unknown(tmp_path):
    # Pins: overwrite=True on a missing method -> PROTOCOL 1001 carrying the method name.
    reg = MethodRegistry(tmp_path)
    with pytest.raises(F.FaultError) as exc:
        reg.save("ghost", _valid_method_config(), overwrite=True)
    assert exc.value.fault.category == F.FaultCategory.PROTOCOL
    assert exc.value.fault.code == 1001
    assert exc.value.fault.detail["method"] == "ghost"


def test_method_delete_removes_file(tmp_path):
    # Pins: delete() unlinks the backing .yaml and exists() flips to False.
    reg = MethodRegistry(tmp_path)
    reg.save("gone", _valid_method_config(), overwrite=False)
    assert (tmp_path / "gone.yaml").exists()
    reg.delete("gone")
    assert not (tmp_path / "gone.yaml").exists()
    assert reg.exists("gone") is False


def test_method_list_sorted_by_name(tmp_path):
    # Pins: list() returns summaries sorted lexicographically by name.
    reg = MethodRegistry(tmp_path)
    for name in ("zebra", "alpha", "mid"):
        reg.save(name, _valid_method_config(), overwrite=False)
    assert [s["name"] for s in reg.list()] == ["alpha", "mid", "zebra"]


def test_method_summary_exact_key_set(tmp_path):
    # Pins: a parseable method's summary key set exactly, with estimated_duration_s None.
    reg = MethodRegistry(tmp_path)
    reg.save("only", _valid_method_config(), overwrite=False)
    summary = reg.list()[0]
    assert set(summary.keys()) == {
        "name",
        "widget_type",
        "channels",
        "objective",
        "wellplate_format",
        "wells",
        "nz",
        "nt",
        "estimated_duration_s",
    }
    assert summary["estimated_duration_s"] is None


def test_method_unparseable_file_summary_exact_key_set(tmp_path):
    # Pins: an unparseable file yields a summary with EXACTLY {name, error}.
    reg = MethodRegistry(tmp_path)
    (tmp_path / "bad.yaml").write_text("{{not yaml")
    entry = {s["name"]: s for s in reg.list()}["bad"]
    assert set(entry.keys()) == {"name", "error"}


# =====================================================================
# 9. Job-persistence contract
# =====================================================================


def test_job_persists_full_record_across_restart(tmp_path):
    # Pins: audit fields survive a JobStore restart AND the exact on-disk JSON key set.
    path = tmp_path / "last_job.json"
    store = JobStore(persist_path=path)
    job = store.create(experiment_id="exp", operator="op", scheduler_job_id="sched-1")
    store.mark_running(job.job_id)
    store.complete(job.job_id, JobOutcome.SUCCESS, JobResult(end_reason="completed"))

    reloaded = JobStore(persist_path=path)
    assert reloaded.last is not None
    assert reloaded.last.job_id == job.job_id
    assert reloaded.last.outcome == JobOutcome.SUCCESS
    assert reloaded.last.operator == "op"
    assert reloaded.last.scheduler_job_id == "sched-1"
    assert reloaded.last == store.last  # equal to the completed record

    with open(path, "r", encoding="utf-8") as f:
        disk = json.load(f)
    assert set(disk.keys()) == {
        "job_id",
        "kind",
        "experiment_id",
        "origin",
        "operator",
        "scheduler_job_id",
        "state",
        "accepted_at",
        "started_at",
        "completed_at",
        "outcome",
        "progress",
        "result",
        "fault",
    }


# =====================================================================
# 10. Fault-model JSON contract
# =====================================================================


def test_make_fault_exact_key_set_and_defaults():
    # Pins: Fault.model_dump() key set exactly + every default + parseable UTC timestamp.
    fault = F.make_fault(F.FaultCategory.IO, F.IO_GENERIC, "disk hiccup")
    d = fault.model_dump()
    assert set(d.keys()) == {
        "category",
        "code",
        "recoverable",
        "scheduler_action",
        "sequence",
        "component",
        "message",
        "detail",
        "timestamp",
        "terminal",
        "operator_intervention_required",
        "plate_removable",
        "resolved_at",
        "resolved_by",
    }
    assert d["recoverable"] is False
    assert d["scheduler_action"] == "ESCALATE_OPERATOR"
    assert d["terminal"] is False
    assert d["plate_removable"] is True
    assert d["sequence"] == 0

    ts = d["timestamp"]
    assert ts.endswith("Z")
    parsed = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_fault_log_sequence_since_and_limit():
    # Pins: first fault gets sequence 1; since() is exclusive on seq; limit is honored.
    log = F.FaultLog()
    first = log.record(F.make_fault(F.FaultCategory.IO, F.IO_GENERIC, "one"))
    assert first.sequence == 1
    assert [f.message for f in log.since(0)] == ["one"]
    assert log.since(1) == []

    for i in range(5):
        log.record(F.make_fault(F.FaultCategory.IO, F.IO_GENERIC, f"m{i}"))
    assert len(log.since(0, limit=2)) == 2
