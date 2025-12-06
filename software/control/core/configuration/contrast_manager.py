from typing import Dict, Optional, Tuple, Union
import numpy as np
import numpy.typing as npt


class ContrastManager:
    contrast_limits: Dict[str, Tuple[Union[int, float], Union[int, float]]]
    acquisition_dtype: Optional[npt.DTypeLike]

    def __init__(self) -> None:
        self.contrast_limits = {}
        self.acquisition_dtype = None

    def update_limits(self, channel: str, min_val: Union[int, float], max_val: Union[int, float]) -> None:
        self.contrast_limits[channel] = (min_val, max_val)

    def get_limits(
        self, channel: str, dtype: Optional[npt.DTypeLike] = None
    ) -> Tuple[Union[int, float], Union[int, float]]:
        if dtype is not None:
            if self.acquisition_dtype is None:
                self.acquisition_dtype = dtype
            elif self.acquisition_dtype != dtype:
                self.scale_contrast_limits(dtype)
        return self.contrast_limits.get(channel, self.get_default_limits())

    def get_default_limits(self) -> Tuple[Union[int, float], Union[int, float]]:
        if self.acquisition_dtype is None:
            return (0, 1)
        elif np.issubdtype(self.acquisition_dtype, np.integer):
            info = np.iinfo(self.acquisition_dtype)
            return (info.min, info.max)
        elif np.issubdtype(self.acquisition_dtype, np.floating):
            return (0.0, 1.0)
        else:
            return (0, 1)

    def get_scaled_limits(
        self, channel: str, target_dtype: npt.DTypeLike
    ) -> Tuple[Union[int, float], Union[int, float]]:
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

    def scale_contrast_limits(self, target_dtype: npt.DTypeLike) -> None:
        print(f"{self.acquisition_dtype} -> {target_dtype}")
        for channel in self.contrast_limits.keys():
            self.contrast_limits[channel] = self.get_scaled_limits(channel, target_dtype)

        self.acquisition_dtype = target_dtype
