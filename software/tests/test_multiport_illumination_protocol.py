"""Protocol agreement tests for multi-port illumination.

These tests verify that Python sends exactly the bytes the firmware expects,
and that edge cases are handled consistently on both sides.
"""

import pytest
from control._def import CMD_SET, ILLUMINATION_CODE
from control.microcontroller import Microcontroller, SimSerial
from control.lighting import IlluminationController


class TestProtocolByteAgreement:
    """Verify Python sends bytes matching firmware protocol spec."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def capture_command(self, mcu):
        """Helper to capture the command bytes sent."""
        sent_commands = []
        original_write = mcu._serial.write

        def capture_write(data, **kwargs):
            sent_commands.append(bytes(data))
            return original_write(data, **kwargs)

        mcu._serial.write = capture_write
        return sent_commands

    def test_turn_on_port_sends_correct_bytes(self, mcu):
        """TURN_ON_PORT should send [cmd_id, 35, port, 0, 0, 0, 0, crc]."""
        sent = self.capture_command(mcu)
        mcu.turn_on_port(3)
        mcu.wait_till_operation_is_completed()

        cmd = next(c for c in sent if c[1] == CMD_SET.TURN_ON_PORT)
        assert cmd[1] == 35, "Command code should be 35"
        assert cmd[2] == 3, "Port index should be 3"
        # Bytes 3-6 should be 0 (no additional params)
        assert cmd[3] == 0
        assert cmd[4] == 0
        assert cmd[5] == 0
        assert cmd[6] == 0

    def test_turn_off_port_sends_correct_bytes(self, mcu):
        """TURN_OFF_PORT should send [cmd_id, 36, port, 0, 0, 0, 0, crc]."""
        sent = self.capture_command(mcu)
        mcu.turn_off_port(4)
        mcu.wait_till_operation_is_completed()

        cmd = next(c for c in sent if c[1] == CMD_SET.TURN_OFF_PORT)
        assert cmd[1] == 36
        assert cmd[2] == 4

    def test_set_port_intensity_sends_big_endian(self, mcu):
        """Intensity should be sent as big-endian 16-bit value."""
        sent = self.capture_command(mcu)
        # 75% = 0.75 * 65535 = 49151 = 0xBFFF
        mcu.set_port_intensity(0, 75)
        mcu.wait_till_operation_is_completed()

        cmd = next(c for c in sent if c[1] == CMD_SET.SET_PORT_INTENSITY)
        intensity = (cmd[3] << 8) | cmd[4]
        expected = int(0.75 * 65535)
        assert intensity == expected, f"Expected {expected} (0x{expected:04X}), got {intensity} (0x{intensity:04X})"

    def test_set_multi_port_mask_sends_big_endian_masks(self, mcu):
        """Both masks should be sent as big-endian 16-bit values."""
        sent = self.capture_command(mcu)
        # port_mask = 0x8001 (ports 0 and 15)
        # on_mask = 0x0001 (only port 0 on)
        mcu.set_multi_port_mask(0x8001, 0x0001)
        mcu.wait_till_operation_is_completed()

        cmd = next(c for c in sent if c[1] == CMD_SET.SET_MULTI_PORT_MASK)
        port_mask = (cmd[2] << 8) | cmd[3]
        on_mask = (cmd[4] << 8) | cmd[5]
        assert port_mask == 0x8001, f"Expected 0x8001, got 0x{port_mask:04X}"
        assert on_mask == 0x0001, f"Expected 0x0001, got 0x{on_mask:04X}"


class TestPortIndexBoundaries:
    """Test port index boundary conditions."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_port_index_0_works(self, mcu):
        """Port index 0 (D1) should work."""
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is True

    def test_port_index_4_works(self, mcu):
        """Port index 4 (D5) should work."""
        mcu.turn_on_port(4)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[4] is True

    def test_port_index_15_accepted(self, mcu):
        """Port index 15 should be accepted (for future expansion)."""
        # Should not raise, even though no physical port exists
        mcu.turn_on_port(15)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[15] is True

    def test_port_index_16_rejected_by_mcu(self, mcu):
        """Port index 16 is rejected by Microcontroller validation.

        Microcontroller validates port indices (0-15) before sending commands.
        """
        with pytest.raises(ValueError) as exc_info:
            mcu.turn_on_port(16)
        assert "Invalid port_index 16" in str(exc_info.value)

    def test_negative_port_index_fails_byte_conversion(self, mcu):
        """Negative port index fails during byte conversion."""
        # Negative value can't be packed into unsigned byte
        with pytest.raises(ValueError):
            mcu.turn_on_port(-1)


class TestIntensityEdgeCases:
    """Test intensity value edge cases."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_intensity_exactly_0(self, mcu):
        """Intensity 0.0 should produce DAC value 0."""
        mcu.set_port_intensity(0, 0.0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_intensity[0] == 0

    def test_intensity_exactly_100(self, mcu):
        """Intensity 100.0 should produce DAC value 65535."""
        mcu.set_port_intensity(0, 100.0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_intensity[0] == 65535

    def test_intensity_very_small(self, mcu):
        """Very small intensity should not be 0."""
        mcu.set_port_intensity(0, 0.01)
        mcu.wait_till_operation_is_completed()
        # 0.01% = 6.5535, should round to at least 6
        assert mcu._serial.port_intensity[0] >= 6

    def test_intensity_99_999(self, mcu):
        """Intensity 99.999 should be very close to max but not equal."""
        mcu.set_port_intensity(0, 99.999)
        mcu.wait_till_operation_is_completed()
        # Should be close to 65535 but not necessarily equal
        assert mcu._serial.port_intensity[0] >= 65528

    def test_intensity_float_precision(self, mcu):
        """Float precision should not cause unexpected results."""
        # 33.333...% should give consistent results
        mcu.set_port_intensity(0, 100.0 / 3.0)
        mcu.wait_till_operation_is_completed()
        expected = int((100.0 / 3.0 / 100.0) * 65535)
        assert abs(mcu._serial.port_intensity[0] - expected) <= 1


class TestStateConsistency:
    """Test that state remains consistent across operations."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_turn_on_twice_remains_on(self, mcu):
        """Turning on a port twice should leave it on."""
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is True

    def test_turn_off_twice_remains_off(self, mcu):
        """Turning off a port twice should leave it off."""
        mcu.turn_off_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_off_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is False

    def test_intensity_persists_after_off_on(self, mcu):
        """Intensity should persist through off/on cycle."""
        mcu.set_port_intensity(0, 75)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_off_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        # Intensity should still be 75%
        expected = int(0.75 * 65535)
        assert mcu._serial.port_intensity[0] == expected

    def test_set_intensity_while_off_then_turn_on(self, mcu):
        """Setting intensity while off, then turning on should work."""
        mcu.turn_off_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.set_port_intensity(0, 50)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_intensity[0] == int(0.5 * 65535)

    def test_turn_off_all_clears_all_ports(self, mcu):
        """turn_off_all_ports should turn off all ports."""
        # Turn on several ports
        for i in range(5):
            mcu.turn_on_port(i)
            mcu.wait_till_operation_is_completed()

        # Verify they're on
        for i in range(5):
            assert mcu._serial.port_is_on[i] is True

        # Turn off all
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()

        # Verify all are off
        for i in range(16):
            assert mcu._serial.port_is_on[i] is False


class TestLegacyNewInteraction:
    """Test interaction between legacy and new illumination commands."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_legacy_set_illumination_updates_port_intensity(self, mcu):
        """Legacy set_illumination should update port intensity."""
        # Use legacy command to set D1 (source 11) to 60%
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D1, 60)
        mcu.wait_till_operation_is_completed()
        # Port 0 intensity should be updated
        expected = int(0.6 * 65535)
        assert mcu._serial.port_intensity[0] == expected

    def test_legacy_turn_on_updates_port_state(self, mcu):
        """Legacy turn_on_illumination should update port state."""
        # First set a source
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D2, 50)
        mcu.wait_till_operation_is_completed()
        # Then turn on
        mcu.turn_on_illumination()
        mcu.wait_till_operation_is_completed()
        # Port 1 (D2) should be on
        assert mcu._serial.port_is_on[1] is True

    def test_new_command_after_legacy(self, mcu):
        """New commands should work after legacy commands."""
        # Legacy: set D1
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D1, 50)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_illumination()
        mcu.wait_till_operation_is_completed()

        # New: turn on D2 as well
        mcu.set_port_intensity(1, 75)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(1)
        mcu.wait_till_operation_is_completed()

        # Both should be on
        assert mcu._serial.port_is_on[0] is True  # D1 from legacy
        assert mcu._serial.port_is_on[1] is True  # D2 from new

    def test_legacy_turn_off_after_new_commands(self, mcu):
        """Legacy turn_off should turn off all ports."""
        # Turn on multiple ports with new commands
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(1)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(2)
        mcu.wait_till_operation_is_completed()

        # Use legacy turn off
        mcu.turn_off_illumination()
        mcu.wait_till_operation_is_completed()

        # All should be off
        for i in range(5):
            assert mcu._serial.port_is_on[i] is False


class TestD3D4RoundTrip:
    """Round-trip tests verifying legacy and new commands control the same hardware.

    This is critical for the D3/D4 non-sequential mapping:
    - D3 has source code 14, maps to port index 2
    - D4 has source code 13, maps to port index 3
    """

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_legacy_d3_controls_port_2(self, mcu):
        """Legacy D3 (source 14) and new port_index 2 control the same hardware."""
        # Set intensity via legacy D3
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D3, 60)
        mcu.wait_till_operation_is_completed()

        # Verify port 2 received the intensity (not port 3!)
        expected = int(0.6 * 65535)
        assert mcu._serial.port_intensity[2] == expected
        assert mcu._serial.port_intensity[3] == 0  # D4 should be unchanged

        # Turn on via new API using port 2
        mcu.turn_on_port(2)
        mcu.wait_till_operation_is_completed()

        # Port 2 should be on
        assert mcu._serial.port_is_on[2] is True
        assert mcu._serial.port_is_on[3] is False

    def test_legacy_d4_controls_port_3(self, mcu):
        """Legacy D4 (source 13) and new port_index 3 control the same hardware."""
        # Set intensity via legacy D4
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D4, 80)
        mcu.wait_till_operation_is_completed()

        # Verify port 3 received the intensity (not port 2!)
        expected = int(0.8 * 65535)
        assert mcu._serial.port_intensity[3] == expected
        assert mcu._serial.port_intensity[2] == 0  # D3 should be unchanged

        # Turn on via new API using port 3
        mcu.turn_on_port(3)
        mcu.wait_till_operation_is_completed()

        # Port 3 should be on
        assert mcu._serial.port_is_on[3] is True
        assert mcu._serial.port_is_on[2] is False

    def test_new_intensity_legacy_turn_on_d3(self, mcu):
        """Set intensity via new API, turn on via legacy - D3 case."""
        # Set intensity on port 2 via new API
        mcu.set_port_intensity(2, 70)
        mcu.wait_till_operation_is_completed()

        # Select D3 via legacy (source 14)
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D3, 70)
        mcu.wait_till_operation_is_completed()

        # Turn on via legacy
        mcu.turn_on_illumination()
        mcu.wait_till_operation_is_completed()

        # Port 2 (D3) should be on
        assert mcu._serial.port_is_on[2] is True

    def test_new_intensity_legacy_turn_on_d4(self, mcu):
        """Set intensity via new API, turn on via legacy - D4 case."""
        # Set intensity on port 3 via new API
        mcu.set_port_intensity(3, 55)
        mcu.wait_till_operation_is_completed()

        # Select D4 via legacy (source 13)
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D4, 55)
        mcu.wait_till_operation_is_completed()

        # Turn on via legacy
        mcu.turn_on_illumination()
        mcu.wait_till_operation_is_completed()

        # Port 3 (D4) should be on
        assert mcu._serial.port_is_on[3] is True

    def test_d3_d4_independent_control(self, mcu):
        """D3 and D4 should be independently controllable despite adjacent source codes."""
        # Set different intensities for D3 (source 14 -> port 2) and D4 (source 13 -> port 3)
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D3, 30)
        mcu.wait_till_operation_is_completed()
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D4, 90)
        mcu.wait_till_operation_is_completed()

        # Verify correct mapping
        assert mcu._serial.port_intensity[2] == int(0.3 * 65535)  # D3 -> port 2
        assert mcu._serial.port_intensity[3] == int(0.9 * 65535)  # D4 -> port 3

        # Turn on both via new API
        mcu.turn_on_port(2)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(3)
        mcu.wait_till_operation_is_completed()

        # Both should be on independently
        assert mcu._serial.port_is_on[2] is True
        assert mcu._serial.port_is_on[3] is True

        # Turn off D3 via legacy turn_off (turns off all)
        mcu.turn_off_illumination()
        mcu.wait_till_operation_is_completed()

        # Both should be off (legacy turn_off affects all)
        assert mcu._serial.port_is_on[2] is False
        assert mcu._serial.port_is_on[3] is False

    def test_all_five_ports_round_trip(self, mcu):
        """Verify all 5 ports map correctly: D1-D5 to ports 0-4."""
        mappings = [
            (ILLUMINATION_CODE.ILLUMINATION_D1, 0),  # 11 -> 0
            (ILLUMINATION_CODE.ILLUMINATION_D2, 1),  # 12 -> 1
            (ILLUMINATION_CODE.ILLUMINATION_D3, 2),  # 14 -> 2 (non-sequential!)
            (ILLUMINATION_CODE.ILLUMINATION_D4, 3),  # 13 -> 3 (non-sequential!)
            (ILLUMINATION_CODE.ILLUMINATION_D5, 4),  # 15 -> 4
        ]

        for source_code, expected_port in mappings:
            # Reset all ports
            mcu.turn_off_all_ports()
            mcu.wait_till_operation_is_completed()
            for i in range(5):
                mcu.set_port_intensity(i, 0)
                mcu.wait_till_operation_is_completed()

            # Set intensity via legacy
            mcu.set_illumination(source_code, 50)
            mcu.wait_till_operation_is_completed()

            # Verify only the expected port has intensity
            expected_intensity = int(0.5 * 65535)
            for port in range(5):
                if port == expected_port:
                    assert (
                        mcu._serial.port_intensity[port] == expected_intensity
                    ), f"Source {source_code} should map to port {expected_port}"
                else:
                    assert (
                        mcu._serial.port_intensity[port] == 0
                    ), f"Port {port} should be unchanged when setting source {source_code}"


class TestFirmwareVersionBehavior:
    """Test firmware version detection and behavior."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_version_detected_after_any_command(self, mcu):
        """Firmware version should be detected after any command."""
        # Initially might be (0, 0) until we get a response
        initial = mcu.firmware_version
        assert isinstance(initial, tuple) and len(initial) == 2

        # Send a command to trigger response
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()

        # Now version should be detected (SimSerial reports 1.0)
        assert mcu.firmware_version == (1, 0)

    def test_supports_multi_port_true_for_v1(self, mcu):
        """supports_multi_port() should return True for v1.0+."""
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()
        assert mcu.supports_multi_port() is True

    def test_version_persists_across_commands(self, mcu):
        """Version should remain consistent across commands."""
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()
        v1 = mcu.firmware_version

        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        v2 = mcu.firmware_version

        mcu.set_port_intensity(0, 50)
        mcu.wait_till_operation_is_completed()
        v3 = mcu.firmware_version

        assert v1 == v2 == v3 == (1, 0)


class TestMultiPortMaskEdgeCases:
    """Test SET_MULTI_PORT_MASK edge cases."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_empty_mask_no_change(self, mcu):
        """Empty port_mask should not change any ports."""
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()

        # Empty mask - no ports selected
        mcu.set_multi_port_mask(0x0000, 0x0000)
        mcu.wait_till_operation_is_completed()

        # Port 0 should still be on
        assert mcu._serial.port_is_on[0] is True

    def test_partial_mask_only_affects_selected(self, mcu):
        """Only ports in port_mask should be affected."""
        # Turn on ports 0, 1, 2
        for i in range(3):
            mcu.turn_on_port(i)
            mcu.wait_till_operation_is_completed()

        # Select only port 1, turn it off
        mcu.set_multi_port_mask(0x0002, 0x0000)  # port_mask=D2, on_mask=off
        mcu.wait_till_operation_is_completed()

        # Port 0 and 2 should still be on, port 1 should be off
        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_is_on[1] is False
        assert mcu._serial.port_is_on[2] is True

    def test_all_16_ports_mask(self, mcu):
        """Mask 0xFFFF should address all 16 ports."""
        # Turn all on
        mcu.set_multi_port_mask(0xFFFF, 0xFFFF)
        mcu.wait_till_operation_is_completed()

        for i in range(16):
            assert mcu._serial.port_is_on[i] is True

        # Turn all off
        mcu.set_multi_port_mask(0xFFFF, 0x0000)
        mcu.wait_till_operation_is_completed()

        for i in range(16):
            assert mcu._serial.port_is_on[i] is False

    def test_alternating_on_off(self, mcu):
        """Test turning alternating ports on/off."""
        # Select all, turn on even ports only
        mcu.set_multi_port_mask(0xFFFF, 0x5555)  # 0101 0101 0101 0101
        mcu.wait_till_operation_is_completed()

        for i in range(16):
            if i % 2 == 0:
                assert mcu._serial.port_is_on[i] is True, f"Port {i} should be ON"
            else:
                assert mcu._serial.port_is_on[i] is False, f"Port {i} should be OFF"


class TestIlluminationControllerEdgeCases:
    """Test IlluminationController edge cases."""

    @pytest.fixture
    def controller(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        controller = IlluminationController(mcu)
        yield controller
        mcu.close()

    def test_get_active_ports_after_operations(self, controller):
        """get_active_ports should return correct list after operations."""
        controller.turn_on_port(0)
        controller.turn_on_port(2)
        controller.turn_on_port(4)

        active = controller.get_active_ports()
        assert sorted(active) == [0, 2, 4]

    def test_turn_on_multiple_empty_list(self, controller):
        """turn_on_multiple_ports with empty list should be no-op."""
        controller.turn_on_port(0)  # Turn one on first
        controller.turn_on_multiple_ports([])

        # Original state should be preserved
        assert controller.port_is_on[0] is True
        assert sum(controller.port_is_on.values()) == 1

    def test_intensity_state_tracking(self, controller):
        """Controller should track intensity state."""
        controller.set_port_intensity(0, 25)
        controller.set_port_intensity(1, 50)
        controller.set_port_intensity(2, 75)

        assert controller.port_intensity[0] == 25
        assert controller.port_intensity[1] == 50
        assert controller.port_intensity[2] == 75

    def test_invalid_port_in_turn_on_multiple(self, controller):
        """Invalid port in list should raise ValueError."""
        with pytest.raises(ValueError):
            controller.turn_on_multiple_ports([0, 1, 100])  # 100 is invalid
