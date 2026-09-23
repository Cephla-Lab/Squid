"""Tests for tools/turret_setup.py (factory parameter + EEPROM setup for the NiMotion turret drive)."""

from __future__ import annotations

import importlib.util
import pathlib
import time
import types

import pytest

import control.objective_turret_controller as otc
from control.objective_turret_controller import (
    CLEAR_ERROR_STORAGE_MAGIC,
    CW_CLEAR_FAULT,
    CW_DISABLE,
    DI1_FUNCTION_ORIGIN_SWITCH,
    EXPECTED_MAX_SPEED,
    INIT_PARAMS,
    MICROSTEP_REG_VALUE,
    POWER_CYCLE_PARAMS,
    REG_CLEAR_ERROR_STORAGE,
    REG_CONTROL_WORD,
    REG_DI_FUNCTION,
    REG_DIRECTION,
    REG_MAX_SPEED,
    REG_MICROSTEP,
    REG_SAVE_PARAMS,
    SAVE_PARAMS_MAGIC,
)

_TOOL_PATH = pathlib.Path(__file__).resolve().parents[2] / "tools" / "turret_setup.py"
_spec = importlib.util.spec_from_file_location("turret_setup", _TOOL_PATH)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

_quiet = lambda *_: None  # noqa: E731


class _RegisterMemoryModbus:
    """Fake ModbusRTUClient with real register memory, so read-backs reflect writes."""

    def __init__(self, initial: dict):
        self.regs = dict(initial)
        self.writes = []  # (address, value) in order
        self.reads = 0

    def read_register(self, slave_id, address):
        self.reads += 1
        return self.regs.get(address, 0)

    def read_register_32bit(self, slave_id, address, signed=False):
        self.reads += 1
        return self.regs.get(address, 0)

    def write_register(self, slave_id, address, value):
        self.writes.append((address, value))
        self.regs[address] = value

    def write_register_32bit(self, slave_id, address, value, signed=False):
        self.writes.append((address, value))
        self.regs[address] = value


def _factory_state() -> dict:
    """A drive that already carries every expected value."""
    return {addr: expected for addr, expected, _label, _kwargs in POWER_CYCLE_PARAMS + INIT_PARAMS}


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(otc, "time", types.SimpleNamespace(sleep=lambda _s: None, monotonic=time.monotonic))


def test_check_mode_reports_mismatch_without_writing():
    state = _factory_state()
    state[REG_MICROSTEP] = 3  # 8 microsteps: the controller would refuse to start
    state[REG_MAX_SPEED] = 200
    fake = _RegisterMemoryModbus(state)
    assert tool.run(fake, slave_id=1, check_only=True, out=_quiet) == 1
    assert fake.writes == []


def test_check_mode_passes_on_a_configured_drive():
    fake = _RegisterMemoryModbus(_factory_state())
    assert tool.run(fake, slave_id=1, check_only=True, out=_quiet) == 0
    assert fake.writes == []


def test_apply_writes_mismatches_then_saves_and_verifies(no_sleep):
    state = _factory_state()
    state[REG_MICROSTEP] = 3
    state[REG_MAX_SPEED] = 200
    state[REG_DI_FUNCTION] = 0x0000_0510  # DI2..DI4 configured, DI1 = 0 (unassigned)
    fake = _RegisterMemoryModbus(state)
    lines = []
    assert tool.run(fake, slave_id=1, check_only=False, out=lines.append) == 0

    # Motor forced into the disabled state (parameter writes need it) after a fault clear.
    assert fake.writes[:3] == [
        (REG_CONTROL_WORD, CW_CLEAR_FAULT),
        (REG_CLEAR_ERROR_STORAGE, CLEAR_ERROR_STORAGE_MAGIC),
        (REG_CONTROL_WORD, CW_DISABLE),
    ]
    assert (REG_MICROSTEP, MICROSTEP_REG_VALUE) in fake.writes
    assert (REG_MAX_SPEED, EXPECTED_MAX_SPEED) in fake.writes
    # DI1 nibble set to "origin switch" with DI2..DI4 preserved.
    assert (REG_DI_FUNCTION, 0x0510 | DI1_FUNCTION_ORIGIN_SWITCH) in fake.writes
    # Only the values that differed were written, each once, from the initial read pass.
    param_writes = [w for w in fake.writes if w[0] not in (REG_CONTROL_WORD, REG_CLEAR_ERROR_STORAGE, REG_SAVE_PARAMS)]
    assert len(param_writes) == 3
    assert REG_DIRECTION not in [a for (a, _v) in fake.writes]  # runtime-only register, never persisted
    # EEPROM save comes after the parameter writes and is the last register write.
    assert fake.writes[-1] == (REG_SAVE_PARAMS, SAVE_PARAMS_MAGIC)
    # Exactly two read passes: the report and the verification.
    assert fake.reads == 2 * len(tool.SETUP_PARAMS)
    text = " ".join(lines).lower()
    assert "power-cycle" in text and "--check" in text


def test_apply_saves_even_when_nothing_differs(no_sleep):
    # RAM matching does not prove EEPROM matches; the setup tool always persists.
    fake = _RegisterMemoryModbus(_factory_state())
    assert tool.run(fake, slave_id=1, check_only=False, out=_quiet) == 0
    param_writes = [w for w in fake.writes if w[0] not in (REG_CONTROL_WORD, REG_CLEAR_ERROR_STORAGE)]
    assert param_writes == [(REG_SAVE_PARAMS, SAVE_PARAMS_MAGIC)]


def test_apply_reports_failure_when_a_write_does_not_stick(no_sleep):
    state = _factory_state()
    state[REG_MAX_SPEED] = 200

    class _Stubborn(_RegisterMemoryModbus):
        def write_register_32bit(self, slave_id, address, value, signed=False):
            self.writes.append((address, value))  # accepted on the wire, silently dropped

    assert tool.run(_Stubborn(state), slave_id=1, check_only=False, out=_quiet) == 1


def test_declined_confirmation_writes_nothing():
    state = _factory_state()
    state[REG_MAX_SPEED] = 200
    fake = _RegisterMemoryModbus(state)
    assert tool.run(fake, slave_id=1, check_only=False, out=_quiet, confirm=lambda: False) == 2
    assert fake.writes == []
