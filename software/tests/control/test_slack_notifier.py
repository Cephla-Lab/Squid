"""Unit tests for the Slack notification system.

These tests verify the SlackNotifier class functionality including:
- Message queuing and dispatch
- Error notification throttling
- Timepoint and acquisition notifications
- Image conversion
- Webhook communication
"""

import json
import queue
import time
import urllib.error
import urllib.request
from unittest import mock

import numpy as np
import pytest

# Import with config handling - skip if configuration not available
try:
    import control._def
    from control.slack_notifier import (
        SlackNotifier,
        TimepointStats,
        AcquisitionStats,
    )

    SLACK_NOTIFIER_AVAILABLE = True
except (SystemExit, Exception):
    SLACK_NOTIFIER_AVAILABLE = False
    SlackNotifier = None
    TimepointStats = None
    AcquisitionStats = None


pytestmark = pytest.mark.skipif(
    not SLACK_NOTIFIER_AVAILABLE, reason="SlackNotifier not available (configuration not loaded)"
)


@pytest.fixture
def notifier():
    """Create a SlackNotifier instance for testing."""
    # Enable notifications and set a test webhook URL
    control._def.SlackNotifications.ENABLED = True
    control._def.SlackNotifications.WEBHOOK_URL = "https://hooks.slack.com/test"
    control._def.SlackNotifications.NOTIFY_ON_ERROR = True
    control._def.SlackNotifications.NOTIFY_ON_TIMEPOINT_COMPLETE = True
    control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_START = True
    control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_FINISHED = True
    control._def.SlackNotifications.SEND_MOSAIC_SNAPSHOTS = True

    notifier = SlackNotifier()
    yield notifier
    notifier.close()


@pytest.fixture
def disabled_notifier():
    """Create a disabled SlackNotifier instance for testing."""
    control._def.SlackNotifications.ENABLED = False
    notifier = SlackNotifier()
    yield notifier
    notifier.close()


class TestSlackNotifierInit:
    """Tests for SlackNotifier initialization."""

    def test_init_creates_worker_thread(self, notifier):
        """Test that initialization starts a worker thread."""
        assert notifier._worker_thread is not None
        assert notifier._worker_thread.is_alive()

    def test_init_with_custom_webhook_url(self):
        """Test initialization with a custom webhook URL."""
        custom_url = "https://custom.webhook.url"
        notifier = SlackNotifier(webhook_url=custom_url)
        assert notifier.webhook_url == custom_url
        notifier.close()

    def test_webhook_url_property_prefers_instance_value(self):
        """Test that instance webhook URL takes precedence over config."""
        control._def.SlackNotifications.WEBHOOK_URL = "https://config.url"
        notifier = SlackNotifier(webhook_url="https://instance.url")
        assert notifier.webhook_url == "https://instance.url"
        notifier.close()

    def test_enabled_property_checks_both_flags(self):
        """Test that enabled requires both ENABLED flag and webhook URL."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.WEBHOOK_URL = "https://test.url"
        notifier = SlackNotifier()
        assert notifier.enabled is True
        notifier.close()

        control._def.SlackNotifications.ENABLED = False
        notifier = SlackNotifier()
        assert notifier.enabled is False
        notifier.close()

        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.WEBHOOK_URL = None
        notifier = SlackNotifier()
        assert notifier.enabled is False
        notifier.close()


class TestSlackNotifierMessages:
    """Tests for message sending functionality."""

    def test_send_message_queues_payload(self, notifier):
        """Test that send_message adds payload to queue."""
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.send_message("Test message")
            mock_queue.assert_called_once()
            args = mock_queue.call_args[0][0]
            assert args["text"] == "Test message"

    def test_send_message_with_blocks(self, notifier):
        """Test that send_message includes blocks in payload."""
        blocks = [{"type": "section", "text": {"type": "plain_text", "text": "Test"}}]
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.send_message("Test message", blocks=blocks)
            args = mock_queue.call_args[0][0]
            assert args["blocks"] == blocks

    def test_queue_message_respects_disabled_state(self, disabled_notifier):
        """Test that queue_message does nothing when disabled."""
        disabled_notifier._message_queue = mock.MagicMock()
        disabled_notifier._queue_message({"text": "test"})
        disabled_notifier._message_queue.put_nowait.assert_not_called()


class TestSlackNotifierErrorNotifications:
    """Tests for error notification functionality."""

    def test_notify_error_sends_message(self, notifier):
        """Test that notify_error sends a formatted error message."""
        with mock.patch.object(notifier, "send_message") as mock_send:
            notifier.notify_error("Test error", {"region": "A1", "fov": 3})
            mock_send.assert_called_once()
            args = mock_send.call_args
            assert "Test error" in args[0][0]

    def test_notify_error_throttling(self, notifier):
        """Test that repeated errors are throttled."""
        with mock.patch.object(notifier, "send_message") as mock_send:
            # First call should go through
            notifier.notify_error("Same error")
            assert mock_send.call_count == 1

            # Immediate second call should be throttled
            notifier.notify_error("Same error")
            assert mock_send.call_count == 1

    def test_notify_error_respects_flag(self):
        """Test that notify_error respects NOTIFY_ON_ERROR flag."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.WEBHOOK_URL = "https://test.url"
        control._def.SlackNotifications.NOTIFY_ON_ERROR = False

        notifier = SlackNotifier()
        with mock.patch.object(notifier, "send_message") as mock_send:
            notifier.notify_error("Test error")
            mock_send.assert_not_called()
        notifier.close()


class TestSlackNotifierTimepointNotifications:
    """Tests for timepoint notification functionality."""

    def test_notify_timepoint_complete_sends_message(self, notifier):
        """Test that notify_timepoint_complete sends a formatted message."""
        stats = TimepointStats(
            timepoint=5,
            total_timepoints=10,
            elapsed_seconds=3600,
            estimated_remaining_seconds=3600,
            images_captured=1000,
            fovs_captured=100,
            laser_af_successes=95,
            laser_af_failures=5,
            laser_af_failure_reasons=[],
        )
        with mock.patch.object(notifier, "send_message") as mock_send:
            notifier.notify_timepoint_complete(stats)
            mock_send.assert_called_once()
            args = mock_send.call_args
            assert "5/10" in args[0][0]

    def test_notify_timepoint_respects_flag(self):
        """Test that notify_timepoint_complete respects flag."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.WEBHOOK_URL = "https://test.url"
        control._def.SlackNotifications.NOTIFY_ON_TIMEPOINT_COMPLETE = False

        notifier = SlackNotifier()
        stats = TimepointStats(
            timepoint=1,
            total_timepoints=10,
            elapsed_seconds=60,
            estimated_remaining_seconds=540,
            images_captured=100,
            fovs_captured=10,
            laser_af_successes=10,
            laser_af_failures=0,
            laser_af_failure_reasons=[],
        )
        with mock.patch.object(notifier, "send_message") as mock_send:
            notifier.notify_timepoint_complete(stats)
            mock_send.assert_not_called()
        notifier.close()


class TestSlackNotifierAcquisitionNotifications:
    """Tests for acquisition start/finish notifications."""

    def test_notify_acquisition_start_sends_message(self, notifier):
        """Test that notify_acquisition_start sends a formatted message."""
        with mock.patch.object(notifier, "send_message") as mock_send:
            notifier.notify_acquisition_start(
                experiment_id="test_exp",
                num_regions=5,
                num_timepoints=10,
                num_channels=3,
                num_z_levels=5,
            )
            mock_send.assert_called_once()
            args = mock_send.call_args
            assert "test_exp" in args[0][0]

    def test_notify_acquisition_finished_sends_message(self, notifier):
        """Test that notify_acquisition_finished sends a formatted message."""
        stats = AcquisitionStats(
            total_images=5000,
            total_timepoints=10,
            total_duration_seconds=7200,
            errors_encountered=2,
            experiment_id="test_exp",
        )
        with mock.patch.object(notifier, "send_message") as mock_send:
            notifier.notify_acquisition_finished(stats)
            mock_send.assert_called_once()
            args = mock_send.call_args
            assert "test_exp" in args[0][0]


class TestSlackNotifierTimeEstimation:
    """Tests for time estimation functionality."""

    def test_record_timepoint_duration(self, notifier):
        """Test recording timepoint durations."""
        notifier.record_timepoint_duration(60.0)
        notifier.record_timepoint_duration(65.0)
        assert len(notifier._timepoint_durations) == 2

    def test_estimate_remaining_time(self, notifier):
        """Test remaining time estimation."""
        notifier.record_timepoint_duration(60.0)
        notifier.record_timepoint_duration(60.0)

        # After 2 timepoints of 10 total, 8 remaining at 60s each
        remaining = notifier.estimate_remaining_time(2, 10)
        assert remaining == 480.0  # 8 * 60

    def test_estimate_remaining_time_no_data(self, notifier):
        """Test remaining time estimation with no data."""
        remaining = notifier.estimate_remaining_time(1, 10)
        assert remaining == 0.0


class TestSlackNotifierFormatting:
    """Tests for duration formatting."""

    def test_format_duration_seconds(self, notifier):
        """Test formatting durations under a minute."""
        assert notifier._format_duration(30) == "30s"
        assert notifier._format_duration(59) == "59s"

    def test_format_duration_minutes(self, notifier):
        """Test formatting durations in minutes."""
        assert notifier._format_duration(60) == "1m"
        assert notifier._format_duration(3599) == "60m"

    def test_format_duration_hours(self, notifier):
        """Test formatting durations in hours."""
        assert notifier._format_duration(3600) == "1h 0m"
        assert notifier._format_duration(7200) == "2h 0m"
        assert notifier._format_duration(5400) == "1h 30m"


class TestSlackNotifierImageConversion:
    """Tests for image conversion functionality."""

    def test_image_to_png_bytes_uint8(self, notifier):
        """Test converting uint8 image to PNG bytes."""
        image = np.zeros((100, 100), dtype=np.uint8)
        image[40:60, 40:60] = 255

        png_bytes = notifier._image_to_png_bytes(image)
        assert len(png_bytes) > 0
        # PNG files start with specific magic bytes
        assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    def test_image_to_png_bytes_uint16(self, notifier):
        """Test converting uint16 image to PNG bytes."""
        image = np.zeros((100, 100), dtype=np.uint16)
        image[40:60, 40:60] = 65535

        png_bytes = notifier._image_to_png_bytes(image)
        assert len(png_bytes) > 0
        assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    def test_image_to_png_bytes_large_image_resize(self, notifier):
        """Test that large images are resized."""
        # Create an image larger than MAX_IMAGE_SIZE
        large_size = notifier.MAX_IMAGE_SIZE * 2
        image = np.zeros((large_size, large_size), dtype=np.uint8)

        png_bytes = notifier._image_to_png_bytes(image)
        assert len(png_bytes) > 0


class TestSlackNotifierWebhook:
    """Tests for webhook communication."""

    def test_send_to_slack_success(self, notifier):
        """Test successful webhook post."""
        mock_response = mock.MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_response):
            result = notifier._send_to_slack({"text": "test"})
            assert result is True

    def test_send_to_slack_failure(self, notifier):
        """Test handling webhook failure."""
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection failed"),
        ):
            result = notifier._send_to_slack({"text": "test"})
            assert result is False

    def test_send_to_slack_no_url(self, notifier):
        """Test that sending fails gracefully with no URL."""
        notifier._webhook_url = None
        control._def.SlackNotifications.WEBHOOK_URL = None
        result = notifier._send_to_slack({"text": "test"})
        assert result is False


class TestSlackNotifierTestConnection:
    """Tests for connection testing."""

    def test_test_connection_success(self, notifier):
        """Test successful connection test."""
        mock_response = mock.MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_response):
            success, message = notifier.test_connection()
            assert success is True
            assert "successful" in message.lower()

    def test_test_connection_no_url(self):
        """Test connection test with no URL."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.WEBHOOK_URL = None
        notifier = SlackNotifier()
        success, message = notifier.test_connection()
        assert success is False
        assert "no webhook url" in message.lower()
        notifier.close()


class TestSlackNotifierClose:
    """Tests for cleanup functionality."""

    def test_close_stops_worker_thread(self, notifier):
        """Test that close() stops the worker thread."""
        assert notifier._worker_thread.is_alive()
        notifier.close()
        # Give thread time to stop
        time.sleep(0.5)
        assert not notifier._worker_thread.is_alive()
