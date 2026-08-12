import logging

import numpy as np

logger = logging.getLogger(__name__)


class ContrastManager:
    """User contrast limits per channel, each remembered in its own dtype.

    A channel's dtype is its camera's dtype: every channel on one camera shares it, and it
    only changes when that camera's pixel format changes. So limits are stored per channel
    together with the dtype they were chosen in, and a channel is converted only when its own
    dtype changes - lazily, the next time it is displayed.

    The previous model kept a single run-wide acquisition_dtype and rescaled EVERY channel's
    stored limits whenever a frame of a different dtype arrived. On a dual-camera run that
    fires on every camera switch, so a contrast setting made on the mono camera's channel was
    rewritten into the colour camera's 0-255 range (and vice versa) purely because the other
    camera delivered a frame.
    """

    def __init__(self):
        self.contrast_limits = {}
        # dtype each channel's stored limits are expressed in.
        self.limit_dtypes = {}
        # Most recently seen dtype. Kept because callers use it as "has any frame arrived
        # yet"; it is deliberately NOT used to reinterpret another channel's limits.
        self.acquisition_dtype = None

    def update_limits(self, channel, min_val, max_val, dtype=None):
        self.contrast_limits[channel] = (min_val, max_val)
        if dtype is not None:
            self.limit_dtypes[channel] = np.dtype(dtype)

    def get_limits(self, channel, dtype=None):
        if dtype is None:
            return self.contrast_limits.get(channel, self.get_default_limits())
        return self.get_limits_for_dtype(channel, dtype)

    def get_default_limits(self):
        return self.default_limits_for_dtype(self.acquisition_dtype)

    @staticmethod
    def default_limits_for_dtype(dtype):
        """Full display range of one dtype, independent of any other channel's dtype."""
        if dtype is None:
            return (0, 1)
        elif np.issubdtype(dtype, np.integer):
            info = np.iinfo(dtype)
            return (info.min, info.max)
        elif np.issubdtype(dtype, np.floating):
            return (0.0, 1.0)
        else:
            return (0, 1)

    def get_limits_for_dtype(self, channel, dtype):
        """This channel's limits, expressed in `dtype`.

        Defaults to the full range of `dtype` for a channel the user has never adjusted. A
        channel that HAS been adjusted keeps that choice, converted if (and only if) its own
        dtype changed - e.g. its camera was switched from MONO8 to MONO16.
        """
        dtype = np.dtype(dtype)
        if self.acquisition_dtype is None:
            self.acquisition_dtype = dtype

        if channel not in self.contrast_limits:
            return self.default_limits_for_dtype(dtype)

        stored_dtype = self.limit_dtypes.get(channel)
        if stored_dtype is None:
            # Limits recorded before this channel's dtype was known (legacy callers of
            # update_limits). Adopt the dtype rather than rescaling from a guess.
            self.limit_dtypes[channel] = dtype
            return self.contrast_limits[channel]

        if stored_dtype == dtype:
            return self.contrast_limits[channel]

        converted = self._rescale(self.contrast_limits[channel], stored_dtype, dtype)
        logger.debug(f"Converting {channel!r} contrast limits {stored_dtype} -> {dtype}: {converted}")
        self.contrast_limits[channel] = converted
        self.limit_dtypes[channel] = dtype
        return converted

    @staticmethod
    def _rescale(limits, source_dtype, target_dtype):
        """Map limits from one integer dtype's range onto another's."""
        if not (np.issubdtype(source_dtype, np.integer) and np.issubdtype(target_dtype, np.integer)):
            # Nothing meaningful to scale between (e.g. a float dtype); keep the values.
            return limits
        min_val, max_val = limits
        source_info = np.iinfo(source_dtype)
        target_info = np.iinfo(target_dtype)
        span = source_info.max - source_info.min
        target_span = target_info.max - target_info.min

        def convert(value):
            return (value - source_info.min) / span * target_span + target_info.min

        return (convert(min_val), convert(max_val))

    def get_scaled_limits(self, channel, target_dtype):
        """This channel's limits in target_dtype, without recording the conversion.

        Used by views that render a channel at a different depth than it was acquired in
        (the mosaic downsamples to its own dtype), so the channel's own record must not be
        rewritten to the view's dtype.
        """
        target_dtype = np.dtype(target_dtype)
        limits = self.contrast_limits.get(channel)
        if limits is None:
            return self.default_limits_for_dtype(target_dtype)
        source_dtype = self.limit_dtypes.get(channel, self.acquisition_dtype)
        if source_dtype is None or np.dtype(source_dtype) == target_dtype:
            return limits
        return self._rescale(limits, np.dtype(source_dtype), target_dtype)

    def scale_contrast_limits(self, target_dtype):
        """Record that frames of target_dtype are now arriving.

        No longer rewrites every channel's stored limits: each channel is converted from its
        own dtype on read (see get_limits_for_dtype), so a camera switch cannot reinterpret
        another camera's channels. Callers that announce a dtype change can keep calling this.
        """
        self.acquisition_dtype = np.dtype(target_dtype)
