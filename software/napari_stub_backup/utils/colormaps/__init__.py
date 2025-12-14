class _AvailableColormaps:
    """Container stub with a marker attribute to satisfy hasattr checks."""

    def __init__(self):
        self.grayclip = True


class Colormap:
    """Colormap stub."""

    def __init__(self, *args, **kwargs):
        pass


AVAILABLE_COLORMAPS = _AvailableColormaps()

__all__ = ["AVAILABLE_COLORMAPS", "Colormap"]
