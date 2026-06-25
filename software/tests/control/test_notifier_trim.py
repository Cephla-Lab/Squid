# tests/control/test_notifier_trim.py
from unittest.mock import patch

import control._def
from control.slack_notifier import SlackNotifier, AcquisitionStats


def _stats(reason):
    return AcquisitionStats(
        total_images=10,
        total_timepoints=2,
        total_duration_seconds=5.0,
        errors_encountered=0,
        experiment_id="e",
        reason=reason,
    )


def test_finish_message_sent_only_on_clean_completion(monkeypatch):
    monkeypatch.setattr(control._def.SlackNotifications, "NOTIFY_ON_ACQUISITION_FINISHED", True)
    n = SlackNotifier(bot_token="x", channel_id="C")
    with patch.object(n, "_queue_message") as q:
        n.notify_acquisition_finished(_stats("completed"))
    assert q.call_count == 1

    with patch.object(n, "_queue_message") as q:
        n.notify_acquisition_finished(_stats("error"))
        n.notify_acquisition_finished(_stats("user_abort"))
        n.notify_acquisition_finished(_stats("completed_with_errors"))
    assert q.call_count == 0  # watchdog owns these alerts
