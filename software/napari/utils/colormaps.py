class Colormap:
    def __init__(self, colors=None, controls=None, name=None) -> None:
        self.colors = list(colors) if colors is not None else []
        self.controls = list(controls) if controls is not None else []
        self.name = name or "stub"


AVAILABLE_COLORMAPS = {}


__all__ = ["Colormap", "AVAILABLE_COLORMAPS"]
