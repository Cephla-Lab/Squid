import pytest

from squid_service.state import BUSY_STATES, InstrumentState, InvalidTransition, StateMachine


def test_initial_state_and_legal_transition():
    transitions = []
    sm = StateMachine(InstrumentState.INITIALIZED, on_transition=lambda o, n: transitions.append((o, n)))
    assert sm.state == InstrumentState.INITIALIZED
    sm.transition(InstrumentState.ACQUIRING)
    assert sm.state == InstrumentState.ACQUIRING
    assert transitions == [(InstrumentState.INITIALIZED, InstrumentState.ACQUIRING)]


def test_illegal_transition_raises_and_preserves_state():
    sm = StateMachine(InstrumentState.INITIALIZED)
    with pytest.raises(InvalidTransition):
        sm.transition(InstrumentState.PROCESSING)
    assert sm.state == InstrumentState.INITIALIZED


def test_full_acquisition_lifecycle():
    sm = StateMachine(InstrumentState.INITIALIZED)
    for target in (
        InstrumentState.ACQUIRING,
        InstrumentState.PROCESSING,
        InstrumentState.INITIALIZED,
    ):
        sm.transition(target)
    assert sm.state == InstrumentState.INITIALIZED


def test_error_and_recovery_paths():
    sm = StateMachine(InstrumentState.ACQUIRING)
    sm.transition(InstrumentState.ERROR)
    sm.transition(InstrumentState.RECOVERING)
    sm.transition(InstrumentState.INITIALIZED)
    assert sm.state == InstrumentState.INITIALIZED


def test_busy_states():
    assert InstrumentState.ACQUIRING in BUSY_STATES
    assert InstrumentState.INITIALIZED not in BUSY_STATES
    sm = StateMachine(InstrumentState.ACQUIRING)
    assert sm.is_busy()


def test_self_transition_is_noop_no_listener():
    calls = []
    sm = StateMachine(InstrumentState.INITIALIZED, on_transition=lambda o, n: calls.append(1))
    sm.transition(InstrumentState.INITIALIZED)
    assert sm.state == InstrumentState.INITIALIZED
    assert calls == []
