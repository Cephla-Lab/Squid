import time

import pytest
import control._def
import control.microcontroller


def get_test_micro() -> control.microcontroller.Microcontroller:
    return control.microcontroller.Microcontroller(
        serial_device=control.microcontroller.get_microcontroller_serial_device(simulated=True)
    )


def assert_pos_almost_equal(expected, actual):
    assert len(actual) == len(expected)
    for e, a in zip(expected, actual):
        assert a == pytest.approx(e)


def test_create_simulated_microcontroller():
    micro = get_test_micro()


def test_microcontroller_simulated_positions():
    micro = get_test_micro()

    micro.move_x_to_usteps(1000)
    micro.wait_till_operation_is_completed()
    micro.move_y_to_usteps(2000)
    micro.wait_till_operation_is_completed()
    micro.move_z_to_usteps(3000)
    micro.wait_till_operation_is_completed()
    micro.move_theta_usteps(4000)
    micro.wait_till_operation_is_completed()

    assert_pos_almost_equal((1000, 2000, 3000, 4000), micro.get_pos())

    micro.home_x()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 2000, 3000, 4000), micro.get_pos())

    micro.home_y()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 3000, 4000), micro.get_pos())

    micro.home_z()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 4000), micro.get_pos())

    micro.home_theta()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    micro.move_x_to_usteps(1000)
    micro.wait_till_operation_is_completed()
    micro.move_y_to_usteps(2000)
    micro.wait_till_operation_is_completed()
    micro.move_z_to_usteps(3000)
    micro.wait_till_operation_is_completed()
    micro.move_theta_usteps(4000)
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((1000, 2000, 3000, 4000), micro.get_pos())

    micro.move_x_usteps(1)
    micro.wait_till_operation_is_completed()
    micro.move_y_usteps(2)
    micro.wait_till_operation_is_completed()
    micro.move_z_usteps(3)
    micro.wait_till_operation_is_completed()
    micro.move_theta_usteps(4)
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((1001, 2002, 3003, 4004), micro.get_pos())

    micro.zero_x()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 2002, 3003, 4004), micro.get_pos())

    micro.zero_y()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 3003, 4004), micro.get_pos())

    micro.zero_z()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 4004), micro.get_pos())

    micro.zero_theta()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    micro.move_x_to_usteps(1000)
    micro.wait_till_operation_is_completed()
    micro.move_y_to_usteps(2000)
    micro.wait_till_operation_is_completed()
    micro.move_z_to_usteps(3000)
    micro.wait_till_operation_is_completed()
    # There's no move_theta_to_usteps.
    assert_pos_almost_equal((1000, 2000, 3000, 0), micro.get_pos())

    micro.home_xy()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 3000, 0), micro.get_pos())


@pytest.mark.skip(
    reason="This is likely a bug, but I'm not sure yet.  Tracking in https://linear.app/cephla/issue/S-115/microcontroller-relative-and-absolute-position-sign-mismatch"
)
def test_microcontroller_absolute_and_relative_match():
    micro = get_test_micro()

    def wait():
        micro.wait_till_operation_is_completed()

    micro.home_x()
    wait()

    micro.home_y()
    wait()

    micro.home_z()
    wait()

    micro.home_theta()
    wait()

    # For all our axes, we'd expect that moving to an absolute position from zero brings us to that position.
    # Then doing a relative move of the negative of the absolute position should bring us back to zero.
    abs_position = 1234

    # X
    micro.move_x_to_usteps(abs_position)
    wait()
    assert_pos_almost_equal((abs_position, 0, 0, 0), micro.get_pos())

    micro.move_x_usteps(-abs_position)
    wait()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    # Y
    micro.move_y_to_usteps(abs_position)
    wait()
    assert_pos_almost_equal((0, abs_position, 0, 0), micro.get_pos())

    micro.move_y_usteps(-abs_position)
    wait()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    # Z
    micro.move_z_to_usteps(abs_position)
    wait()
    assert_pos_almost_equal((0, 0, abs_position, 0), micro.get_pos())

    micro.move_z_usteps(-abs_position)
    wait()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())


def test_microcontroller_reconnects_serial():
    micro = get_test_micro()
    serial = micro._serial

    def wait():
        micro.wait_till_operation_is_completed()

    some_pos = 1234
    micro.move_x_to_usteps(some_pos)
    wait()
    assert_pos_almost_equal((some_pos, 0, 0, 0), micro.get_pos())

    # Force closed, then make sure the microcontroller handles reconnecting.  Both in the write and read cases
    # For the read, sleep a bit first since we know we have a reader loop spinning that could blowup if reconnects
    # don't work properly.
    serial.close()

    time.sleep(1)
    micro.move_y_to_usteps(2 * some_pos)
    wait()
    assert_pos_almost_equal((some_pos, 2 * some_pos, 0, 0), micro.get_pos())

    serial.close()
    micro.move_z_usteps(3 * some_pos)
    wait()
    assert_pos_almost_equal((some_pos, 2 * some_pos, 3 * some_pos, 0), micro.get_pos())
