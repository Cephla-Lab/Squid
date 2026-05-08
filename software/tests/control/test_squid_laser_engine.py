"""Unit tests for SquidLaserEngine and its supporting types."""

import struct
from zlib import crc32

import pytest
from control.squid_laser_engine import (
    LaserChannelState,
    TcmModuleInfo,
    LaserChannelInfo,
    SquidLaserEngineStatus,
    SquidLaserEngineError,
    _parse_status_packet,
    _build_command_packet,
)


def _make_module(state, module_index=0):
    return TcmModuleInfo(
        module_index=module_index,
        state=state,
        temperature_c=25.0,
        setpoint_c=25.0,
        setpoint_diff_c=0.0,
        tec_voltage=0.5,
        tec_current=0.5,
        hi_temp_setpoint_c=99.7,
    )


class TestLaserChannelInfo:
    def test_single_module_active_is_ready(self):
        info = LaserChannelInfo(
            key="405",
            laser_ttl_on=False,
            modules=(_make_module(LaserChannelState.ACTIVE),),
        )
        assert info.is_ready
        assert not info.is_error
        assert info.display_state == LaserChannelState.ACTIVE

    def test_single_module_warming_up_is_not_ready(self):
        info = LaserChannelInfo(
            key="405",
            laser_ttl_on=False,
            modules=(_make_module(LaserChannelState.WARMING_UP),),
        )
        assert not info.is_ready
        assert info.display_state == LaserChannelState.WARMING_UP

    def test_55x_one_active_one_warming_is_not_ready(self):
        info = LaserChannelInfo(
            key="55x",
            laser_ttl_on=False,
            modules=(
                _make_module(LaserChannelState.ACTIVE, module_index=4),
                _make_module(LaserChannelState.WARMING_UP, module_index=5),
            ),
        )
        assert not info.is_ready
        assert info.display_state == LaserChannelState.WARMING_UP

    def test_55x_both_active_is_ready(self):
        info = LaserChannelInfo(
            key="55x",
            laser_ttl_on=False,
            modules=(
                _make_module(LaserChannelState.ACTIVE, module_index=4),
                _make_module(LaserChannelState.ACTIVE, module_index=5),
            ),
        )
        assert info.is_ready
        assert info.display_state == LaserChannelState.ACTIVE

    def test_error_module_is_error(self):
        info = LaserChannelInfo(
            key="638",
            laser_ttl_on=False,
            modules=(_make_module(LaserChannelState.ERROR),),
        )
        assert info.is_error
        assert not info.is_ready
        assert info.display_state == LaserChannelState.ERROR


class TestSquidLaserEngineStatus:
    def _status(self, *channel_states):
        channels = {}
        for key, state in channel_states:
            channels[key] = LaserChannelInfo(
                key=key,
                laser_ttl_on=False,
                modules=(_make_module(state),),
            )
        return SquidLaserEngineStatus(channels=channels, timestamp_s=0.0)

    def test_is_ready_for_all_active(self):
        status = self._status(("405", LaserChannelState.ACTIVE), ("470", LaserChannelState.ACTIVE))
        assert status.is_ready_for(["405", "470"])

    def test_is_ready_for_one_warming(self):
        status = self._status(("405", LaserChannelState.ACTIVE), ("470", LaserChannelState.WARMING_UP))
        assert not status.is_ready_for(["405", "470"])

    def test_is_ready_for_subset(self):
        status = self._status(("405", LaserChannelState.ACTIVE), ("470", LaserChannelState.WARMING_UP))
        assert status.is_ready_for(["405"])

    def test_any_error_true(self):
        status = self._status(("405", LaserChannelState.ERROR))
        assert status.any_error()

    def test_any_error_false(self):
        status = self._status(("405", LaserChannelState.ACTIVE))
        assert not status.any_error()

    def test_unknown_key_not_ready(self):
        status = self._status(("405", LaserChannelState.ACTIVE))
        assert not status.is_ready_for(["638"])


def _build_firmware_status_bytes(
    laser_ttl=(0, 0, 0, 0, 0),
    states=(2, 2, 2, 2, 2, 2),  # all ACTIVE
    temps_c=(25.0, 25.0, 25.0, 25.0, 25.0, 99.7),
    voltages=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
    currents=(0.0, 0.0, 0.0, 0.0, 0.1, 0.2),
    diff_temps_c=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    hi_temp_setpoints_c=(99.0, 99.0, 99.0, 99.0, 99.0, 99.7),
):
    """Build the inner payload of the 'S' status packet (without trailing CRC32)."""
    NUM_LASER_CH = 5
    NUM_TEMP_CH = 6
    out = bytearray()
    out.append(ord("S"))
    for v in laser_ttl:
        out.append(v & 0xFF)
    # 6 × 7-byte TCM blocks: state(1) + temp(2 BE) + voltage(2 BE) + current(2 BE)
    for i in range(NUM_TEMP_CH):
        out.append(states[i] & 0xFF)
        out += struct.pack(">h", int(temps_c[i] * 100))
        out += struct.pack(">h", int(voltages[i] * 100))
        out += struct.pack(">h", int(currents[i] * 100))
    # 6 × ΔT (signed BE centidegrees)
    for i in range(NUM_TEMP_CH):
        out += struct.pack(">h", int(diff_temps_c[i] * 100))
    # 6 × hi-temp setpoint (signed BE centidegrees)
    for i in range(NUM_TEMP_CH):
        out += struct.pack(">h", int(hi_temp_setpoints_c[i] * 100))
    return bytes(out)


class TestParseStatusPacket:
    def test_all_active_default(self):
        payload = _build_firmware_status_bytes()
        status = _parse_status_packet(payload)
        assert status is not None
        assert list(status.channels.keys()) == ["405", "470", "55x", "638", "730"]
        assert status.is_ready_for(["405", "470", "55x", "638", "730"])
        # 55x has both modules
        assert len(status.channels["55x"].modules) == 2
        # Other channels have one module
        assert len(status.channels["405"].modules) == 1

    def test_temperatures_parsed(self):
        payload = _build_firmware_status_bytes(temps_c=(24.5, 25.0, 25.0, 25.0, 25.0, 99.7))
        status = _parse_status_packet(payload)
        assert status.channels["405"].modules[0].temperature_c == pytest.approx(24.5)

    def test_diff_temp_negative(self):
        payload = _build_firmware_status_bytes(diff_temps_c=(-1.5, 0.0, 0.0, 0.0, 0.0, 0.0))
        status = _parse_status_packet(payload)
        assert status.channels["405"].modules[0].setpoint_diff_c == pytest.approx(-1.5)
        # setpoint_c = temp - diff = 25.0 - (-1.5) = 26.5
        assert status.channels["405"].modules[0].setpoint_c == pytest.approx(26.5)

    def test_55x_module_indices(self):
        payload = _build_firmware_status_bytes()
        status = _parse_status_packet(payload)
        modules = status.channels["55x"].modules
        assert modules[0].module_index == 4
        assert modules[1].module_index == 5

    def test_laser_ttl_on(self):
        payload = _build_firmware_status_bytes(laser_ttl=(1, 0, 0, 0, 0))
        status = _parse_status_packet(payload)
        assert status.channels["405"].laser_ttl_on is True
        assert status.channels["470"].laser_ttl_on is False

    def test_state_warming_up(self):
        payload = _build_firmware_status_bytes(states=(0, 2, 2, 2, 2, 2))
        status = _parse_status_packet(payload)
        assert status.channels["405"].modules[0].state == LaserChannelState.WARMING_UP
        assert not status.is_ready_for(["405"])

    def test_55x_only_one_module_active(self):
        # module 4 ACTIVE, module 5 WARMING_UP
        payload = _build_firmware_status_bytes(states=(2, 2, 2, 2, 2, 0))
        status = _parse_status_packet(payload)
        assert not status.channels["55x"].is_ready

    def test_truncated_payload_returns_none(self):
        assert _parse_status_packet(b"S\x00") is None

    def test_wrong_command_byte_returns_none(self):
        # 'A' = ack, not a status packet
        payload = _build_firmware_status_bytes()
        assert _parse_status_packet(b"A" + payload[1:]) is None

    def test_state_value_out_of_range_falls_back_to_error(self):
        # Firmware should never send 255, but the parser must not crash.
        payload = _build_firmware_status_bytes(states=(255, 2, 2, 2, 2, 2))
        status = _parse_status_packet(payload)
        assert status is not None
        assert status.channels["405"].modules[0].state == LaserChannelState.ERROR

    def test_empty_payload_returns_none(self):
        assert _parse_status_packet(b"") is None


class TestBuildCommandPacket:
    def test_query_packet(self):
        pkt = _build_command_packet(b"Q")
        # cmd byte + crc32(LE) + 0x0A 0x0D
        expected_crc = crc32(b"Q")
        assert pkt == b"Q" + struct.pack("<I", expected_crc) + b"\x0a\x0d"

    def test_wake_55x(self):
        # 55x is firmware channel index 4
        pkt = _build_command_packet(b"W", channel_index=4)
        body = b"W" + struct.pack("<I", 4)
        expected_crc = crc32(body)
        assert pkt == body + struct.pack("<I", expected_crc) + b"\x0a\x0d"

    def test_sleep_405(self):
        # 405 is firmware channel index 0
        pkt = _build_command_packet(b"S", channel_index=0)
        body = b"S" + struct.pack("<I", 0)
        expected_crc = crc32(body)
        assert pkt == body + struct.pack("<I", expected_crc) + b"\x0a\x0d"


import time

from control.squid_laser_engine import SquidLaserEngine_Simulation


@pytest.fixture
def sim_engine():
    """Simulation engine with a fast tick so tests don't sleep for ages."""
    engine = SquidLaserEngine_Simulation(query_interval_s=0.05, transition_seconds=0.2)
    engine.start()
    yield engine
    engine.close()


class TestSquidLaserEngineSimulation:
    def test_starts_in_warming_then_active(self, sim_engine):
        # Wait for at least one transition cycle.
        time.sleep(0.5)
        status = sim_engine.get_latest_status()
        assert status is not None
        assert status.is_ready_for(["405", "470", "55x", "638", "730"])

    def test_sleep_then_wake(self, sim_engine):
        time.sleep(0.4)
        sim_engine.put_to_sleep("405")
        time.sleep(0.4)
        status = sim_engine.get_latest_status()
        assert not status.channels["405"].is_ready
        sim_engine.wake_up("405")
        time.sleep(0.8)
        status = sim_engine.get_latest_status()
        assert status.channels["405"].is_ready

    def test_wait_until_ready_already_active(self, sim_engine):
        time.sleep(0.4)
        assert sim_engine.wait_until_ready(["405"], timeout_s=1.0) is True

    def test_wait_until_ready_after_wake(self, sim_engine):
        time.sleep(0.4)
        sim_engine.put_to_sleep("470")
        time.sleep(0.3)
        assert sim_engine.wait_until_ready(["470"], timeout_s=2.0) is True

    def test_wait_until_ready_timeout(self, sim_engine):
        # Force-hold a channel in WARMING_UP so it never reaches ACTIVE.
        sim_engine.force_hold_state("405", LaserChannelState.WARMING_UP)
        time.sleep(0.2)
        assert sim_engine.wait_until_ready(["405"], timeout_s=0.5) is False

    def test_wait_until_ready_cancel(self, sim_engine):
        sim_engine.force_hold_state("405", LaserChannelState.WARMING_UP)
        cancel_called = [False]

        def cancel():
            return cancel_called[0]

        # Schedule a cancel after 0.1s.
        import threading

        threading.Timer(0.1, lambda: cancel_called.__setitem__(0, True)).start()
        t0 = time.time()
        result = sim_engine.wait_until_ready(["405"], timeout_s=5.0, cancel_fn=cancel)
        elapsed = time.time() - t0
        assert result is False
        assert elapsed < 1.0  # exited promptly, not after the full timeout

    def test_wait_until_ready_error_raises(self, sim_engine):
        sim_engine.force_error("638")
        time.sleep(0.2)
        with pytest.raises(SquidLaserEngineError):
            sim_engine.wait_until_ready(["638"], timeout_s=1.0)

    def test_channel_keys_for_wavelengths(self, sim_engine):
        keys = sim_engine.channel_keys_for_wavelengths([488, 561, 640, 999])
        # 488->470, 561->55x, 640->638; 999 is unmapped and dropped.
        # Order preserved, no duplicates.
        assert keys == ["470", "55x", "638"]

    def test_status_updated_signal(self, qtbot, sim_engine):
        received = []
        sim_engine.status_updated.connect(lambda s: received.append(s))
        # Drive a state change so the dirty-gated tick publishes.
        sim_engine.put_to_sleep("405")
        qtbot.wait(150)
        sim_engine.wake_up("405")
        qtbot.wait(150)
        assert len(received) >= 2


def test_simulation_connection_lost():
    """Simulator can be told to drop its connection."""
    engine = SquidLaserEngine_Simulation(query_interval_s=0.05, transition_seconds=0.1)
    engine.start()
    try:
        signals = []
        engine.connection_lost.connect(lambda msg: signals.append(msg))
        engine.force_connection_lost("test drop")
        time.sleep(0.2)
        assert engine.is_connection_lost() is True
        assert signals == ["test drop"]
    finally:
        engine.close()


class _FakeSerial:
    """Drop-in for serial.Serial: captures writes, returns canned responses on read."""

    def __init__(self):
        self._read_buffer = bytearray()
        self._write_log = bytearray()
        self._lock = threading.Lock()
        self.is_open = True

    # Used by the receive thread.
    def read(self, n):
        with self._lock:
            if not self._read_buffer:
                return b""
            chunk = bytes(self._read_buffer[:n])
            del self._read_buffer[:n]
            return chunk

    def write(self, data):
        with self._lock:
            self._write_log += data
        return len(data)

    def close(self):
        self.is_open = False

    # Test helpers
    def feed_bytes(self, data: bytes):
        with self._lock:
            self._read_buffer += data

    def feed_status_packet(self, payload: bytes):
        full = payload + struct.pack("<I", crc32(payload)) + b"\x0a\x0d"
        self.feed_bytes(full)

    def writes(self) -> bytes:
        with self._lock:
            return bytes(self._write_log)


import threading

from control.squid_laser_engine import SquidLaserEngine


class TestSquidLaserEngineRealClass:
    def _make_engine(self):
        fake = _FakeSerial()
        engine = SquidLaserEngine(_test_serial=fake, query_interval_s=0.05)
        return engine, fake

    def test_query_thread_sends_Q_periodically(self):
        engine, fake = self._make_engine()
        engine.start()
        try:
            time.sleep(0.25)
        finally:
            engine.close()
        writes = fake.writes()
        # Expect at least 2 'Q' packets in 0.25s at 0.05s interval
        q_packet = _build_command_packet(b"Q")
        assert writes.count(q_packet) >= 2

    def test_receive_thread_parses_status(self):
        engine, fake = self._make_engine()
        engine.start()
        try:
            payload = _build_firmware_status_bytes()
            fake.feed_status_packet(payload)
            time.sleep(0.3)
        finally:
            engine.close()
        status = engine.get_latest_status()
        assert status is not None
        assert status.is_ready_for(["405", "470", "55x", "638", "730"])

    def test_wake_writes_W_packet(self):
        engine, fake = self._make_engine()
        engine.start()
        try:
            engine.wake_up("55x")
            time.sleep(0.1)
        finally:
            engine.close()
        wake_packet = _build_command_packet(b"W", channel_index=4)
        assert wake_packet in fake.writes()

    def test_sleep_writes_S_packet(self):
        engine, fake = self._make_engine()
        engine.start()
        try:
            engine.put_to_sleep("405")
            time.sleep(0.1)
        finally:
            engine.close()
        sleep_packet = _build_command_packet(b"S", channel_index=0)
        assert sleep_packet in fake.writes()

    def test_crc_mismatch_dropped(self):
        engine, fake = self._make_engine()
        engine.start()
        try:
            payload = _build_firmware_status_bytes()
            # Bad CRC
            fake.feed_bytes(payload + b"\x00\x00\x00\x00" + b"\x0a\x0d")
            time.sleep(0.3)
        finally:
            engine.close()
        # No status was published from the bad packet; counter incremented.
        assert engine.crc_mismatch_count >= 1
        assert engine.get_latest_status() is None

    def test_serial_exception_signals_connection_lost(self, qtbot):
        engine, fake = self._make_engine()
        signals = []
        engine.connection_lost.connect(lambda msg: signals.append(msg))
        # Replace read with one that raises after first call.
        original_read = fake.read
        call_count = [0]

        def boom(n):
            call_count[0] += 1
            if call_count[0] > 1:
                import serial

                raise serial.SerialException("simulated drop")
            return original_read(n)

        fake.read = boom
        engine.start()
        try:
            # qtbot.wait pumps the Qt event loop so the cross-thread connection_lost
            # signal (emitted from the receive thread) can be delivered to our slot.
            qtbot.wait(400)
        finally:
            engine.close()
        # Pump once more to catch any emit that happened during close().
        qtbot.wait(50)
        assert engine.is_connection_lost() is True
        assert any("simulated drop" in s for s in signals)
