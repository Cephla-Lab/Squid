import numpy as np


class ContrastManager:
    def __init__(self):
        self.contrast_limits = {}
        self.acquisition_dtype = None

    def update_limits(self, channel, min_val, max_val):
        self.contrast_limits[channel] = (min_val, max_val)

    def get_limits(self, channel, dtype=None):
        if dtype is not None:
            if self.acquisition_dtype is None:
                self.acquisition_dtype = dtype
            elif self.acquisition_dtype != dtype:
                self.scale_contrast_limits(dtype)
        return self.contrast_limits.get(channel, self.get_default_limits())

    def get_default_limits(self):
        return self.default_limits_for_dtype(self.acquisition_dtype)

    @staticmethod
    def default_limits_for_dtype(dtype):
        """Full display range of one dtype, independent of the run-wide acquisition_dtype."""
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
        """Limits for one channel, defaulting to the full range of ITS OWN dtype.

        get_limits() falls back to get_default_limits(), which is derived from the single
        run-wide acquisition_dtype - i.e. from whichever camera delivered the first frame.
        On a dual-camera run that is the wrong range for the other camera's channels: a
        uint8 RGB layer handed uint16 limits renders essentially black, and a uint16 layer
        handed uint8 limits renders saturated white. A limit the user has actually set for
        the channel still wins.
        """
        if channel in self.contrast_limits:
            return self.contrast_limits[channel]
        return self.default_limits_for_dtype(dtype)

    def get_scaled_limits(self, channel, target_dtype):
        min_val, max_val = self.get_limits(channel)
        if self.acquisition_dtype == target_dtype:
            return min_val, max_val

        source_info = np.iinfo(self.acquisition_dtype)
        target_info = np.iinfo(target_dtype)

        scaled_min = (min_val - source_info.min) / (source_info.max - source_info.min) * (
            target_info.max - target_info.min
        ) + target_info.min
        scaled_max = (max_val - source_info.min) / (source_info.max - source_info.min) * (
            target_info.max - target_info.min
        ) + target_info.min

        return scaled_min, scaled_max

    def scale_contrast_limits(self, target_dtype):
        print(f"{self.acquisition_dtype} -> {target_dtype}")
        for channel in self.contrast_limits.keys():
            self.contrast_limits[channel] = self.get_scaled_limits(channel, target_dtype)

        self.acquisition_dtype = target_dtype
