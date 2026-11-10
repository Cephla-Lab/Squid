"""The analyzer contract and registry.

An analyzer looks at one field of view and says where the interesting things
are, in *pixel* coordinates. It never converts to stage coordinates -- that is
:mod:`control.core.smart_acquisition.geometry`'s job -- which keeps analyzers
free of coordinate fixtures and makes them trivially unit-testable.
"""

from typing import Any, Callable, Dict, List, Protocol, Tuple, runtime_checkable

import numpy as np

from ..targets import FovRecord, PixelDetection


@runtime_checkable
class Analyzer(Protocol):
    """What an analysis plugin must provide."""

    name: str

    def configure(self, params: Dict[str, Any]) -> None:
        """Apply user parameters. Should raise ValueError on unknown/invalid keys."""

    def analyze_fov(self, image: np.ndarray, fov: FovRecord) -> List[PixelDetection]:
        """Return detections in pixel coordinates of ``image``."""


class BaseAnalyzer:
    """Convenience base: declares defaults, validates and stores params."""

    name: str = "base"
    #: parameter name -> default value. Also the accepted-key whitelist.
    defaults: Dict[str, Any] = {}
    #: The few parameters a user normally touches. The GUI shows these up front
    #: and files the rest under "Advanced"; an empty tuple means "all of them".
    primary: Tuple[str, ...] = ()

    def __init__(self, params: Dict[str, Any] = None):
        self.params: Dict[str, Any] = dict(self.defaults)
        if params:
            self.configure(params)

    def configure(self, params: Dict[str, Any]) -> None:
        unknown = sorted(set(params) - set(self.defaults))
        if unknown:
            raise ValueError(f"{self.name}: unknown parameter(s) {unknown}. Known: {sorted(self.defaults)}")
        self.params.update(params)

    def analyze_fov(self, image: np.ndarray, fov: FovRecord) -> List[PixelDetection]:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in sorted(self.params.items()))})"


_REGISTRY: Dict[str, Callable[..., Any]] = {}


def register(factory: Callable[..., Any], name: str = None) -> Callable[..., Any]:
    """Register an analyzer class under its ``name``."""
    key = name or getattr(factory, "name", None)
    if not key:
        raise ValueError("Analyzer must define a 'name'")
    if key in _REGISTRY and _REGISTRY[key] is not factory:
        raise ValueError(f"Analyzer '{key}' is already registered")
    _REGISTRY[key] = factory
    return factory


def available() -> List[str]:
    return sorted(_REGISTRY)


def create(name: str, params: Dict[str, Any] = None):
    if name not in _REGISTRY:
        raise ValueError(f"Unknown analyzer '{name}'. Available: {available()}")
    return _REGISTRY[name](params or {})


def defaults_for(name: str) -> Dict[str, Any]:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown analyzer '{name}'. Available: {available()}")
    return dict(getattr(_REGISTRY[name], "defaults", {}))


def primary_params_for(name: str) -> List[str]:
    """The parameters worth showing up front, in declaration order.

    Falls back to every parameter when an analyzer does not single any out.
    """
    if name not in _REGISTRY:
        raise ValueError(f"Unknown analyzer '{name}'. Available: {available()}")
    factory = _REGISTRY[name]
    declared = getattr(factory, "primary", ()) or ()
    known = getattr(factory, "defaults", {})
    chosen = [key for key in declared if key in known]
    return chosen or sorted(known)
