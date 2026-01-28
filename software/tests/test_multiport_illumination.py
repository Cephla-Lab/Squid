"""Tests for multi-port illumination control (firmware v1.0+).

These tests verify the new multi-port illumination commands that allow
multiple illumination ports to be ON simultaneously with independent intensities.
"""

import pytest
from control._def import CMD_SET, ILLUMINATION_PORT
from control.microcontroller import Microcontroller, SimSerial
from control.lighting import IlluminationController, NUM_ILLUMINATION_PORTS


class TestIlluminationPortConstants:
    """Test ILLUMINATION_PORT constant definitions."""

    def test_port_indices_are_sequential(self):
        """Port indices should start at 0 and be sequential."""
        assert ILLUMINATION_PORT.D1 == 0
        assert ILLUMINATION_PORT.D2 == 1
        assert ILLUMINATION_PORT.D3 == 2
        assert ILLUMINATION_PORT.D4 == 3
        assert ILLUMINATION_PORT.D5 == 4

    def test_cmd_set_constants(self):
        """Command codes should match the protocol specification."""
        assert CMD_SET.SET_PORT_INTENSITY == 34
        assert CMD_SET.TURN_ON_PORT == 35
        assert CMD_SET.TURN_OFF_PORT == 36
        assert CMD_SET.SET_PORT_ILLUMINATION == 37
        assert CMD_SET.SET_MULTI_PORT_MASK == 38
        assert CMD_SET.TURN_OFF_ALL_PORTS == 39


class TestSimSerialMultiPort:
    """Test SimSerial multi-port illumination simulation."""

    @pytest.fixture
    def sim_serial(self):
        """Create a fresh SimSerial instance for each test."""
        return SimSerial()

    def test_initial_state(self, sim_serial):
        """All ports should be off with zero intensity initially."""
        assert len(sim_serial.port_is_on) == SimSerial.NUM_ILLUMINATION_PORTS
        assert len(sim_serial.port_intensity) == SimSerial.NUM_ILLUMINATION_PORTS
        assert all(not on for on in sim_serial.port_is_on)
        assert all(intensity == 0 for intensity in sim_serial.port_intensity)

    def test_turn_on_port(self, sim_serial):
        """TURN_ON_PORT command should turn on a specific port."""
        # Command: [cmd_id, 35, port, 0, 0, 0, 0, crc]
        sim_serial.write(bytes([1, CMD_SET.TURN_ON_PORT, 0, 0, 0, 0, 0, 0]))
        assert sim_serial.port_is_on[0] is True
        assert sim_serial.port_is_on[1] is False  # Other ports unchanged

    def test_turn_off_port(self, sim_serial):
        """TURN_OFF_PORT command should turn off a specific port."""
        # First turn on
        sim_serial.write(bytes([1, CMD_SET.TURN_ON_PORT, 0, 0, 0, 0, 0, 0]))
        assert sim_serial.port_is_on[0] is True

        # Then turn off
        sim_serial.write(bytes([2, CMD_SET.TURN_OFF_PORT, 0, 0, 0, 0, 0, 0]))
        assert sim_serial.port_is_on[0] is False

    def test_set_port_intensity(self, sim_serial):
        """SET_PORT_INTENSITY command should set DAC value for a port."""
        # Command: [cmd_id, 34, port, intensity_hi, intensity_lo, 0, 0, crc]
        # Set port 0 to intensity 0x8000 (50%)
        sim_serial.write(bytes([1, CMD_SET.SET_PORT_INTENSITY, 0, 0x80, 0x00, 0, 0, 0]))
        assert sim_serial.port_intensity[0] == 0x8000
        assert sim_serial.port_intensity[1] == 0  # Other ports unchanged

    def test_set_port_illumination(self, sim_serial):
        """SET_PORT_ILLUMINATION command should set intensity and on/off state."""
        # Command: [cmd_id, 37, port, intensity_hi, intensity_lo, on_flag, 0, crc]
        # Set port 2 to intensity 0xFFFF and turn on
        sim_serial.write(bytes([1, CMD_SET.SET_PORT_ILLUMINATION, 2, 0xFF, 0xFF, 1, 0, 0]))
        assert sim_serial.port_intensity[2] == 0xFFFF
        assert sim_serial.port_is_on[2] is True

        # Set port 2 intensity and turn off
        sim_serial.write(bytes([2, CMD_SET.SET_PORT_ILLUMINATION, 2, 0x40, 0x00, 0, 0, 0]))
        assert sim_serial.port_intensity[2] == 0x4000
        assert sim_serial.port_is_on[2] is False

    def test_set_multi_port_mask(self, sim_serial):
        """SET_MULTI_PORT_MASK command should update multiple ports."""
        # Command: [cmd_id, 38, mask_hi, mask_lo, on_hi, on_lo, 0, crc]
        # Turn on ports 0 and 1, leave others unchanged
        # port_mask = 0x0003 (bits 0,1), on_mask = 0x0003 (both on)
        sim_serial.write(bytes([1, CMD_SET.SET_MULTI_PORT_MASK, 0x00, 0x03, 0x00, 0x03, 0, 0]))
        assert sim_serial.port_is_on[0] is True
        assert sim_serial.port_is_on[1] is True
        assert sim_serial.port_is_on[2] is False  # Unchanged

    def test_set_multi_port_mask_partial_on_off(self, sim_serial):
        """SET_MULTI_PORT_MASK can turn some ports on and others off."""
        # First turn on ports 0, 1, 2
        sim_serial.write(bytes([1, CMD_SET.SET_MULTI_PORT_MASK, 0x00, 0x07, 0x00, 0x07, 0, 0]))
        assert sim_serial.port_is_on[0] is True
        assert sim_serial.port_is_on[1] is True
        assert sim_serial.port_is_on[2] is True

        # Now turn off port 1, turn on port 3, leave 0 and 2 unchanged
        # port_mask = 0x000A (bits 1,3), on_mask = 0x0008 (only bit 3)
        sim_serial.write(bytes([2, CMD_SET.SET_MULTI_PORT_MASK, 0x00, 0x0A, 0x00, 0x08, 0, 0]))
        assert sim_serial.port_is_on[0] is True  # Unchanged (not in mask)
        assert sim_serial.port_is_on[1] is False  # Turned off
        assert sim_serial.port_is_on[2] is True  # Unchanged (not in mask)
        assert sim_serial.port_is_on[3] is True  # Turned on

    def test_turn_off_all_ports(self, sim_serial):
        """TURN_OFF_ALL_PORTS command should turn off all ports."""
        # First turn on some ports
        sim_serial.write(bytes([1, CMD_SET.SET_MULTI_PORT_MASK, 0x00, 0x1F, 0x00, 0x1F, 0, 0]))
        assert all(sim_serial.port_is_on[i] for i in range(5))

        # Turn off all
        sim_serial.write(bytes([2, CMD_SET.TURN_OFF_ALL_PORTS, 0, 0, 0, 0, 0, 0]))
        assert all(not on for on in sim_serial.port_is_on)

    def test_invalid_port_index_ignored(self, sim_serial):
        """Commands with invalid port indices should be ignored."""
        # Try to turn on port 20 (invalid, only 16 ports)
        sim_serial.write(bytes([1, CMD_SET.TURN_ON_PORT, 20, 0, 0, 0, 0, 0]))
        # Should not crash, all ports should remain off
        assert all(not on for on in sim_serial.port_is_on)


class TestMicrocontrollerMultiPort:
    """Test Microcontroller multi-port illumination methods."""

    @pytest.fixture
    def mcu(self):
        """Create a Microcontroller with SimSerial for testing."""
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_set_port_intensity(self, mcu):
        """set_port_intensity should send correct command bytes."""
        mcu.set_port_intensity(0, 50)  # 50%
        mcu.wait_till_operation_is_completed()
        # Verify via SimSerial state
        assert mcu._serial.port_intensity[0] == int(0.5 * 65535)

    def test_turn_on_port(self, mcu):
        """turn_on_port should send correct command bytes."""
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is True

    def test_turn_off_port(self, mcu):
        """turn_off_port should send correct command bytes."""
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_off_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is False

    def test_set_port_illumination(self, mcu):
        """set_port_illumination should set intensity and on/off state."""
        mcu.set_port_illumination(2, 75, turn_on=True)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_intensity[2] == int(0.75 * 65535)
        assert mcu._serial.port_is_on[2] is True

    def test_set_multi_port_mask(self, mcu):
        """set_multi_port_mask should update multiple ports."""
        # Turn on D1 and D2
        mcu.set_multi_port_mask(0x0003, 0x0003)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_is_on[1] is True

    def test_turn_off_all_ports(self, mcu):
        """turn_off_all_ports should turn off all ports."""
        # First turn on some ports
        mcu.set_multi_port_mask(0x001F, 0x001F)
        mcu.wait_till_operation_is_completed()

        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()
        assert all(not on for on in mcu._serial.port_is_on)

    def test_multiple_ports_on_simultaneously(self, mcu):
        """Multiple ports can be on at the same time."""
        # Set intensities
        mcu.set_port_intensity(0, 30)
        mcu.wait_till_operation_is_completed()
        mcu.set_port_intensity(1, 60)
        mcu.wait_till_operation_is_completed()
        mcu.set_port_intensity(2, 90)
        mcu.wait_till_operation_is_completed()

        # Turn on all three
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(1)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(2)
        mcu.wait_till_operation_is_completed()

        # Verify all are on with different intensities
        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_is_on[1] is True
        assert mcu._serial.port_is_on[2] is True
        assert mcu._serial.port_intensity[0] == int(0.30 * 65535)
        assert mcu._serial.port_intensity[1] == int(0.60 * 65535)
        assert mcu._serial.port_intensity[2] == int(0.90 * 65535)


class TestIlluminationControllerMultiPort:
    """Test IlluminationController multi-port methods."""

    @pytest.fixture
    def controller(self):
        """Create an IlluminationController with simulated microcontroller."""
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        controller = IlluminationController(mcu)
        yield controller
        mcu.close()

    def test_initial_state(self, controller):
        """All ports should be off initially."""
        assert all(not on for on in controller.port_is_on.values())
        assert all(intensity == 0 for intensity in controller.port_intensity.values())

    def test_set_port_intensity(self, controller):
        """set_port_intensity should update intensity tracking."""
        controller.set_port_intensity(0, 50)
        assert controller.port_intensity[0] == 50

    def test_turn_on_port(self, controller):
        """turn_on_port should update state tracking."""
        controller.turn_on_port(0)
        assert controller.port_is_on[0] is True

    def test_turn_off_port(self, controller):
        """turn_off_port should update state tracking."""
        controller.turn_on_port(0)
        controller.turn_off_port(0)
        assert controller.port_is_on[0] is False

    def test_set_port_illumination(self, controller):
        """set_port_illumination should update both intensity and state."""
        controller.set_port_illumination(2, 75, turn_on=True)
        assert controller.port_intensity[2] == 75
        assert controller.port_is_on[2] is True

    def test_turn_on_multiple_ports(self, controller):
        """turn_on_multiple_ports should turn on all specified ports."""
        controller.turn_on_multiple_ports([0, 1, 2])
        assert controller.port_is_on[0] is True
        assert controller.port_is_on[1] is True
        assert controller.port_is_on[2] is True
        assert controller.port_is_on[3] is False  # Not in list

    def test_turn_off_all_ports(self, controller):
        """turn_off_all_ports should turn off all ports."""
        controller.turn_on_multiple_ports([0, 1, 2, 3, 4])
        controller.turn_off_all_ports()
        assert all(not on for on in controller.port_is_on.values())

    def test_get_active_ports(self, controller):
        """get_active_ports should return list of active port indices."""
        controller.turn_on_multiple_ports([1, 3])
        active = controller.get_active_ports()
        assert active == [1, 3]

    def test_get_active_ports_empty(self, controller):
        """get_active_ports should return empty list when no ports active."""
        assert controller.get_active_ports() == []

    def test_invalid_port_index_raises(self, controller):
        """Methods should raise ValueError for invalid port indices."""
        with pytest.raises(ValueError, match="Invalid port index"):
            controller.set_port_intensity(-1, 50)

        with pytest.raises(ValueError, match="Invalid port index"):
            controller.turn_on_port(NUM_ILLUMINATION_PORTS)

        with pytest.raises(ValueError, match="Invalid port index"):
            controller.turn_on_multiple_ports([0, 100])

    def test_turn_on_multiple_ports_empty_list(self, controller):
        """turn_on_multiple_ports with empty list should be a no-op."""
        controller.turn_on_multiple_ports([])
        assert all(not on for on in controller.port_is_on.values())
