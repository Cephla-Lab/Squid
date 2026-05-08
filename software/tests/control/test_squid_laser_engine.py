"""Unit tests for SquidLaserEngine and its supporting types."""

import pytest
from control.serial_peripherals import (
    LaserChannelState,
    TcmModuleInfo,
    LaserChannelInfo,
    SquidLaserEngineStatus,
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
