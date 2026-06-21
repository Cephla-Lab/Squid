import math
from dataclasses import dataclass, field
from typing import List, Optional

from control.models.acquisition_config import AcquisitionChannel


def frame_count(fps: float, duration_s: float) -> int:
    return int(round(fps * duration_s))


def zstack_plane_count(z_min_um: float, z_max_um: float, step_um: float) -> int:
    if step_um <= 0 or z_max_um < z_min_um:
        raise ValueError("require step>0 and z_max>=z_min")
    # epsilon absorbs float representation error so e.g. 6.0/1.0 -> 5.999... still floors to 6
    return int(math.floor((z_max_um - z_min_um) / step_um + 1e-9)) + 1


def zstack_offsets_um(z_min_um: float, z_max_um: float, step_um: float) -> List[float]:
    return [round(z_min_um + i * step_um, 6) for i in range(zstack_plane_count(z_min_um, z_max_um, step_um))]


@dataclass
class RecordZStackAcquisitionParameters:
    base_path: str
    experiment_id: str
    Nt: int = 1
    dt_s: float = 0.0
    use_laser_af: bool = False
    # recording phase
    recording_enabled: bool = False
    recording_channel: Optional[AcquisitionChannel] = None
    fps: float = 10.0
    duration_s: float = 1.0
    recording_z_offset_um: float = 0.0
    # z-stack phase
    zstack_enabled: bool = False
    zstack_channels: List[AcquisitionChannel] = field(default_factory=list)
    z_min_um: float = -3.0
    z_max_um: float = 3.0
    z_step_um: float = 1.0
