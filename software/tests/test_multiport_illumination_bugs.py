"""Tests specifically designed to find bugs in multi-port illumination.

These tests target potential edge cases and implementation issues.
"""

import pytest
from control._def import CMD_SET, ILLUMINATION_CODE
from control.microcontroller import Microcontroller, SimSerial
from control.lighting import IlluminationController, NUM_ILLUMINATION_PORTS


class TestD3D4NonSequentialMapping:
    """Test the tricky D3/D4 non-sequential source code mapping.

    Source codes: D1=11, D2=12, D3=14, D4=13, D5=15
    Note: D3 and D4 are swapped! This is a common source of bugs.
    """

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_legacy_d3_maps_to_port_2(self, mcu):
        """Legacy D3 (source 14) should map to port index 2."""
        mcu.set_illumination(14, 50)  # D3 = 14
        mcu.wait_till_operation_is_completed()
        # Port 2 should have the intensity, not port 3
        expected = int(0.5 * 65535)
        assert mcu._serial.port_intensity[2] == expected
        assert mcu._serial.port_intensity[3] == 0  # D4 should be unchanged

    def test_legacy_d4_maps_to_port_3(self, mcu):
        """Legacy D4 (source 13) should map to port index 3."""
        mcu.set_illumination(13, 75)  # D4 = 13
        mcu.wait_till_operation_is_completed()
        # Port 3 should have the intensity, not port 2
        expected = int(0.75 * 65535)
        assert mcu._serial.port_intensity[3] == expected
        assert mcu._serial.port_intensity[2] == 0  # D3 should be unchanged

    def test_legacy_d3_d4_different_values(self, mcu):
        """Setting D3 and D4 separately should maintain separate intensities."""
        mcu.set_illumination(14, 30)  # D3
        mcu.wait_till_operation_is_completed()
        mcu.set_illumination(13, 70)  # D4
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_intensity[2] == int(0.3 * 65535)  # D3 at port 2
        assert mcu._serial.port_intensity[3] == int(0.7 * 65535)  # D4 at port 3

    def test_legacy_constants_match_expected(self, mcu):
        """Verify ILLUMINATION_CODE constants match expected values."""
        assert ILLUMINATION_CODE.ILLUMINATION_D1 == 11
        assert ILLUMINATION_CODE.ILLUMINATION_D2 == 12
        assert ILLUMINATION_CODE.ILLUMINATION_D3 == 14  # Not 13!
        assert ILLUMINATION_CODE.ILLUMINATION_D4 == 13  # Not 14!
        assert ILLUMINATION_CODE.ILLUMINATION_D5 == 15


class TestSetPortIlluminationCombined:
    """Test the combined SET_PORT_ILLUMINATION command."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_set_intensity_and_turn_on(self, mcu):
        """Should set intensity AND turn on in one command."""
        mcu.set_port_illumination(0, 50, turn_on=True)
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_intensity[0] == int(0.5 * 65535)

    def test_set_intensity_and_turn_off(self, mcu):
        """Should set intensity AND turn off in one command."""
        # First turn on
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        assert mcu._serial.port_is_on[0] is True

        # Set intensity and turn off
        mcu.set_port_illumination(0, 75, turn_on=False)
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_is_on[0] is False
        assert mcu._serial.port_intensity[0] == int(0.75 * 65535)

    def test_intensity_zero_with_turn_on(self, mcu):
        """Setting intensity to 0 with turn_on=True should work."""
        mcu.set_port_illumination(0, 0, turn_on=True)
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_intensity[0] == 0

    def test_on_flag_byte_value(self, mcu):
        """Verify on_flag is sent as 1 for True, 0 for False."""
        sent_commands = []
        original_write = mcu._serial.write

        def capture_write(data, **kwargs):
            sent_commands.append(bytes(data))
            return original_write(data, **kwargs)

        mcu._serial.write = capture_write

        # Turn on
        mcu.set_port_illumination(0, 50, turn_on=True)
        mcu.wait_till_operation_is_completed()
        cmd_on = next(c for c in sent_commands if c[1] == CMD_SET.SET_PORT_ILLUMINATION)
        assert cmd_on[5] == 1, "on_flag should be 1 for turn_on=True"

        sent_commands.clear()

        # Turn off
        mcu.set_port_illumination(0, 50, turn_on=False)
        mcu.wait_till_operation_is_completed()
        cmd_off = next(c for c in sent_commands if c[1] == CMD_SET.SET_PORT_ILLUMINATION)
        assert cmd_off[5] == 0, "on_flag should be 0 for turn_on=False"


class TestPortValidation:
    """Test port index validation gaps."""

    @pytest.fixture
    def controller(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        controller = IlluminationController(mcu)
        yield controller
        mcu.close()

    def test_port_16_raises_error(self, controller):
        """Port 16 should raise an error (only 0-15 valid)."""
        with pytest.raises(ValueError):
            controller.turn_on_port(16)

    def test_port_100_raises_error(self, controller):
        """Port 100 should raise an error."""
        with pytest.raises(ValueError):
            controller.set_port_intensity(100, 50)

    def test_port_float_raises_error(self, controller):
        """Float port index should raise TypeError."""
        with pytest.raises((TypeError, ValueError)):
            controller.turn_on_port(1.5)

    def test_port_string_raises_error(self, controller):
        """String port index should raise TypeError."""
        with pytest.raises((TypeError, ValueError)):
            controller.turn_on_port("D1")


class TestIntensityEdgeCases:
    """Test more intensity edge cases."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_intensity_nan_handling(self, mcu):
        """NaN intensity should be handled gracefully."""
        import math

        # This might raise or clamp - either is acceptable
        try:
            mcu.set_port_intensity(0, float("nan"))
            mcu.wait_till_operation_is_completed()
            # If it doesn't raise, check the result is sensible
            assert mcu._serial.port_intensity[0] >= 0
            assert mcu._serial.port_intensity[0] <= 65535
        except (ValueError, TypeError):
            pass  # Raising is also acceptable

    def test_intensity_inf_handling(self, mcu):
        """Infinity intensity should be clamped to 100%."""
        mcu.set_port_intensity(0, float("inf"))
        mcu.wait_till_operation_is_completed()
        # Should clamp to max
        assert mcu._serial.port_intensity[0] == 65535

    def test_intensity_negative_inf_handling(self, mcu):
        """Negative infinity should be clamped to 0%."""
        mcu.set_port_intensity(0, float("-inf"))
        mcu.wait_till_operation_is_completed()
        # Should clamp to 0
        assert mcu._serial.port_intensity[0] == 0


class TestMaskOverlapBehavior:
    """Test behavior when masks have overlapping bits."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_on_mask_superset_of_port_mask(self, mcu):
        """on_mask bits outside port_mask should be ignored."""
        # port_mask = 0x0001 (only port 0)
        # on_mask = 0x0003 (ports 0 and 1)
        # Only port 0 should be affected
        mcu.set_multi_port_mask(0x0001, 0x0003)
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_is_on[1] is False  # Not in port_mask

    def test_on_mask_subset_of_port_mask(self, mcu):
        """on_mask can be subset of port_mask (some off, some on)."""
        # port_mask = 0x0007 (ports 0, 1, 2)
        # on_mask = 0x0005 (ports 0 and 2 on, port 1 off)
        mcu.set_multi_port_mask(0x0007, 0x0005)
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_is_on[0] is True
        assert mcu._serial.port_is_on[1] is False
        assert mcu._serial.port_is_on[2] is True


class TestCommandSequencing:
    """Test specific command sequences that might reveal bugs."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_rapid_on_off_sequence(self, mcu):
        """Rapid on/off should end in correct state."""
        for _ in range(10):
            mcu.turn_on_port(0)
            mcu.wait_till_operation_is_completed()
            mcu.turn_off_port(0)
            mcu.wait_till_operation_is_completed()

        # Should end up off
        assert mcu._serial.port_is_on[0] is False

    def test_multiple_ports_interleaved(self, mcu):
        """Interleaved operations on different ports."""
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.set_port_intensity(1, 50)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(1)
        mcu.wait_till_operation_is_completed()
        mcu.set_port_intensity(0, 75)
        mcu.wait_till_operation_is_completed()
        mcu.turn_off_port(0)
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_is_on[0] is False
        assert mcu._serial.port_is_on[1] is True
        assert mcu._serial.port_intensity[0] == int(0.75 * 65535)
        assert mcu._serial.port_intensity[1] == int(0.5 * 65535)

    def test_turn_off_all_then_individual_on(self, mcu):
        """Individual on should work after turn_off_all."""
        mcu.turn_on_port(0)
        mcu.wait_till_operation_is_completed()
        mcu.turn_on_port(1)
        mcu.wait_till_operation_is_completed()

        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()

        mcu.turn_on_port(2)
        mcu.wait_till_operation_is_completed()

        assert mcu._serial.port_is_on[0] is False
        assert mcu._serial.port_is_on[1] is False
        assert mcu._serial.port_is_on[2] is True


class TestLegacyTurnOnSourceTracking:
    """Test that legacy turn_on_illumination tracks the correct source."""

    @pytest.fixture
    def mcu(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        yield mcu
        mcu.close()

    def test_turn_on_uses_last_set_source(self, mcu):
        """turn_on_illumination should turn on the last set source."""
        # Set D1
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D1, 50)
        mcu.wait_till_operation_is_completed()
        # Set D3 (this becomes the "current" source)
        mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D3, 75)
        mcu.wait_till_operation_is_completed()

        # Turn on - should turn on D3 (the last set source)
        mcu.turn_on_illumination()
        mcu.wait_till_operation_is_completed()

        # D3 (port 2) should be on
        assert mcu._serial.port_is_on[2] is True
        # D1 (port 0) should NOT be on (we only set intensity, didn't turn on)
        assert mcu._serial.port_is_on[0] is False

    def test_turn_on_without_set_first(self, mcu):
        """turn_on_illumination without set_illumination first."""
        # No source set yet - behavior is undefined but shouldn't crash
        try:
            mcu.turn_on_illumination()
            mcu.wait_till_operation_is_completed()
        except Exception:
            pass  # Either working or raising is acceptable


class TestIlluminationControllerMcuSync:
    """Test that IlluminationController stays in sync with MCU state."""

    @pytest.fixture
    def controller(self):
        sim_serial = SimSerial()
        mcu = Microcontroller(sim_serial, reset_and_initialize=False)
        controller = IlluminationController(mcu)
        yield controller
        mcu.close()

    def test_controller_tracks_intensity(self, controller):
        """Controller should track intensity it sets."""
        controller.set_port_intensity(0, 50)
        assert controller.port_intensity[0] == 50

        controller.set_port_intensity(0, 75)
        assert controller.port_intensity[0] == 75

    def test_controller_tracks_on_off(self, controller):
        """Controller should track on/off state it sets."""
        controller.turn_on_port(0)
        assert controller.port_is_on[0] is True

        controller.turn_off_port(0)
        assert controller.port_is_on[0] is False

    def test_turn_off_all_updates_controller_state(self, controller):
        """turn_off_all_ports should update controller state."""
        controller.turn_on_port(0)
        controller.turn_on_port(1)
        controller.turn_on_port(2)

        controller.turn_off_all_ports()

        # All should be tracked as off
        for i in range(NUM_ILLUMINATION_PORTS):
            assert controller.port_is_on[i] is False


class TestResponseVersionParsing:
    """Test firmware version parsing from responses."""

    def test_version_0_0_indicates_legacy(self):
        """Version (0, 0) indicates legacy firmware without version support."""
        sim = SimSerial()
        mcu = Microcontroller(sim, reset_and_initialize=False)

        # Before any command, version might be (0, 0)
        # After command, SimSerial returns 1.0

        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()

        # Should now have version from SimSerial
        assert mcu.firmware_version == (1, 0)
        mcu.close()

    def test_supports_multi_port_false_for_v0(self):
        """supports_multi_port should return False for legacy firmware."""
        sim = SimSerial()
        mcu = Microcontroller(sim, reset_and_initialize=False)

        # Manually set to legacy version
        mcu.firmware_version = (0, 0)
        assert mcu.supports_multi_port() is False

        mcu.firmware_version = (0, 9)
        assert mcu.supports_multi_port() is False

        mcu.close()

    def test_supports_multi_port_true_for_v1_plus(self):
        """supports_multi_port should return True for v1.0+."""
        sim = SimSerial()
        mcu = Microcontroller(sim, reset_and_initialize=False)

        mcu.firmware_version = (1, 0)
        assert mcu.supports_multi_port() is True

        mcu.firmware_version = (1, 5)
        assert mcu.supports_multi_port() is True

        mcu.firmware_version = (2, 0)
        assert mcu.supports_multi_port() is True

        mcu.close()
