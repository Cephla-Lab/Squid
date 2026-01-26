"""Slack notification system for Squid microscope acquisitions.

Provides real-time Slack notifications for acquisition events including errors,
timepoint completions, and acquisition start/end. Uses a background thread
queue to avoid blocking acquisition operations.

Uses Slack Bot API for message posting and image uploads.
"""

import io
import json
import queue
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import numpy as np

import control._def
import squid.logging

log = squid.logging.get_logger(__name__)


@dataclass
class TimepointStats:
    """Statistics for a completed timepoint."""

    timepoint: int
    total_timepoints: int
    elapsed_seconds: float
    estimated_remaining_seconds: float
    images_captured: int
    fovs_captured: int
    laser_af_successes: int
    laser_af_failures: int
    laser_af_failure_reasons: List[str]


@dataclass
class AcquisitionStats:
    """Statistics for a completed acquisition."""

    total_images: int
    total_timepoints: int
    total_duration_seconds: float
    errors_encountered: int
    experiment_id: str


@dataclass
class SlackMessage:
    """A message to be sent to Slack, optionally with an image."""

    text: str
    blocks: Optional[List[Dict[str, Any]]] = None
    image_data: Optional[bytes] = None
    image_title: Optional[str] = None


class SlackNotifier:
    """Sends notifications to Slack via Bot API.

    Uses a background thread with a queue to dispatch messages without
    blocking the acquisition thread. Supports rate limiting for error
    messages to prevent spam. Supports image uploads via the Slack API.
    """

    ERROR_THROTTLE_SECONDS = 5.0  # Minimum time between error notifications
    MAX_IMAGE_SIZE = 1024  # Maximum dimension for image attachments
    QUEUE_TIMEOUT = 1.0  # Timeout for queue operations
    SLACK_API_BASE = "https://slack.com/api"

    def __init__(
        self,
        bot_token: Optional[str] = None,
        channel_id: Optional[str] = None,
    ):
        """Initialize the Slack notifier.

        Args:
            bot_token: Slack Bot Token (xoxb-...) for API access.
            channel_id: Slack Channel ID (C...) to post to.
        """
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._message_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_error_time: Dict[str, float] = {}
        self._lock = threading.Lock()

        # Track acquisition state
        self._acquisition_start_time: Optional[float] = None
        self._current_experiment_id: Optional[str] = None
        self._timepoint_durations: List[float] = []

        # Pending image for next message (set from main thread via signal)
        self._pending_image: Optional[bytes] = None
        self._pending_image_lock = threading.Lock()

        # Start the background worker thread
        self._start_worker()

    @property
    def bot_token(self) -> Optional[str]:
        """Get the current bot token, preferring instance value over config."""
        if self._bot_token:
            return self._bot_token
        return control._def.SlackNotifications.BOT_TOKEN

    @bot_token.setter
    def bot_token(self, value: Optional[str]):
        """Set the bot token."""
        self._bot_token = value

    @property
    def channel_id(self) -> Optional[str]:
        """Get the current channel ID, preferring instance value over config."""
        if self._channel_id:
            return self._channel_id
        return control._def.SlackNotifications.CHANNEL_ID

    @channel_id.setter
    def channel_id(self, value: Optional[str]):
        """Set the channel ID."""
        self._channel_id = value

    @property
    def enabled(self) -> bool:
        """Check if Slack notifications are enabled."""
        return control._def.SlackNotifications.ENABLED and self.bot_token is not None and self.channel_id is not None

    def _start_worker(self):
        """Start the background worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="SlackNotifierWorker")
        self._worker_thread.start()

    def _worker_loop(self):
        """Background worker loop that processes the message queue."""
        while not self._stop_event.is_set():
            try:
                message = self._message_queue.get(timeout=self.QUEUE_TIMEOUT)
                if message is None:  # Shutdown signal
                    break
                self._send_message(message)
            except queue.Empty:
                continue
            except Exception as e:
                log.warning(f"Error in Slack worker loop: {e}")

    def _post_message(self, text: str, blocks: Optional[list] = None) -> Tuple[bool, Optional[str]]:
        """Post a message to Slack using chat.postMessage API.

        Args:
            text: Fallback text for the message.
            blocks: Optional Block Kit blocks for rich formatting.

        Returns:
            Tuple of (success, message_ts) where message_ts is the timestamp
            of the posted message (used for threading).
        """
        token = self.bot_token
        channel = self.channel_id

        if not token or not channel:
            log.debug("No Slack bot token or channel configured")
            return False, None

        try:
            log.info(f"Sending Slack message: {text[:50]}")
            payload = {
                "channel": channel,
                "text": text,
            }
            if blocks:
                payload["blocks"] = blocks

            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                f"{self.SLACK_API_BASE}/chat.postMessage",
                data=data,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
                if result.get("ok"):
                    log.info("Slack message sent successfully")
                    return True, result.get("ts")
                else:
                    log.warning(f"Slack API error: {result.get('error')}")
                    return False, None

        except urllib.error.URLError as e:
            log.warning(f"Failed to send Slack message: {e}")
            return False, None
        except Exception as e:
            log.warning(f"Unexpected error sending Slack message: {e}")
            return False, None

    def _upload_image(
        self,
        image_data: bytes,
        title: str,
        initial_comment: Optional[str] = None,
        thread_ts: Optional[str] = None,
    ) -> bool:
        """Upload an image to Slack using the new files.getUploadURLExternal API.

        The new Slack file upload flow:
        1. Call files.getUploadURLExternal to get an upload URL and file_id
        2. Upload the file content to that URL
        3. Call files.completeUploadExternal to complete and share to channel

        Args:
            image_data: PNG image data as bytes.
            title: Title for the image.
            initial_comment: Optional comment to post with the image.
            thread_ts: Optional thread timestamp to post as reply.

        Returns:
            True if successful, False otherwise.
        """
        token = self.bot_token
        channel = self.channel_id

        if not token or not channel:
            log.debug("No Slack bot token or channel configured")
            return False

        if not image_data:
            log.debug("No image data to upload")
            return False

        try:
            log.info(f"Uploading image to Slack: {title}")
            filename = "mosaic.png"

            # Step 1: Get upload URL (uses form-urlencoded params)
            get_url_params = urllib.parse.urlencode(
                {
                    "filename": filename,
                    "length": len(image_data),
                }
            ).encode("utf-8")
            get_url_request = urllib.request.Request(
                f"{self.SLACK_API_BASE}/files.getUploadURLExternal",
                data=get_url_params,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            )

            with urllib.request.urlopen(get_url_request, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not result.get("ok"):
                    log.warning(f"Slack API error getting upload URL: {result.get('error')}")
                    return False
                upload_url = result.get("upload_url")
                file_id = result.get("file_id")
                log.debug(f"Got upload URL, file_id={file_id}")

            # Step 2: Upload file content to the URL
            upload_request = urllib.request.Request(
                upload_url,
                data=image_data,
                headers={
                    "Content-Type": "application/octet-stream",
                },
                method="POST",
            )

            with urllib.request.urlopen(upload_request, timeout=30) as response:
                # Upload endpoint returns 200 on success
                if response.status != 200:
                    log.warning(f"Failed to upload file content: HTTP {response.status}")
                    return False

            # Step 3: Complete upload and share to channel
            # The files parameter must be a JSON-encoded string
            files_json = json.dumps([{"id": file_id, "title": title}])
            complete_params = {
                "files": files_json,
                "channel_id": channel,
            }
            log.debug(f"Completing upload with params: {complete_params}")
            if thread_ts:
                complete_params["thread_ts"] = thread_ts
            if initial_comment:
                complete_params["initial_comment"] = initial_comment

            complete_request = urllib.request.Request(
                f"{self.SLACK_API_BASE}/files.completeUploadExternal",
                data=urllib.parse.urlencode(complete_params).encode("utf-8"),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            )

            with urllib.request.urlopen(complete_request, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
                if result.get("ok"):
                    log.info("Slack image uploaded successfully")
                    return True
                else:
                    log.warning(f"Slack API error completing upload: {result}")
                    return False

        except urllib.error.URLError as e:
            log.warning(f"Failed to upload image to Slack: {e}")
            return False
        except Exception as e:
            log.warning(f"Unexpected error uploading image to Slack: {e}")
            return False

    def _send_message(self, message: SlackMessage):
        """Send a SlackMessage, including image upload if present."""
        # First post the text message
        success, ts = self._post_message(message.text, message.blocks)

        # If there's an image and message was successful, upload it as a reply
        if success and message.image_data and control._def.SlackNotifications.SEND_MOSAIC_SNAPSHOTS:
            self._upload_image(
                message.image_data,
                message.image_title or "Mosaic",
                thread_ts=ts,
            )

    def _queue_message(self, message: SlackMessage):
        """Add a message to the dispatch queue."""
        if not self.enabled:
            log.debug(f"Slack notifications disabled, skipping message: {message.text[:50]}")
            return
        try:
            log.info(f"Queuing Slack message: {message.text[:50]}")
            self._message_queue.put_nowait(message)
        except queue.Full:
            log.warning("Slack message queue full, dropping notification")

    def _should_throttle_error(self, error_key: str) -> bool:
        """Check if an error notification should be throttled."""
        with self._lock:
            now = time.time()
            last_time = self._last_error_time.get(error_key, 0)
            if now - last_time < self.ERROR_THROTTLE_SECONDS:
                return True
            self._last_error_time[error_key] = now
            return False

    def _image_to_png_bytes(self, image: np.ndarray) -> bytes:
        """Convert a numpy array to PNG bytes."""
        try:
            from PIL import Image

            # Ensure image is in correct format
            if image.dtype == np.uint16:
                # Scale to 8-bit
                image = (image / 256).astype(np.uint8)
            elif image.dtype != np.uint8:
                image = image.astype(np.uint8)

            # Resize if too large
            h, w = image.shape[:2]
            if max(h, w) > self.MAX_IMAGE_SIZE:
                scale = self.MAX_IMAGE_SIZE / max(h, w)
                new_w = int(w * scale)
                new_h = int(h * scale)
                pil_image = Image.fromarray(image)
                pil_image = pil_image.resize((new_w, new_h), Image.LANCZOS)
            else:
                pil_image = Image.fromarray(image)

            # Convert to PNG bytes
            buffer = io.BytesIO()
            pil_image.save(buffer, format="PNG")
            return buffer.getvalue()
        except ImportError:
            log.warning("PIL not available, cannot convert image to PNG")
            return b""
        except Exception as e:
            log.warning(f"Failed to convert image to PNG: {e}")
            return b""

    def _format_duration(self, seconds: float) -> str:
        """Format a duration in seconds to a human-readable string."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.0f}m"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    def set_pending_image(self, image: Optional[np.ndarray]):
        """Set a pending image to be sent with the next timepoint notification.

        This should be called from the main Qt thread after capturing a screenshot.

        Args:
            image: Screenshot as numpy array, or None to clear.
        """
        with self._pending_image_lock:
            if image is not None:
                self._pending_image = self._image_to_png_bytes(image)
            else:
                self._pending_image = None

    def get_and_clear_pending_image(self) -> Optional[bytes]:
        """Get and clear the pending image data."""
        with self._pending_image_lock:
            image = self._pending_image
            self._pending_image = None
            return image

    def send_message(self, text: str, blocks: Optional[list] = None):
        """Send a text message to Slack.

        Args:
            text: Fallback text for notifications.
            blocks: Optional Slack Block Kit blocks for rich formatting.
        """
        message = SlackMessage(text=text, blocks=blocks)
        self._queue_message(message)

    def _format_context(self, context: Optional[Dict[str, Any]]) -> str:
        """Format acquisition context into a readable string."""
        if not context:
            return "N/A"

        field_formats = [
            ("region", "Region {}"),
            ("fov", "FOV {}"),
            ("z_level", "Z-level {}"),
            ("channel", "Channel: {}"),
        ]
        parts = [fmt.format(context[key]) for key, fmt in field_formats if key in context]
        if "job_id" in context:
            parts.append(f"Job: {context['job_id'][:8]}")
        return ", ".join(parts) if parts else "N/A"

    def _make_experiment_context_block(self) -> Optional[Dict[str, Any]]:
        """Create a context block with experiment ID if available."""
        if not self._current_experiment_id:
            return None
        return {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Experiment: {self._current_experiment_id}"}],
        }

    def notify_error(self, error_msg: str, context: Optional[Dict[str, Any]] = None):
        """Send an error notification to Slack."""
        if not control._def.SlackNotifications.NOTIFY_ON_ERROR:
            return

        error_key = error_msg[:50]
        if self._should_throttle_error(error_key):
            log.debug(f"Throttling Slack error notification: {error_key}")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        context_str = self._format_context(context)

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Acquisition Error", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Error:* {error_msg}\n*Location:* {context_str}\n*Time:* {timestamp}",
                },
            },
        ]

        experiment_block = self._make_experiment_context_block()
        if experiment_block:
            blocks.append(experiment_block)

        message = SlackMessage(text=f"Acquisition Error: {error_msg}", blocks=blocks)
        self._queue_message(message)

    def notify_timepoint_complete(
        self,
        stats: TimepointStats,
        mosaic_image: Optional[np.ndarray] = None,
    ):
        """Send a timepoint completion notification to Slack."""
        if not control._def.SlackNotifications.NOTIFY_ON_TIMEPOINT_COMPLETE:
            return

        # Convert image to PNG if provided
        image_data = None
        if mosaic_image is not None:
            image_data = self._image_to_png_bytes(mosaic_image)
        else:
            # Check for pending image (set from main thread)
            image_data = self.get_and_clear_pending_image()

        # Format times
        elapsed_str = self._format_duration(stats.elapsed_seconds)
        remaining_str = self._format_duration(stats.estimated_remaining_seconds)

        # Build stats text
        stats_text = (
            f"*Elapsed:* {elapsed_str}\n"
            f"*Remaining:* ~{remaining_str}\n"
            f"*Images:* {stats.images_captured:,}\n"
            f"*FOVs:* {stats.fovs_captured:,}"
        )

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Timepoint {stats.timepoint}/{stats.total_timepoints} Complete",
                    "emoji": True,
                },
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": stats_text}},
        ]

        # Add laser AF stats if applicable
        total_af = stats.laser_af_successes + stats.laser_af_failures
        if total_af > 0:
            af_text = f"*Laser AF:* {stats.laser_af_successes}/{total_af} FOVs succeeded"
            if stats.laser_af_failures > 0:
                af_text += f" ({stats.laser_af_failures} failed)"
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": af_text}})

        experiment_block = self._make_experiment_context_block()
        if experiment_block:
            blocks.append(experiment_block)

        message = SlackMessage(
            text=f"Timepoint {stats.timepoint}/{stats.total_timepoints} complete",
            blocks=blocks,
            image_data=image_data,
            image_title=f"Mosaic - Timepoint {stats.timepoint}",
        )
        self._queue_message(message)

    def notify_acquisition_start(
        self,
        experiment_id: str,
        num_regions: int,
        num_timepoints: int,
        num_channels: int,
        num_z_levels: int,
    ):
        """Send an acquisition start notification to Slack."""
        # Always initialize acquisition tracking state, even if the start
        # notification itself is disabled. This ensures subsequent error and
        # timepoint notifications have the correct experiment context.
        self._acquisition_start_time = time.time()
        self._current_experiment_id = experiment_id
        with self._lock:
            self._timepoint_durations = []

        if not control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_START:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Acquisition Started", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Experiment:* {experiment_id}\n"
                        f"*Regions:* {num_regions}\n"
                        f"*Timepoints:* {num_timepoints}\n"
                        f"*Channels:* {num_channels}\n"
                        f"*Z-levels:* {num_z_levels}\n"
                        f"*Started:* {timestamp}"
                    ),
                },
            },
        ]

        message = SlackMessage(text=f"Acquisition started: {experiment_id}", blocks=blocks)
        self._queue_message(message)

    def notify_acquisition_finished(self, stats: AcquisitionStats):
        """Send an acquisition completion notification to Slack."""
        if not control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_FINISHED:
            return

        duration_str = self._format_duration(stats.total_duration_seconds)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        status_text = f"with {stats.errors_encountered} errors" if stats.errors_encountered > 0 else "successfully"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Acquisition Complete", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Experiment:* {stats.experiment_id}\n"
                        f"*Status:* Finished {status_text}\n"
                        f"*Total Images:* {stats.total_images:,}\n"
                        f"*Timepoints:* {stats.total_timepoints}\n"
                        f"*Duration:* {duration_str}\n"
                        f"*Completed:* {timestamp}"
                    ),
                },
            },
        ]

        message = SlackMessage(
            text=f"Acquisition complete: {stats.experiment_id} ({status_text})",
            blocks=blocks,
        )
        self._queue_message(message)

        # Reset state
        self._acquisition_start_time = None
        self._current_experiment_id = None
        with self._lock:
            self._timepoint_durations = []

    def record_timepoint_duration(self, duration_seconds: float):
        """Record the duration of a completed timepoint for estimation."""
        with self._lock:
            self._timepoint_durations.append(duration_seconds)

    def estimate_remaining_time(self, current_timepoint: int, total_timepoints: int) -> float:
        """Estimate remaining acquisition time based on past timepoint durations."""
        with self._lock:
            if not self._timepoint_durations:
                return 0.0
            avg_duration = sum(self._timepoint_durations) / len(self._timepoint_durations)
            remaining_timepoints = total_timepoints - current_timepoint
            return avg_duration * remaining_timepoints

    def test_connection(self) -> Tuple[bool, str]:
        """Test the Slack API connection.

        Returns:
            Tuple of (success, message).
        """
        token = self.bot_token
        channel = self.channel_id

        if not token:
            return False, "No bot token configured"
        if not channel:
            return False, "No channel ID configured"

        # Test by posting a message
        success, _ = self._post_message(
            "Squid Microscope: Test connection successful!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Squid Microscope Slack integration test successful!",
                    },
                }
            ],
        )

        if success:
            return True, "Connection successful"
        return False, "Failed to send test message"

    def close(self):
        """Shutdown the notifier and worker thread."""
        self._stop_event.set()
        try:
            self._message_queue.put_nowait(None)
        except queue.Full:
            # Worker will still stop due to _stop_event being set
            log.debug("SlackNotifier.close: queue full while enqueuing shutdown sentinel")

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
