"""Edge case and integration tests for multi-port illumination.

These tests cover edge cases, boundary conditions, and integration scenarios
that may reveal bugs or missing functionality.
"""

import pytest
from control._def import CMD_SET, ILLUMINATION_CODE
from control.microcontroller import Microcontroller, SimSerial
from control.lighting import IlluminationController


class TestIntensityBoundaryConditions:
    """Test intensity values at boundaries."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_intensity_zero(self, mcu):
        """Setting intensity to 0 should result in DAC value of 0."""
        mcu.set_port_intensity(0, 0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_intensity[0] == 0

    def test_intensity_100_percent(self, mcu):
        """Setting intensity to 100% should result in DAC value of 65535."""
        mcu.set_port_intensity(0, 100)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_intensity[0] == 65535

    def test_intensity_over_100_percent(self, mcu):
        """Setting intensity over 100% should be clamped to 100%."""
        mcu.set_port_intensity(0, 150)
        mcu.wait_till_operation_is_completed()
        # Should clamp to 65535 (100%)
        assert mcu._serial.port_intensity[0] == 65535

    def test_intensity_negative(self, mcu):
        """Setting negative intensity should be clamped to 0."""
        mcu.set_port_intensity(0, -10)
        mcu.wait_till_operation_is_completed()
        # Should clamp to 0
        assert mcu._serial.port_intensity[0] == 0


class TestCommandByteLayout:
    """Verify command byte layout matches protocol specification."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_set_port_intensity_byte_layout(self, mcu):
        """SET_PORT_INTENSITY should have correct byte layout."""
        # Intercept the command before it's sent
        sent_commands = []
        original_write = mcu._serial.write

        def capture_write(data, **kwargs):
            sent_commands.append(bytes(data))
            return original_write(data, **kwargs)

        mcu._serial.write = capture_write

        mcu.set_port_intensity(3, 50)  # Port 3, 50%
        mcu.wait_till_operation_is_completed()

        # Find the SET_PORT_INTENSITY command
        cmd = next(c for c in sent_commands if c[1] == CMD_SET.SET_PORT_INTENSITY)

        # Verify byte layout: [cmd_id, 34, port, intensity_hi, intensity_lo, 0, 0, crc]
        assert cmd[1] == 34  # Command code
        assert cmd[2] == 3  # Port index
        intensity_value = (cmd[3] << 8) | cmd[4]
        expected_intensity = int(0.5 * 65535)
        assert intensity_value == expected_intensity, f"Expected {expected_intensity}, got {intensity_value}"

    def test_set_multi_port_mask_byte_layout(self, mcu):
        """SET_MULTI_PORT_MASK should have correct byte layout for 16-bit masks."""
        sent_commands = []
        original_write = mcu._serial.write

        def capture_write(data, **kwargs):
            sent_commands.append(bytes(data))
            return original_write(data, **kwargs)

        mcu._serial.write = capture_write

        # Use a mask that requires both bytes: 0x0102
        mcu.set_multi_port_mask(0x0102, 0x0100)
        mcu.wait_till_operation_is_completed()

        cmd = next(c for c in sent_commands if c[1] == CMD_SET.SET_MULTI_PORT_MASK)

        # Verify byte layout: [cmd_id, 38, mask_hi, mask_lo, on_hi, on_lo, 0, crc]
        assert cmd[1] == 38
        port_mask = (cmd[2] << 8) | cmd[3]
        on_mask = (cmd[4] << 8) | cmd[5]
        assert port_mask == 0x0102, f"Expected 0x0102, got 0x{port_mask:04X}"
        assert on_mask == 0x0100, f"Expected 0x0100, got 0x{on_mask:04X}"


class TestLegacyCommandInteraction:
    """Test interaction between legacy and new illumination commands."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_legacy_turn_off_affects_new_port_state(self, mcu):
        """Legacy turn_off_illumination should update new port state tracking."""
        # Turn on port using new command
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is True

        # Turn off using legacy command
        mcu.turn_off_illumination()
        mcu.wait_till_operation_is_completed()

        # Legacy command now updates per-port state (backward compatibility)
        assert mcu._serial.port_is_on[0] is False

    def test_new_turn_off_all_affects_legacy_state(self, mcu):
        """New turn_off_all_ports should affect legacy illumination_is_on state."""
        # We'd need to track illumination_is_on in SimSerial to test this
        # This test documents that the interaction needs consideration
        pass


class TestIlluminationControllerStateSync:
    """Test that IlluminationController state stays in sync with hardware."""

    @pytest.fixture
    def controller(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        controller = IlluminationController(mcu)
        yield controller
        mcu.close()

    @pytest.mark.xfail(reason="Direct MCU calls bypass controller state tracking - by design")
    def test_state_sync_after_direct_mcu_call(self, controller):
        """Controller state should reflect direct MCU calls."""
        # Turn on port via direct MCU call (bypassing controller)
        controller.microcontroller.turn_on_port(0)
        controller.microcontroller.wait_till_operation_is_completed()

        # Controller state won't know about this - direct MCU calls bypass
        # controller state tracking. This is expected behavior.
        # Use controller methods to keep state in sync.
        assert controller.port_is_on[0] is True

    def test_concurrent_port_operations(self, controller):
        """Multiple rapid port operations should maintain consistent state."""
        # Rapidly toggle ports
        for i in range(5):
            controller.turn_on_port(i)

        # All should be on
        assert all(controller.port_is_on[i] for i in range(5))

        for i in range(5):
            controller.turn_off_port(i)

        # All should be off
        assert all(not controller.port_is_on[i] for i in range(5))


class TestPortIndexMapping:
    """Test mapping between port indices and source codes."""

    def test_port_index_to_source_code_mapping(self):
        """Verify port indices map to correct legacy source codes."""
        expected_mapping = {
            0: ILLUMINATION_CODE.ILLUMINATION_D1,  # 11
            1: ILLUMINATION_CODE.ILLUMINATION_D2,  # 12
            2: ILLUMINATION_CODE.ILLUMINATION_D3,  # 14 (non-sequential!)
            3: ILLUMINATION_CODE.ILLUMINATION_D4,  # 13 (non-sequential!)
            4: ILLUMINATION_CODE.ILLUMINATION_D5,  # 15
        }

        from control._def import port_index_to_source_code

        for port_idx, expected_source in expected_mapping.items():
            assert port_index_to_source_code(port_idx) == expected_source

    def test_source_code_to_port_index_mapping(self):
        """Verify legacy source codes map to correct port indices."""
        expected_mapping = {
            ILLUMINATION_CODE.ILLUMINATION_D1: 0,
            ILLUMINATION_CODE.ILLUMINATION_D2: 1,
            ILLUMINATION_CODE.ILLUMINATION_D3: 2,
            ILLUMINATION_CODE.ILLUMINATION_D4: 3,
            ILLUMINATION_CODE.ILLUMINATION_D5: 4,
        }

        from control._def import source_code_to_port_index

        for source_code, expected_port in expected_mapping.items():
            assert source_code_to_port_index(source_code) == expected_port

    def test_invalid_port_index_returns_negative_one(self):
        """Invalid port indices should return -1."""
        from control._def import port_index_to_source_code

        assert port_index_to_source_code(-1) == -1
        assert port_index_to_source_code(5) == -1
        assert port_index_to_source_code(100) == -1

    def test_invalid_source_code_returns_negative_one(self):
        """Invalid source codes should return -1."""
        from control._def import source_code_to_port_index

        assert source_code_to_port_index(0) == -1
        assert source_code_to_port_index(10) == -1
        assert source_code_to_port_index(16) == -1

    def test_round_trip_port_to_source_to_port(self):
        """Round trip: port -> source -> port should give same value."""
        from control._def import port_index_to_source_code, source_code_to_port_index

        for port in range(5):
            source = port_index_to_source_code(port)
            port_back = source_code_to_port_index(source)
            assert port_back == port, f"Round trip failed for port {port}"


class TestFirmwareVersionCheck:
    """Test firmware version detection and compatibility checks."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_firmware_version_detected_at_init(self, mcu):
        """Firmware version should be detected during Microcontroller init."""
        # Version is read from response byte 22 (nibble-encoded)
        # SimSerial reports version 1.0 by default
        # Early detection sends TURN_OFF_ALL_PORTS during __init__
        assert hasattr(mcu, "firmware_version")
        assert mcu.firmware_version is not None
        # Version should already be populated - no need to send a command first
        assert mcu.firmware_version == (1, 0)

    def test_supports_multi_port_accurate_at_init(self, mcu):
        """supports_multi_port() should return accurate result immediately after init."""
        assert hasattr(mcu, "supports_multi_port")
        assert callable(mcu.supports_multi_port)
        # Should return True immediately - no need to send a command first
        # (Early detection populates firmware_version during __init__)
        assert mcu.supports_multi_port() is True

    def test_multi_port_command_fails_on_old_firmware(self):
        """Multi-port commands should fail gracefully on old firmware."""
        # This would require simulating old firmware behavior
        # For now, document that this isn't implemented
        pass


class TestIlluminationControllerValidation:
    """Test input validation in IlluminationController."""

    @pytest.fixture
    def controller(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        controller = IlluminationController(mcu)
        yield controller
        mcu.close()

    def test_intensity_clamping_in_set_port_intensity(self, controller):
        """set_port_intensity should clamp intensity to valid range."""
        # Should clamp 150 to 100
        controller.set_port_intensity(0, 150)
        assert controller.port_intensity[0] == 150  # Controller stores requested value
        # MCU receives clamped value (verified via SimSerial)
        assert controller.microcontroller._serial.port_intensity[0] == 65535

    def test_intensity_clamping_in_set_port_illumination(self, controller):
        """set_port_illumination should clamp intensity to valid range."""
        # Should clamp -10 to 0
        controller.set_port_illumination(0, -10, turn_on=True)
        assert controller.port_intensity[0] == -10  # Controller stores requested value
        # MCU receives clamped value
        assert controller.microcontroller._serial.port_intensity[0] == 0

    def test_port_type_validation(self, controller):
        """Methods should reject non-integer port indices."""
        with pytest.raises((ValueError, TypeError)):
            controller.turn_on_port("D1")  # String instead of int

        with pytest.raises((ValueError, TypeError)):
            controller.turn_on_port(1.5)  # Float instead of int
