import json

from control.laser_engine_v2_sim import FakeEngine
from control.laser_engine_v2_status import LineState, SourceStatus, parse_status


def _stat(fake):
    return json.loads(fake._stat())


def _armed_fake():
    fake = FakeEngine(tok_delay_polls=0)
    fake.latch_ok, fake.armed = True, True
    fake.tok = [True] * 5
    return fake


def test_not_armed_and_unused():
    stat = _stat(FakeEngine())
    stat["lines"][3]["kind"] = "NONE"  # a variant with nothing on line 4
    st = parse_status(stat)
    assert st.channels["L1"].state == LineState.NOT_ARMED
    assert st.channels["L4"].state == LineState.UNUSED
    assert not st.is_ready_for(["L1"])


def test_tok_low_while_off_is_warming_up():
    fake = _armed_fake()
    fake.tok[0] = False
    assert parse_status(_stat(fake)).channels["L1"].state == LineState.WARMING_UP


def test_enabled_line_ramps_then_ready():
    fake = _armed_fake()
    fake.lines[0]["st"] = "RAMP"
    assert parse_status(_stat(fake)).channels["L1"].state == LineState.READY  # _stat() advances RAMP -> ON


def test_tok_lost_blocks_only_that_line():
    fake = _armed_fake()
    fake.lines[0]["st"] = fake.lines[1]["st"] = "ON"
    fake.set_tok(1, False)
    st = parse_status(_stat(fake))
    assert st.channels["L1"].state == LineState.BLOCKED and "FAULT:RESET" in st.channels["L1"].reason
    assert st.channels["L2"].state == LineState.READY
    assert st.is_ready_for(["L2"]) and not st.is_ready_for(["L1", "L2"])


def test_system_fault_makes_every_line_fault():
    fake = _armed_fake()
    fake.faults = ["OVERTEMP"]
    st = parse_status(_stat(fake))
    assert all(st.channels[k].state == LineState.FAULT for k in ("L1", "L2", "L4", "L5"))
    assert "OVERTEMP" in st.channels["L1"].reason and st.any_error()


def test_cover_open_is_paused_with_or_without_lines_on_and_before_arm():
    fake = _armed_fake()
    fake.lines[0]["st"] = "ON"
    fake.open_cover()  # firmware pauses: a line was on
    assert parse_status(_stat(fake)).channels["L1"].state == LineState.PAUSED
    fake2 = _armed_fake()
    fake2.open_cover()  # nothing on: no pause, only interlock_ok = 0
    st2 = parse_status(_stat(fake2))
    assert not st2.suspended and st2.channels["L1"].state == LineState.PAUSED
    fake3 = FakeEngine()
    fake3.open_cover()  # not armed yet
    st3 = parse_status(_stat(fake3))
    assert st3.channels["L1"].state == LineState.PAUSED and not st3.any_error()


def test_560_line_source_states():
    fake = _armed_fake()
    fake.lines[2]["st"] = "ON"
    stat = _stat(fake)
    st = parse_status(stat, source=None, has_source=False)
    assert st.channels["L3"].state == LineState.NOT_CONFIGURED and not st.any_error()
    cases = [
        (SourceStatus(link_ok=False), LineState.FAULT),
        (SourceStatus(link_ok=True, needs_key=True), LineState.NEEDS_KEY),
        (SourceStatus(link_ok=True, fault=True, detail="x"), LineState.FAULT),
        (SourceStatus(link_ok=True, off=True), LineState.SOURCE_OFF),
        (SourceStatus(link_ok=True, starting=True), LineState.STARTING),
        (SourceStatus(link_ok=True, ready=True), LineState.READY),
        (SourceStatus(link_ok=True, ready=True, settled=False), LineState.STARTING),
    ]
    for source, expected in cases:
        assert parse_status(stat, source=source, has_source=True).channels["L3"].state == expected, source


def test_560_key_or_fault_shows_while_line3_is_off():
    stat = _stat(_armed_fake())  # line 3 not enabled
    assert (
        parse_status(stat, SourceStatus(link_ok=True, needs_key=True), has_source=True).channels["L3"].state
        == LineState.NEEDS_KEY
    )
    assert parse_status(stat, SourceStatus(link_ok=False), has_source=True).channels["L3"].state == LineState.FAULT
    assert (
        parse_status(stat, SourceStatus(link_ok=True, off=True), has_source=True).channels["L3"].state == LineState.OFF
    )


def test_readbacks_not_up_yet_is_starting_not_fault():
    stat = _stat(FakeEngine())
    stat["in"]["exp_ok"] = 0
    assert parse_status(stat).channels["L1"].state == LineState.STARTING
