# Display widgets package
from squid.ui.widgets.display.stats import StatsDisplayWidget
from squid.ui.widgets.display.focus_map import FocusMapWidget
from squid.ui.widgets.display.napari_live import NapariLiveWidget
from squid.ui.widgets.display.napari_multichannel import NapariMultiChannelWidget
from squid.ui.widgets.display.napari_mosaic import NapariMosaicDisplayWidget
from squid.ui.widgets.display.plotting import (
    WaveformDisplay,
    PlotWidget,
    SurfacePlotWidget,
)

__all__ = [
    "StatsDisplayWidget",
    "FocusMapWidget",
    "NapariLiveWidget",
    "NapariMultiChannelWidget",
    "NapariMosaicDisplayWidget",
    "WaveformDisplay",
    "PlotWidget",
    "SurfacePlotWidget",
]
