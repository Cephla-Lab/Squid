"""Unit tests for the Slack notification system.

These tests verify the SlackNotifier class functionality including:
- Message queuing and dispatch
- Error notification throttling
- Timepoint and acquisition notifications
- Image conversion
- Bot API communication
"""

import json
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
        SlackMessage,
    )

    SLACK_NOTIFIER_AVAILABLE = True
except (SystemExit, Exception):
    SLACK_NOTIFIER_AVAILABLE = False
    SlackNotifier = None
    TimepointStats = None
    AcquisitionStats = None
    SlackMessage = None


pytestmark = pytest.mark.skipif(
    not SLACK_NOTIFIER_AVAILABLE, reason="SlackNotifier not available (configuration not loaded)"
)


@pytest.fixture
def notifier():
    """Create a SlackNotifier instance for testing."""
    # Enable notifications and set test credentials
    control._def.SlackNotifications.ENABLED = True
    control._def.SlackNotifications.BOT_TOKEN = "xoxb-test-token"
    control._def.SlackNotifications.CHANNEL_ID = "C0123456789"
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

    def test_init_with_custom_credentials(self):
        """Test initialization with custom bot token and channel ID."""
        custom_token = "xoxb-custom-token"
        custom_channel = "C9876543210"
        notifier = SlackNotifier(bot_token=custom_token, channel_id=custom_channel)
        assert notifier.bot_token == custom_token
        assert notifier.channel_id == custom_channel
        notifier.close()

    def test_bot_token_property_prefers_instance_value(self):
        """Test that instance bot token takes precedence over config."""
        control._def.SlackNotifications.BOT_TOKEN = "xoxb-config-token"
        notifier = SlackNotifier(bot_token="xoxb-instance-token")
        assert notifier.bot_token == "xoxb-instance-token"
        notifier.close()

    def test_channel_id_property_prefers_instance_value(self):
        """Test that instance channel ID takes precedence over config."""
        control._def.SlackNotifications.CHANNEL_ID = "C-config"
        notifier = SlackNotifier(channel_id="C-instance")
        assert notifier.channel_id == "C-instance"
        notifier.close()

    def test_enabled_property_checks_all_requirements(self):
        """Test that enabled requires ENABLED flag, bot token, and channel ID."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = "xoxb-test"
        control._def.SlackNotifications.CHANNEL_ID = "C12345"
        notifier = SlackNotifier()
        assert notifier.enabled is True
        notifier.close()

        control._def.SlackNotifications.ENABLED = False
        notifier = SlackNotifier()
        assert notifier.enabled is False
        notifier.close()

        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = None
        notifier = SlackNotifier()
        assert notifier.enabled is False
        notifier.close()

        control._def.SlackNotifications.BOT_TOKEN = "xoxb-test"
        control._def.SlackNotifications.CHANNEL_ID = None
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
            assert isinstance(args, SlackMessage)
            assert args.text == "Test message"

    def test_send_message_with_blocks(self, notifier):
        """Test that send_message includes blocks in payload."""
        blocks = [{"type": "section", "text": {"type": "plain_text", "text": "Test"}}]
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.send_message("Test message", blocks=blocks)
            args = mock_queue.call_args[0][0]
            assert args.blocks == blocks

    def test_queue_message_respects_disabled_state(self, disabled_notifier):
        """Test that queue_message does nothing when disabled."""
        disabled_notifier._message_queue = mock.MagicMock()
        disabled_notifier._queue_message(SlackMessage(text="test"))
        disabled_notifier._message_queue.put_nowait.assert_not_called()


class TestSlackNotifierErrorNotifications:
    """Tests for error notification functionality."""

    def test_notify_error_sends_message(self, notifier):
        """Test that notify_error sends a formatted error message."""
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_error("Test error", {"region": "A1", "fov": 3})
            mock_queue.assert_called_once()
            args = mock_queue.call_args[0][0]
            assert "Test error" in args.text

    def test_notify_error_throttling(self, notifier):
        """Test that repeated errors are throttled."""
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            # First call should go through
            notifier.notify_error("Same error")
            assert mock_queue.call_count == 1

            # Immediate second call should be throttled
            notifier.notify_error("Same error")
            assert mock_queue.call_count == 1

    def test_notify_error_respects_flag(self):
        """Test that notify_error respects NOTIFY_ON_ERROR flag."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = "xoxb-test"
        control._def.SlackNotifications.CHANNEL_ID = "C12345"
        control._def.SlackNotifications.NOTIFY_ON_ERROR = False

        notifier = SlackNotifier()
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_error("Test error")
            mock_queue.assert_not_called()
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
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_timepoint_complete(stats)
            mock_queue.assert_called_once()
            args = mock_queue.call_args[0][0]
            assert "5/10" in args.text

    def test_notify_timepoint_respects_flag(self):
        """Test that notify_timepoint_complete respects flag."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = "xoxb-test"
        control._def.SlackNotifications.CHANNEL_ID = "C12345"
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
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_timepoint_complete(stats)
            mock_queue.assert_not_called()
        notifier.close()


class TestSlackNotifierAcquisitionNotifications:
    """Tests for acquisition start/finish notifications."""

    def test_notify_acquisition_start_sends_message(self, notifier):
        """Test that notify_acquisition_start sends a formatted message."""
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_acquisition_start(
                experiment_id="test_exp",
                num_regions=5,
                num_timepoints=10,
                num_channels=3,
                num_z_levels=5,
            )
            mock_queue.assert_called_once()
            args = mock_queue.call_args[0][0]
            assert "test_exp" in args.text

    def test_notify_acquisition_start_respects_flag(self):
        """Test that notify_acquisition_start respects flag."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = "xoxb-test"
        control._def.SlackNotifications.CHANNEL_ID = "C12345"
        control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_START = False

        notifier = SlackNotifier()
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_acquisition_start(
                experiment_id="test",
                num_regions=1,
                num_timepoints=1,
                num_channels=1,
                num_z_levels=1,
            )
            mock_queue.assert_not_called()
        notifier.close()

    def test_notify_acquisition_finished_sends_message(self, notifier):
        """Test that notify_acquisition_finished sends a formatted message."""
        stats = AcquisitionStats(
            total_images=5000,
            total_timepoints=10,
            total_duration_seconds=7200,
            errors_encountered=2,
            experiment_id="test_exp",
        )
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_acquisition_finished(stats)
            mock_queue.assert_called_once()
            args = mock_queue.call_args[0][0]
            assert "test_exp" in args.text

    def test_notify_acquisition_finished_respects_flag(self):
        """Test that notify_acquisition_finished respects flag."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = "xoxb-test"
        control._def.SlackNotifications.CHANNEL_ID = "C12345"
        control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_FINISHED = False

        notifier = SlackNotifier()
        stats = AcquisitionStats(
            total_images=100,
            total_timepoints=1,
            total_duration_seconds=60,
            errors_encountered=0,
            experiment_id="test",
        )
        with mock.patch.object(notifier, "_queue_message") as mock_queue:
            notifier.notify_acquisition_finished(stats)
            mock_queue.assert_not_called()
        notifier.close()


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


class TestSlackNotifierBotAPI:
    """Tests for Slack Bot API communication."""

    def test_post_message_success(self, notifier):
        """Test successful message post via Bot API."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps({"ok": True, "ts": "1234567890.123456"}).encode()
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_response):
            success, ts = notifier._post_message("test message")
            assert success is True
            assert ts == "1234567890.123456"

    def test_post_message_api_error(self, notifier):
        """Test handling Slack API error response."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps({"ok": False, "error": "channel_not_found"}).encode()
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_response):
            success, ts = notifier._post_message("test message")
            assert success is False
            assert ts is None

    def test_post_message_network_failure(self, notifier):
        """Test handling network failure."""
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection failed"),
        ):
            success, ts = notifier._post_message("test message")
            assert success is False
            assert ts is None

    def test_post_message_no_credentials(self, notifier):
        """Test that posting fails gracefully with no credentials."""
        notifier._bot_token = None
        notifier._channel_id = None
        control._def.SlackNotifications.BOT_TOKEN = None
        control._def.SlackNotifications.CHANNEL_ID = None
        success, ts = notifier._post_message("test message")
        assert success is False
        assert ts is None


class TestSlackNotifierTestConnection:
    """Tests for connection testing."""

    def test_test_connection_success(self, notifier):
        """Test successful connection test."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps({"ok": True, "ts": "123"}).encode()
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_response):
            success, message = notifier.test_connection()
            assert success is True
            assert "successful" in message.lower()

    def test_test_connection_no_token(self):
        """Test connection test with no bot token."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = None
        control._def.SlackNotifications.CHANNEL_ID = "C12345"
        notifier = SlackNotifier()
        success, message = notifier.test_connection()
        assert success is False
        assert "no bot token" in message.lower()
        notifier.close()

    def test_test_connection_no_channel(self):
        """Test connection test with no channel ID."""
        control._def.SlackNotifications.ENABLED = True
        control._def.SlackNotifications.BOT_TOKEN = "xoxb-test"
        control._def.SlackNotifications.CHANNEL_ID = None
        notifier = SlackNotifier()
        success, message = notifier.test_connection()
        assert success is False
        assert "no channel" in message.lower()
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
