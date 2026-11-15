"""Fake PySpin and a fake GenICam nodemap for testing control.camera_flir without the Spinnaker SDK.

Importing this module installs the fake ``PySpin`` in ``sys.modules`` and then imports the driver, so a
test module imports it BEFORE ``control.camera_flir``. The fake nodemap records every write the driver
makes, which is how the tests pin that a driver with every opt-in off issues exactly today's writes.
"""

import sys
import threading
import types
from typing import Dict, List, Optional, Sequence, Tuple
from unittest import mock

import squid.logging


def _identity(node):
    return node


def install_fake_pyspin() -> types.ModuleType:
    """Put a fake ``PySpin`` module in sys.modules (always, so the tests never depend on the real SDK)."""
    existing = sys.modules.get("PySpin")
    if existing is not None and getattr(existing, "_squid_fake", False):
        return existing

    fake = types.ModuleType("PySpin")
    fake._squid_fake = True
    for cast in (
        "CEnumerationPtr",
        "CEnumEntryPtr",
        "CFloatPtr",
        "CBooleanPtr",
        "CIntegerPtr",
        "CCommandPtr",
        "CStringPtr",
        "CValuePtr",
        "CCategoryPtr",
    ):
        setattr(fake, cast, _identity)
    fake.IsReadable = lambda node: getattr(node, "readable", False)
    fake.IsWritable = lambda node: getattr(node, "writable", False)
    fake.IsImplemented = lambda node: getattr(node, "readable", False)

    class SpinnakerException(Exception):
        pass

    class ImageEventHandler:
        def __init__(self, *args, **kwargs):
            pass

    class ImageProcessor:
        def SetColorProcessing(self, *args):
            pass

        def Convert(self, *args):
            raise SpinnakerException("fake ImageProcessor cannot convert")

    fake.SpinnakerException = SpinnakerException
    fake.ImageEventHandler = ImageEventHandler
    fake.ImageProcessor = ImageProcessor
    fake.PixelFormat_Mono8 = 1
    fake.PixelFormat_Mono12 = 2
    fake.PixelFormat_Mono16 = 3
    fake.PixelFormat_RGB8 = 4
    fake.PixelFormat_RGB16 = 5
    fake.PixelFormat_BayerRG8 = 6
    fake.PixelFormat_BayerRG12 = 7
    fake.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR = 0
    fake.intfICategory = 10
    fake.intfIInteger = 11
    fake.intfIBoolean = 12
    fake.intfICommand = 13
    fake.intfIFloat = 14
    fake.intfIString = 15
    fake.intfIEnumeration = 16
    fake.System = mock.MagicMock()
    sys.modules["PySpin"] = fake
    return fake


PySpin = install_fake_pyspin()

import control.camera_flir as camera_flir  # noqa: E402  (needs the fake PySpin above)


class FakeEntry:
    def __init__(self, name: str, value: int, readable: bool = True):
        self.name = name
        self.value = value
        self.readable = readable

    def GetValue(self) -> int:
        return self.value

    def GetSymbolic(self) -> str:
        return self.name

    def GetName(self) -> str:
        return self.name


class FakeEnumNode:
    """A GenICam enumeration node; entry values are the entry's index in ``entries``."""

    def __init__(
        self,
        name: str,
        entries: Sequence[str],
        current: str,
        *,
        writable: bool = True,
        readable: bool = True,
        readback: Optional[str] = None,
    ):
        self.name = name
        self.entries = list(entries)
        self.current = current
        self.writable = writable
        self.readable = readable
        self.readback = readback  # when set, what GetCurrentEntry reports regardless of the last write
        self.nodemap: Optional["FakeNodemap"] = None

    def GetPrincipalInterfaceType(self) -> int:
        return PySpin.intfIEnumeration

    def GetEntryByName(self, name: str) -> FakeEntry:
        if name in self.entries:
            return FakeEntry(name, self.entries.index(name))
        return FakeEntry(name, -1, readable=False)

    def SetIntValue(self, value: int) -> None:
        symbolic = self.entries[value]
        self.nodemap.writes.append((self.name, symbolic))
        self.current = symbolic

    def GetIntValue(self) -> int:
        return self.entries.index(self.readback if self.readback is not None else self.current)

    def GetCurrentEntry(self) -> FakeEntry:
        shown = self.readback if self.readback is not None else self.current
        return FakeEntry(shown, self.entries.index(shown))


class FakeBoolNode:
    def __init__(self, name: str, value: bool, *, writable: bool = True, readable: bool = True):
        self.name = name
        self.value = value
        self.writable = writable
        self.readable = readable
        self.nodemap: Optional["FakeNodemap"] = None

    def GetPrincipalInterfaceType(self) -> int:
        return PySpin.intfIBoolean

    def SetValue(self, value: bool) -> None:
        self.nodemap.writes.append((self.name, value))
        self.value = value

    def GetValue(self) -> bool:
        return self.value


class FakeFloatNode:
    def __init__(self, name: str, value: float, *, writable: bool = True, readable: bool = True):
        self.name = name
        self.value = value
        self.writable = writable
        self.readable = readable
        self.nodemap: Optional["FakeNodemap"] = None

    def GetPrincipalInterfaceType(self) -> int:
        return PySpin.intfIFloat

    def SetValue(self, value: float) -> None:
        self.nodemap.writes.append((self.name, float(value)))
        self.value = float(value)

    def GetValue(self) -> float:
        return self.value


class MissingNode:
    """What GetNode returns for a node this camera model does not have: neither readable nor writable."""

    readable = False
    writable = False

    def __init__(self, name: str):
        self.name = name

    def GetEntryByName(self, name: str) -> FakeEntry:
        return FakeEntry(name, -1, readable=False)


class FakeNodemap:
    def __init__(self):
        self._nodes: Dict[str, object] = {}
        self.writes: List[Tuple[str, object]] = []
        self.lookups: List[str] = []

    def add(self, node) -> None:
        """Add a node, replacing one of the same name."""
        node.nodemap = self
        self._nodes[node.name] = node

    def remove(self, name: str) -> None:
        del self._nodes[name]

    def node(self, name: str):
        return self._nodes[name]  # KeyError if absent: the test asked for a node the fake does not have

    def GetNode(self, name: str):
        self.lookups.append(name)
        return self._nodes.get(name) or MissingNode(name)


def bfs_u3_63s4_nodemap() -> FakeNodemap:
    """The nodes a BFS-U3-63S4M exposes that the driver touches, at their power-on values."""
    nodemap = FakeNodemap()
    for node in (
        FakeEnumNode("TriggerMode", ["Off", "On"], "Off"),
        FakeEnumNode("TriggerSelector", ["FrameStart", "AcquisitionStart"], "FrameStart"),
        FakeEnumNode("TriggerSource", ["Line0", "Line1", "Line2", "Line3", "Software"], "Line0"),
        FakeEnumNode(
            "TriggerActivation", ["LevelLow", "LevelHigh", "FallingEdge", "RisingEdge", "AnyEdge"], "RisingEdge"
        ),
        FakeEnumNode("TriggerOverlap", ["Off", "ReadOut"], "Off"),
        FakeEnumNode("ExposureMode", ["Timed", "TriggerWidth"], "Timed"),
        FakeEnumNode("ExposureAuto", ["Off", "Once", "Continuous"], "Off"),
        FakeFloatNode("ExposureTime", 20000.0),
        FakeEnumNode("SensorShutterMode", ["Rolling", "GlobalReset"], "Rolling"),
        FakeEnumNode("LineSelector", ["Line0", "Line1", "Line2", "Line3"], "Line0"),
        FakeEnumNode("LineMode", ["Input", "Output"], "Input"),
        FakeEnumNode(
            "LineSource", ["Off", "ExposureActive", "FrameTriggerWait", "UserOutput0", "AllPixel", "AnyPixel"], "Off"
        ),
        FakeBoolNode("LineInverter", False),
        FakeBoolNode("V3_3Enable", False),
        FakeBoolNode("AcquisitionFrameRateEnable", True),
        FakeEnumNode("AdcBitDepth", ["Bit10", "Bit12", "Bit14"], "Bit14"),
        FakeEnumNode("PixelFormat", ["Mono8", "Mono10", "Mono10p", "Mono12", "Mono12p", "Mono16"], "Mono16"),
    ):
        nodemap.add(node)
    return nodemap


class FakeCamera:
    def __init__(self, nodemap: FakeNodemap):
        self._nodemap = nodemap
        self.deinit_calls = 0

    def GetNodeMap(self) -> FakeNodemap:
        return self._nodemap

    def IsStreaming(self) -> bool:
        return False

    def Init(self) -> None:
        pass

    def DeInit(self) -> None:
        self.deinit_calls += 1


class FakeCameraList:
    def __init__(self, camera: FakeCamera):
        self._camera = camera

    def Clear(self) -> None:
        pass

    def GetSize(self) -> int:
        return 1

    def GetByIndex(self, index: int) -> FakeCamera:
        return self._camera

    def GetBySerial(self, serial: str) -> FakeCamera:
        return self._camera


# What today's set_exposure_time(20) writes to ExposureTime under hardware trigger with make_flir's attributes:
# exposure_delay_us + exposure + row_period_us * pixel_size_byte * (row_numbers - 1) + 500.
ROLLING_EXPOSURE_US_AT_20MS = 1300 + 20_000 + 10 * 2 * 3035 + 500


def make_flir(nodemap: FakeNodemap, trigger_mode=None, exposure_ms: float = 20.0) -> camera_flir.FLIRCamera:
    """A FLIRCamera with exactly the attributes the tested paths read, and no SDK.

    Built with object.__new__ so nothing in __init__ runs; a missing attribute is an AttributeError in the
    test, not a silently different camera (the shape of #654's _make_toupcam).
    """
    cam = object.__new__(camera_flir.FLIRCamera)
    cam.camera = FakeCamera(nodemap)
    # The driver's __del__ tears these down; without them every collected test object logs an AttributeError.
    cam.camera_list = FakeCameraList(cam.camera)
    cam.py_spin_system = mock.MagicMock()
    cam.nodemap = nodemap
    cam._log = squid.logging.get_logger("test_camera_flir")
    cam._trigger_lock = threading.RLock()
    cam.is_streaming = False
    cam.callback_is_enabled = False
    cam.trigger_mode = trigger_mode
    cam.exposure_time = exposure_ms
    cam.is_global_shutter = False
    cam.pixel_size_byte = 2
    cam.row_period_us = 10
    cam.row_numbers = 3036
    cam.exposure_delay_us_8bit = 650
    cam.exposure_delay_us = 1300
    cam.strobe_delay_us = 1300 + 10 * 2 * 3035
    cam.auto_exposure_mode = None
    cam.pixel_format = "MONO16"
    cam.convert_pixel_format = False
    cam.conversion_pixel_format = PySpin.PixelFormat_Mono16
    cam._hw_trigger_fn = lambda t=None: True
    cam.strobe_pushes: List[float] = []
    cam._hw_set_strobe_delay_ms_fn = cam.strobe_pushes.append
    cam._trigger_sent = threading.Event()
    cam._last_trigger_timestamp = 0.0
    cam.frame_ID_offset_hardware_trigger = 0
    return cam
