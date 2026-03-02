"""
Confocal model registry.

Maps confocal unit model names to their supported properties.
Different confocal units have different capabilities (e.g. XLight V3
has illumination and emission irises, while V2/Cicero have none).
"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class ConfocalModelDef:
    """Declares what confocal properties a model supports.

    objective_properties: field names → defaults for per-objective settings (iris, etc.)
    public_properties: reserved for general.yaml settings (dichroic_position, etc.) — deferred
    """

    objective_properties: Dict[str, float] = field(default_factory=dict)
    public_properties: Dict[str, Any] = field(default_factory=dict)


CONFOCAL_MODELS: Dict[str, ConfocalModelDef] = {
    "xlight_v3": ConfocalModelDef(
        objective_properties={"illumination_iris": 100.0, "emission_iris": 100.0},
    ),
    "xlight_v2": ConfocalModelDef(
        objective_properties={},
    ),
    "cicero": ConfocalModelDef(
        objective_properties={},
    ),
    "dragonfly": ConfocalModelDef(
        objective_properties={},
    ),
}
