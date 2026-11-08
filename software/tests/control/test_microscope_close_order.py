"""Microscope.close() turns an external light source off before it closes the rest of the hardware."""

import control.microscope


def test_light_source_is_closed_before_the_microcontroller():
    scope = control.microscope.Microscope.build_from_global_config(True)
    order = []

    def recording(name, close):
        def wrapper():
            order.append(name)
            close()

        return wrapper

    scope.illumination_controller.close = recording("illumination", scope.illumination_controller.close)
    microcontroller = scope.low_level_drivers.microcontroller
    microcontroller.close = recording("microcontroller", microcontroller.close)

    scope.close()

    assert order == ["illumination", "microcontroller"]


def test_close_unlocks_a_locked_control_panel():
    # Closed at a Z lift's loading position: the joystick must work again without Squid.
    scope = control.microscope.Microscope.build_from_global_config(True)
    microcontroller = scope.low_level_drivers.microcontroller
    calls = []
    microcontroller.supports_panel_lock = lambda: True
    microcontroller.set_panel_lock = lambda locked: calls.append(("lock", locked))
    real_close = microcontroller.close
    microcontroller.close = lambda: (calls.append("close"), real_close())

    scope.close()

    assert calls == [("lock", False), "close"]
