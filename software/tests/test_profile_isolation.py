"""The autouse profile fixture must isolate tests from each other, not just from the machine.

The application persists channel settings through ConfigRepository as the user edits live
control spinboxes, so a widget-driven test writes to whatever profile directory the fixture
points at. If that directory were shared for the whole session, one test's edit would decide
what every later test reads, and outcomes would depend on execution order.

These two tests are order-dependent by construction: the first writes, the second asserts it
did not leak. Keep them in this order.
"""

from control.core.config.repository import ConfigRepository

CHANNEL = "BF LED matrix full"
POISON_EXPOSURE = 1234.0


def _exposure_of(objective):
    repo = ConfigRepository()
    repo.set_profile("default")
    channels = repo.get_merged_channels(objective)
    return next(c.camera_settings.exposure_time_ms for c in channels if c.name == CHANNEL)


def test_a_writes_a_channel_setting_through_the_repository():
    repo = ConfigRepository()
    repo.set_profile("default")
    objective = next(iter(repo.get_available_objectives()))

    assert repo.update_channel_setting(objective, CHANNEL, "ExposureTime", POISON_EXPOSURE)
    assert _exposure_of(objective) == POISON_EXPOSURE


def test_b_does_not_see_the_previous_tests_write():
    repo = ConfigRepository()
    repo.set_profile("default")
    objective = next(iter(repo.get_available_objectives()))

    assert _exposure_of(objective) != POISON_EXPOSURE, (
        "the previous test's channel edit leaked into this one; the profile directory is "
        "being shared across tests instead of copied per test"
    )
