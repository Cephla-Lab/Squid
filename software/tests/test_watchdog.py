import struct
import time

import pytest

from control._def import CMD_SET, DEFAULT_WATCHDOG_TIMEOUT_MS, MAX_WATCHDOG_TIMEOUT_MS
from control.microcontroller import Microcontroller, SimSerial


@pytest.fixture
def mcu():
    sim = SimSerial()
    mcu = Microcontroller(sim, reset_and_initialize=False)
    yield mcu
    mcu.close()


class TestSetWatchdogTimeout:
    def test_sends_correct_command_id(self, mcu):
        """SET_WATCHDOG_TIMEOUT should use command ID 40."""
        mcu.set_watchdog_timeout(5.0)
        mcu.wait_till_operation_is_completed()
        assert mcu.last_command[1] == CMD_SET.SET_WATCHDOG_TIMEOUT

    def test_sends_timeout_as_milliseconds(self, mcu):
        """Timeout should be converted to ms and packed big-endian in bytes 2-5."""
        mcu.set_watchdog_timeout(5.0)
        mcu.wait_till_operation_is_completed()
        cmd = mcu.last_command
        timeout_ms = struct.unpack(">I", bytes(cmd[2:6]))[0]
        assert timeout_ms == 5000

    def test_clamps_negative_to_zero(self, mcu):
        """Negative timeout should be clamped to 0 (firmware treats as default)."""
        mcu.set_watchdog_timeout(-1.0)
        mcu.wait_till_operation_is_completed()
        cmd = mcu.last_command
        timeout_ms = struct.unpack(">I", bytes(cmd[2:6]))[0]
        assert timeout_ms == 0

    def test_clamps_to_max(self, mcu):
        """Timeout above max should be clamped."""
        mcu.set_watchdog_timeout(9999.0)
        mcu.wait_till_operation_is_completed()
        cmd = mcu.last_command
        timeout_ms = struct.unpack(">I", bytes(cmd[2:6]))[0]
        assert timeout_ms == MAX_WATCHDOG_TIMEOUT_MS

    def test_simserial_stores_timeout(self, mcu):
        """SimSerial should store the configured timeout."""
        mcu.set_watchdog_timeout(10.0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.watchdog_timeout_ms == 10000

    def test_simserial_clamps_zero_to_default(self, mcu):
        """SimSerial should treat 0 as firmware default."""
        mcu.set_watchdog_timeout(0.0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.watchdog_timeout_ms == DEFAULT_WATCHDOG_TIMEOUT_MS

    def test_simserial_clamps_above_max(self, mcu):
        """SimSerial should clamp values above MAX to MAX."""
        mcu.set_watchdog_timeout(9999.0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.watchdog_timeout_ms == MAX_WATCHDOG_TIMEOUT_MS

    def test_fractional_seconds_conversion(self, mcu):
        """Fractional seconds should convert correctly to milliseconds."""
        mcu.set_watchdog_timeout(2.5)
        mcu.wait_till_operation_is_completed()
        cmd = mcu.last_command
        timeout_ms = struct.unpack(">I", bytes(cmd[2:6]))[0]
        assert timeout_ms == 2500


class TestHeartbeat:
    def test_sends_correct_command_id(self, mcu):
        """HEARTBEAT should use command ID 42."""
        mcu.send_heartbeat()
        mcu.wait_till_operation_is_completed()
        assert mcu.last_command[1] == CMD_SET.HEARTBEAT

    def test_start_and_stop(self, mcu):
        """Heartbeat thread should start and stop cleanly."""
        mcu.start_heartbeat(interval_s=0.1)
        assert mcu._heartbeat_thread is not None
        assert mcu._heartbeat_thread.is_alive()
        mcu.stop_heartbeat()
        assert mcu._heartbeat_thread is None

    def test_close_stops_heartbeat(self):
        """close() should stop the heartbeat thread."""
        sim = SimSerial()
        mcu = Microcontroller(sim, reset_and_initialize=False)
        mcu.start_heartbeat(interval_s=0.1)
        thread = mcu._heartbeat_thread
        mcu.close()
        assert not thread.is_alive()

    def test_heartbeat_thread_is_daemon(self, mcu):
        """Heartbeat thread should be a daemon so it dies with the process."""
        mcu.start_heartbeat(interval_s=0.1)
        assert mcu._heartbeat_thread.daemon is True
        mcu.stop_heartbeat()

    def test_heartbeat_sends_periodically(self, mcu):
        """Heartbeat should send multiple commands over time."""
        mcu.start_heartbeat(interval_s=0.05)
        time.sleep(0.2)
        mcu.stop_heartbeat()
        assert mcu.last_command[1] == CMD_SET.HEARTBEAT

    def test_stop_heartbeat_when_never_started(self, mcu):
        """stop_heartbeat() should be safe when no heartbeat was started."""
        mcu.stop_heartbeat()  # Should not raise

    def test_double_start_stops_first(self, mcu):
        """Starting heartbeat twice should stop the first thread."""
        mcu.start_heartbeat(interval_s=0.1)
        first_thread = mcu._heartbeat_thread
        mcu.start_heartbeat(interval_s=0.1)
        assert not first_thread.is_alive()
        assert mcu._heartbeat_thread.is_alive()
        mcu.stop_heartbeat()


class TestFirmwareVersionForWatchdog:
    def test_version_detected_as_1_1(self, mcu):
        """SimSerial should report firmware version 1.1."""
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()
        assert mcu.firmware_version == (1, 1)
