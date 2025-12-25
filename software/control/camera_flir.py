import argparse
import cv2
import time
import numpy as np
import PySpin
from typing import Optional, Callable, Sequence, Tuple
import threading

from control._def import *
from squid.abc import (
    AbstractCamera,
    CameraError,
    CameraFrame,
    CameraFrameFormat,
    CameraGainRange,
    CameraAcquisitionMode,
)
from squid.config import CameraConfig, CameraPixelFormat, FLIRCameraModel, CameraSensor
import control._def as _def


class ReadType:
    """
    Use the following constants to determine whether nodes are read
    as Value nodes or their individual types.
    """

    VALUE = (0,)
    INDIVIDUAL = 1


try:
    if CHOSEN_READ == "VALUE":
        CHOSEN_READ = ReadType.VALUE
    else:
        CHOSEN_READ = ReadType.INDIVIDUAL
except:
    CHOSEN_READ = ReadType.INDIVIDUAL


def get_value_node(node):
    """
    Retrieves and prints the display name and value of all node types as value nodes.
    A value node is a general node type that allows for the reading and writing of any node type as a string.

    :param node: Node to get information from.
    :type node: INode
    :param level: Depth to indent output.
    :return: node name and value, both strings
    :rtype: (str (node name),str (node value)
    """
    try:
        # Create value node
        node_value = PySpin.CValuePtr(node)

        # Retrieve display name
        #
        # *** NOTES ***
        # A node's 'display name' is generally more appropriate for output and
        # user interaction whereas its 'name' is what the camera understands.
        # Generally, its name is the same as its display name but without
        # spaces - for instance, the name of the node that houses a camera's
        # serial number is 'DeviceSerialNumber' while its display name is
        # 'Device Serial Number'.
        name = node_value.GetName()

        # Retrieve value of any node type as string
        #
        # *** NOTES ***
        # Because value nodes return any node type as a string, it can be much
        # easier to deal with nodes as value nodes rather than their actual
        # individual types.
        value = node_value.ToString()
        return (name, value)
    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)
        return ("", None)


def get_string_node(node):
    """
    Retrieves the display name and value of a string node.

    :param node: Node to get information from.
    :type node: INode
    :return: Tuple of node name and value
    :rtype: (str,str)
    """
    try:
        # Create string node
        node_string = PySpin.CStringPtr(node)

        # Retrieve string node value
        #
        # *** NOTES ***
        # Functions in Spinnaker C++ that use gcstring types
        # are substituted with Python strings in PySpin.
        # The only exception is shown in the DeviceEvents example, where
        # the callback function still uses a wrapped gcstring type.
        name = node_string.GetName()

        # Ensure that the value length is not excessive for printing
        value = node_string.GetValue()

        # Print value; 'level' determines the indentation level of output
        return (name, value)

    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)
        return ("", None)


def get_integer_node(node):
    """
    Retrieves and prints the display name and value of an integer node.

    :param node: Node to get information from.
    :type node: INode
    :return: Tuple of node name and value
    :rtype: (str, int)
    """
    try:
        # Create integer node
        node_integer = PySpin.CIntegerPtr(node)

        # Get display name
        name = node_integer.GetName()

        # Retrieve integer node value
        #
        # *** NOTES ***
        # All node types except base nodes have a ToString()
        # method which returns a value as a string.
        value = node_integer.GetValue()

        # Print value
        return (name, value)

    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)
        return ("", None)


def get_float_node(node):
    """
    Retrieves the name and value of a float node.

    :param node: Node to get information from.
    :type node: INode
    :return: Tuple of node name and value
    :rtype: (str, float)
    """
    try:

        # Create float node
        node_float = PySpin.CFloatPtr(node)

        # Get display name
        name = node_float.GetName()

        # Retrieve float value
        value = node_float.GetValue()

        # Print value
        return (name, value)

    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)
        return ("", None)


def get_boolean_node(node):
    """
    Retrieves the display name and value of a Boolean node.

    :param node: Node to get information from.
    :type node: INode
    :return: Tuple of node name and value
    :rtype: (str, bool)
    """
    try:
        # Create Boolean node
        node_boolean = PySpin.CBooleanPtr(node)

        # Get display name
        name = node_boolean.GetName()

        # Retrieve Boolean value
        value = node_boolean.GetValue()

        # Print Boolean value
        # NOTE: In Python a Boolean will be printed as "True" or "False".
        return (name, value)

    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)
        return ("", None)


def get_command_node(node):
    """
    This function retrieves the name and tooltip of a command
    The tooltip is printed below because command nodes do not have an intelligible
    value.

    :param node: Node to get information from.
    :type node: INode
    :return: node name and tooltip as a tuple
    :rtype: (str, str)
    """
    try:
        result = True

        # Create command node
        node_command = PySpin.CCommandPtr(node)

        # Get display name
        name = node_command.GetName()

        # Retrieve tooltip
        #
        # *** NOTES ***
        # All node types have a tooltip available. Tooltips provide useful
        # information about nodes. Command nodes do not have a method to
        # retrieve values as their is no intelligible value to retrieve.
        tooltip = node_command.GetToolTip()

        # Ensure that the value length is not excessive for printing

        # Print display name and tooltip
        return (name, tooltip)

    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)
        return ("", None)


def get_enumeration_node_and_current_entry(node):
    """
    This function retrieves and prints the display names of an enumeration node
    and its current entry (which is actually housed in another node unto itself).

    :param node: Node to get information from.
    :type node: INode
    :return: name and symbolic of current entry in enumeration
    :rtype: (str,str)
    """
    try:
        # Create enumeration node
        node_enumeration = PySpin.CEnumerationPtr(node)

        # Retrieve current entry as enumeration node
        #
        # *** NOTES ***
        # Enumeration nodes have three methods to differentiate between: first,
        # GetIntValue() returns the integer value of the current entry node;
        # second, GetCurrentEntry() returns the entry node itself; and third,
        # ToString() returns the symbolic of the current entry.
        node_enum_entry = PySpin.CEnumEntryPtr(node_enumeration.GetCurrentEntry())

        # Get display name
        name = node_enumeration.GetName()

        # Retrieve current symbolic
        #
        # *** NOTES ***
        # Rather than retrieving the current entry node and then retrieving its
        # symbolic, this could have been taken care of in one step by using the
        # enumeration node's ToString() method.
        entry_symbolic = node_enum_entry.GetSymbolic()

        # Print current entry symbolic
        return (name, entry_symbolic)

    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)
        return ("", None)


def get_category_node_and_all_features(node):
    """
    This function retrieves and prints out the display name of a category node
    before printing all child nodes. Child nodes that are also category nodes
    are also retrieved recursively

    :param node: Category node to get information from.
    :type node: INode
    :return: Dictionary of category node features
    :rtype: dict
    """
    return_dict = {}
    try:
        # Create category node
        node_category = PySpin.CCategoryPtr(node)

        # Get and print display name
        # Retrieve and iterate through all children
        #
        # *** NOTES ***
        # The two nodes that typically have children are category nodes and
        # enumeration nodes. Throughout the examples, the children of category nodes
        # are referred to as features while the children of enumeration nodes are
        # referred to as entries. Keep in mind that enumeration nodes can be cast as
        # category nodes, but category nodes cannot be cast as enumerations.
        for node_feature in node_category.GetFeatures():

            # Ensure node is readable
            if not PySpin.IsReadable(node_feature):
                continue

            # Category nodes must be dealt with separately in order to retrieve subnodes recursively.
            if node_feature.GetPrincipalInterfaceType() == PySpin.intfICategory:
                return_dict[PySpin.CCategoryPtr(node_feature).GetName()] = get_category_node_and_all_features(
                    node_feature
                )

            # Cast all non-category nodes as value nodes
            #
            # *** NOTES ***
            # If dealing with a variety of node types and their values, it may be
            # simpler to cast them as value nodes rather than as their individual types.
            # However, with this increased ease-of-use, functionality is sacrificed.
            elif CHOSEN_READ == ReadType.VALUE:
                node_name, node_value = get_value_node(node_feature)
                return_dict[node_name] = node_value

            # Cast all non-category nodes as actual types
            elif CHOSEN_READ == ReadType.INDIVIDUAL:
                node_name = ""
                node_value = None
                if node_feature.GetPrincipalInterfaceType() == PySpin.intfIString:
                    node_name, node_value = get_string_node(node_feature)
                elif node_feature.GetPrincipalInterfaceType() == PySpin.intfIInteger:
                    node_name, node_value = get_integer_node(node_feature)
                elif node_feature.GetPrincipalInterfaceType() == PySpin.intfIFloat:
                    node_name, node_value = get_float_node(node_feature)
                elif node_feature.GetPrincipalInterfaceType() == PySpin.intfIBoolean:
                    node_name, node_value = get_boolean_node(node_feature)
                elif node_feature.GetPrincipalInterfaceType() == PySpin.intfICommand:
                    node_name, node_value = get_command_node(node_feature)
                elif node_feature.GetPrincipalInterfaceType() == PySpin.intfIEnumeration:
                    node_name, node_value = get_enumeration_node_and_current_entry(node_feature)
                return_dict[node_name] = node_value

    except PySpin.SpinnakerException as ex:
        print("Error: %s" % ex)

    return return_dict


def get_device_info(cam):
    nodemap_tldevice = cam.GetTLDeviceNodeMap()
    device_info_dict = {}
    device_info_dict["TLDevice"] = get_category_node_and_all_features(nodemap_tldevice.GetNode("Root"))
    return device_info_dict


def get_device_info_full(cam, get_genicam=False):
    device_info_dict = {}
    nodemap_gentl = cam.GetTLDeviceNodeMap()
    device_info_dict["TLDevice"] = get_category_node_and_all_features(nodemap_gentl.GetNode("Root"))

    nodemap_tlstream = cam.GetTLStreamNodeMap()
    device_info_dict["TLStream"] = get_category_node_and_all_features(nodemap_tlstream.GetNode("Root"))
    if get_genicam:
        cam.Init()

        nodemap_applayer = cam.GetNodeMap()
        device_info_dict["GenICam"] = get_category_node_and_all_features(nodemap_applayer.GetNode("Root"))

        cam.DeInit()
    return device_info_dict


def retrieve_all_camera_info(get_genicam=False):
    system = PySpin.System.GetInstance()
    cam_list = system.GetCameras()
    device_num = cam_list.GetSize()
    return_list = []
    if device_num > 0:
        for i, cam in enumerate(cam_list):
            return_list.append(get_device_info_full(cam, get_genicam=get_genicam))
        try:
            del cam
        except NameError:
            pass
    cam_list.Clear()
    system.ReleaseInstance()
    return return_list


def get_sn_by_model(model_name):
    system = PySpin.System.GetInstance()
    cam_list = system.GetCameras()
    device_num = cam_list.GetSize()
    sn_to_return = None
    if device_num > 0:
        for i, cam in enumerate(cam_list):
            device_info = get_device_info(cam)
            try:
                if device_info["TLDevice"]["DeviceInformation"]["DeviceModelName"] == model_name:
                    sn_to_return = device_info["TLDevice"]["DeviceInformation"]["DeviceSerialNumber"]
                    break
            except KeyError:
                pass
        try:
            del cam
        except NameError:
            pass
    cam_list.Clear()
    system.ReleaseInstance()
    return sn_to_return


class ImageEventHandler(PySpin.ImageEventHandler):
    def __init__(self, parent):
        super(ImageEventHandler, self).__init__()

        self.camera = parent  # Camera() type object

        self._processor = PySpin.ImageProcessor()
        self._processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)

    def OnImageEvent(self, raw_image):
        if raw_image.IsIncomplete():
            print("Image incomplete with image status %i ..." % raw_image.GetImageStatus())
            return
        elif self.camera.is_color and "mono" not in self.camera.pixel_format.lower():
            if (
                "10" in self.camera.pixel_format
                or "12" in self.camera.pixel_format
                or "14" in self.camera.pixel_format
                or "16" in self.camera.pixel_format
            ):
                rgb_image = self._processor.Convert(raw_image, PySpin.PixelFormat_RGB16)
            else:
                rgb_image = self._processor.Convert(raw_image, PySpin.PixelFormat_RGB8)
            numpy_image = rgb_image.GetNDArray()
        else:
            if self.camera.convert_pixel_format:
                converted_image = self._processor.Convert(raw_image, self.camera.conversion_pixel_format)
                numpy_image = converted_image.GetNDArray()
                if self.camera.conversion_pixel_format == PySpin.PixelFormat_Mono12:
                    numpy_image = numpy_image << 4
            else:
                try:
                    numpy_image = raw_image.GetNDArray()
                except PySpin.SpinnakerException:
                    converted_image = self.one_frame_post_processor.Convert(raw_image, PySpin.PixelFormat_Mono8)
                    numpy_image = converted_image.GetNDArray()
                if self.camera.pixel_format == "MONO12":
                    numpy_image = numpy_image << 4

        # Process frame and create CameraFrame object
        processed_frame = self.camera._process_raw_frame(numpy_image)

        with self.camera._frame_lock:
            self.camera.frame_ID_software = self.camera.frame_ID_software + 1
            self.camera.frame_ID = raw_image.GetFrameID()
            if self.camera.trigger_mode == TriggerMode.HARDWARE:
                if self.camera.frame_ID_offset_hardware_trigger == None:
                    self.camera.frame_ID_offset_hardware_trigger = self.camera.frame_ID
                self.camera.frame_ID = self.camera.frame_ID - self.camera.frame_ID_offset_hardware_trigger
            self.camera.timestamp = time.time()

            camera_frame = CameraFrame(
                frame_id=self.camera.frame_ID,
                timestamp=self.camera.timestamp,
                frame=processed_frame,
                frame_format=self.camera.get_frame_format(),
                frame_pixel_format=self.camera.get_pixel_format(),
            )
            self.camera.current_frame = camera_frame

        # Propagate to callbacks
        self.camera._propogate_frame(camera_frame)


class FLIRCamera(AbstractCamera):

    def __init__(
        self,
        camera_config: CameraConfig,
        hw_trigger_fn: Optional[Callable[[Optional[float]], bool]],
        hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]],
    ):
        super().__init__(camera_config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        # Map camera models to sensors
        model_to_sensor = {
            FLIRCameraModel.BFS_U3_63S4M_C: CameraSensor.IMX178,
        }

        # Set pixel size based on camera model/sensor
        if camera_config.camera_model and camera_config.camera_model in model_to_sensor:
            sensor = model_to_sensor[camera_config.camera_model]
            self.PIXEL_SIZE_UM = _def.CAMERA_PIXEL_SIZE_UM[sensor.value]
        else:
            self.PIXEL_SIZE_UM = _def.CAMERA_PIXEL_SIZE_UM[CameraSensor.IMX178.value]

        self._frame_lock = threading.Lock()
        self._last_trigger_timestamp = 0
        self._trigger_sent = threading.Event()
        self._trigger_sent.clear()

        self.py_spin_system = PySpin.System.GetInstance()
        self.camera_list = self.py_spin_system.GetCameras()
        self.sn = camera_config.serial_number

        # Camera-specific attributes
        self.is_global_shutter = False  # Can be made configurable
        self.device_info_dict = None
        self.device_index = 0
        self.camera = None  # PySpin CameraPtr type
        self.is_color = False  # Will be determined from config
        self.gamma_lut = None
        self.contrast_lut = None
        self.color_correction_param = None

        self.one_frame_post_processor = PySpin.ImageProcessor()
        self.conversion_pixel_format = PySpin.PixelFormat_Mono8
        self.convert_pixel_format = False
        self.one_frame_post_processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)

        self.auto_exposure_mode = None
        self.auto_gain_mode = None
        self.auto_wb_mode = None
        self.auto_wb_profile = None

        self.exposure_time = 1  # unit: ms
        self.analog_gain = 0
        self.frame_ID = -1
        self.frame_ID_software = -1
        self.frame_ID_offset_hardware_trigger = 0
        self.timestamp = 0

        self.image_locked = False
        self.current_frame = None

        self.callback_is_enabled = False
        self.is_streaming = False

        self.GAIN_MAX = 24
        self.GAIN_MIN = 0
        self.GAIN_STEP = 1
        self.EXPOSURE_TIME_MS_MIN = 0.01
        self.EXPOSURE_TIME_MS_MAX = 4000

        self.trigger_mode = None
        self.pixel_size_byte = 1

        # below are values for IMX226 (MER2-1220-32U3M) - to make configurable
        self.row_period_us = 10
        self.row_numbers = 3036
        self.exposure_delay_us_8bit = 650
        self.exposure_delay_us = self.exposure_delay_us_8bit * self.pixel_size_byte
        self.strobe_delay_us = self.exposure_delay_us + self.row_period_us * self.pixel_size_byte * (
            self.row_numbers - 1
        )

        self.pixel_format = None  # use the default pixel format

        self.is_live = False  # this determines whether a new frame received will be handled in the streamHandler

        self.image_event_handler = ImageEventHandler(self)

        # Open the camera
        self._open_camera(camera_config)

    def _open_camera(self, camera_config: CameraConfig):
        """Internal method to open and initialize the camera."""
        try:
            if self.camera:
                self.camera.DeInit()
                del self.camera
        except AttributeError:
            pass

        self.camera_list.Clear()
        self.camera_list = self.py_spin_system.GetCameras()
        device_num = self.camera_list.GetSize()
        if device_num == 0:
            raise CameraError("Could not find any USB camera devices!")

        if self.sn is None:
            self.device_index = 0
            self.camera = self.camera_list.GetByIndex(0)
        else:
            self.camera = self.camera_list.GetBySerial(str(self.sn))

        self.device_info_dict = get_device_info_full(self.camera, get_genicam=True)

        self.camera.Init()
        self.nodemap = self.camera.GetNodeMap()

        # Determine if color camera
        self.is_color = CameraPixelFormat.is_color_format(camera_config.default_pixel_format)
        if self.is_color and camera_config.default_white_balance_gains:
            wb = camera_config.default_white_balance_gains
            self.set_white_balance_gains(wb.r, wb.g, wb.b)

        # set to highest possible framerate
        PySpin.CBooleanPtr(self.nodemap.GetNode("AcquisitionFrameRateEnable")).SetValue(True)
        target_rate = 1000
        for decrement in range(0, 1000):
            try:
                PySpin.CFloatPtr(self.nodemap.GetNode("AcquisitionFrameRate")).SetValue(target_rate - decrement)
                break
            except PySpin.SpinnakerException as ex:
                pass

        # turn off device throughput limit
        node_throughput_limit = PySpin.CIntegerPtr(self.nodemap.GetNode("DeviceLinkThroughputLimit"))
        node_throughput_limit.SetValue(node_throughput_limit.GetMax())

        self.Width = PySpin.CIntegerPtr(self.nodemap.GetNode("Width")).GetValue()
        self.Height = PySpin.CIntegerPtr(self.nodemap.GetNode("Height")).GetValue()

        self.WidthMaxAbsolute = PySpin.CIntegerPtr(self.nodemap.GetNode("SensorWidth")).GetValue()
        self.HeightMaxAbsolute = PySpin.CIntegerPtr(self.nodemap.GetNode("SensorHeight")).GetValue()

        self.set_region_of_interest(0, 0, self.WidthMaxAbsolute, self.HeightMaxAbsolute)

        self.WidthMax = PySpin.CIntegerPtr(self.nodemap.GetNode("WidthMax")).GetValue()
        self.HeightMax = PySpin.CIntegerPtr(self.nodemap.GetNode("HeightMax")).GetValue()
        self.OffsetX = PySpin.CIntegerPtr(self.nodemap.GetNode("OffsetX")).GetValue()
        self.OffsetY = PySpin.CIntegerPtr(self.nodemap.GetNode("OffsetY")).GetValue()

        # disable gamma
        PySpin.CBooleanPtr(self.nodemap.GetNode("GammaEnable")).SetValue(False)

        # Apply default configuration
        if camera_config.default_pixel_format:
            self.set_pixel_format(camera_config.default_pixel_format)

        if camera_config.default_binning:
            self.set_binning(camera_config.default_binning[0], camera_config.default_binning[1])

        if camera_config.default_roi and any(r is not None for r in camera_config.default_roi):
            roi = camera_config.default_roi
            offset_x = roi[0] if roi[0] is not None else 0
            offset_y = roi[1] if roi[1] is not None else 0
            width = roi[2] if roi[2] is not None else self.WidthMax
            height = roi[3] if roi[3] is not None else self.HeightMax
            self.set_region_of_interest(offset_x, offset_y, width, height)

    # Callback management now handled by base class AbstractCamera
    # Users should use add_frame_callback() and remove_frame_callback() instead

    def close(self):
        try:
            self.camera.DeInit()
            del self.camera
        except AttributeError:
            pass
        self.camera = None
        self.auto_gain_mode = None
        self.auto_exposure_mode = None
        self.auto_wb_mode = None
        self.auto_wb_profile = None
        self.device_info_dict = None
        self.is_color = None
        self.gamma_lut = None
        self.contrast_lut = None
        self.color_correction_param = None
        self.last_raw_image = None
        self.last_converted_image = None
        self.last_numpy_image = None

    def set_exposure_time(self, exposure_time_ms: float):
        """Sets the exposure time in ms. Disables auto-exposure."""
        use_strobe = self.trigger_mode == TriggerMode.HARDWARE  # true if using hardware trigger
        self.nodemap = self.camera.GetNodeMap()
        node_auto_exposure = PySpin.CEnumerationPtr(self.nodemap.GetNode("ExposureAuto"))
        node_auto_exposure_off = PySpin.CEnumEntryPtr(node_auto_exposure.GetEntryByName("Off"))
        if not PySpin.IsReadable(node_auto_exposure_off) or not PySpin.IsWritable(node_auto_exposure):
            raise CameraError("Unable to set exposure manually (cannot disable auto exposure)")

        if node_auto_exposure.GetIntValue() != node_auto_exposure_off.GetValue():
            self.auto_exposure_mode = PySpin.CEnumEntryPtr(node_auto_exposure.GetCurrentEntry()).GetValue()

        node_auto_exposure.SetIntValue(node_auto_exposure_off.GetValue())

        node_exposure_time = PySpin.CFloatPtr(self.nodemap.GetNode("ExposureTime"))
        if not PySpin.IsWritable(node_exposure_time):
            raise CameraError("Unable to set exposure manually after disabling auto exposure")

        if use_strobe == False or self.is_global_shutter:
            self.exposure_time = exposure_time_ms
            node_exposure_time.SetValue(exposure_time_ms * 1000.0)
        else:
            # set the camera exposure time such that the active exposure time (illumination on time) is the desired value
            self.exposure_time = exposure_time_ms
            # add an additional 500 us so that the illumination can fully turn off before rows start to end exposure
            camera_exposure_time = (
                self.exposure_delay_us
                + self.exposure_time * 1000
                + self.row_period_us * self.pixel_size_byte * (self.row_numbers - 1)
                + 500
            )  # add an additional 500 us so that the illumination can fully turn off before rows start to end exposure
            node_exposure_time.SetValue(camera_exposure_time)

            # Set hardware strobe delay if in hardware trigger mode
            if self._hw_set_strobe_delay_ms_fn:
                strobe_time_ms = self.get_strobe_time()
                self._hw_set_strobe_delay_ms_fn(strobe_time_ms)

    def get_exposure_time(self) -> float:
        """Returns the current exposure time in milliseconds."""
        return self.exposure_time

    def get_exposure_limits(self) -> Tuple[float, float]:
        """Return the valid range of exposure times in inclusive milliseconds."""
        return (self.EXPOSURE_TIME_MS_MIN, self.EXPOSURE_TIME_MS_MAX)

    def get_strobe_time(self) -> float:
        """
        Given the current exposure time, return the strobe time such that
        get_strobe_time() + get_exposure_time() == total frame time. In milliseconds.
        """
        return self.strobe_delay_us / 1000.0

    def update_camera_exposure_time(self):
        self.set_exposure_time(self.exposure_time)

    def set_analog_gain(self, analog_gain: float):
        """Set analog gain as an input multiple. Disables auto-gain."""
        self.nodemap = self.camera.GetNodeMap()

        node_auto_gain = PySpin.CEnumerationPtr(self.nodemap.GetNode("GainAuto"))
        node_auto_gain_off = PySpin.CEnumEntryPtr(node_auto_gain.GetEntryByName("Off"))
        if not PySpin.IsReadable(node_auto_gain_off) or not PySpin.IsWritable(node_auto_gain):
            raise CameraError("Unable to set gain manually (cannot disable auto gain)")

        if node_auto_gain.GetIntValue() != node_auto_gain_off.GetValue():
            self.auto_gain_mode = PySpin.CEnumEntryPtr(node_auto_gain.GetCurrentEntry()).GetValue()

        node_auto_gain.SetIntValue(node_auto_gain_off.GetValue())

        node_gain = PySpin.CFloatPtr(self.nodemap.GetNode("Gain"))

        if not PySpin.IsWritable(node_gain):
            raise CameraError("Unable to set gain manually after disabling auto gain")

        self.analog_gain = analog_gain
        node_gain.SetValue(analog_gain)

    def get_analog_gain(self) -> float:
        """Returns gain in the same units as set_analog_gain."""
        return self.analog_gain

    def get_gain_range(self) -> CameraGainRange:
        """Returns the gain range, and minimum gain step, for this camera."""
        return CameraGainRange(min_gain=self.GAIN_MIN, max_gain=self.GAIN_MAX, gain_step=self.GAIN_STEP)

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        """Returns the (R, G, B) white balance gains."""
        self.nodemap = self.camera.GetNodeMap()
        node_balance_white_auto = PySpin.CEnumerationPtr(self.nodemap.GetNode("BalanceWhiteAuto"))
        # node_balance_white_auto_options = [PySpin.CEnumEntryPtr(entry).GetName() for entry in node_balance_white_auto.GetEntries()]
        # print("WB Auto options: "+str(node_balance_white_auto_options))

        node_balance_ratio_select = PySpin.CEnumerationPtr(self.nodemap.GetNode("BalanceRatioSelector"))
        # node_balance_ratio_select_options = [PySpin.CEnumEntryPtr(entry).GetName() for entry in node_balance_ratio_select.GetEntries()]
        # print("Balance Ratio Select options: "+str(node_balance_ratio_select_options))
        """
        node_balance_profile = PySpin.CEnumerationPtr(self.nodemap.GetNode("BalanceWhiteAutoProfile"))
        node_balance_profile_options= [PySpin.CEnumEntryPtr(entry).GetName() for entry in node_balance_profile.GetEntries()]
        print("WB Auto Profile options: "+str(node_balance_profile_options))
        """
        node_balance_white_auto_off = PySpin.CEnumEntryPtr(node_balance_white_auto.GetEntryByName("Off"))
        if not PySpin.IsReadable(node_balance_white_auto) or not PySpin.IsReadable(node_balance_white_auto_off):
            print("Unable to check if white balance is auto or not")

        elif (
            PySpin.IsWritable(node_balance_white_auto)
            and node_balance_white_auto.GetIntValue() == node_balance_white_auto_off.GetValue()
        ):
            if self.auto_wb_mode is not None:
                node_balance_white_auto.SetIntValue(self.auto_wb_mode)
            else:
                node_balance_white_continuous = PySpin.CEnumEntryPtr(
                    node_balance_white_auto.GetEntryByName("Continuous")
                )
                if PySpin.IsReadable(node_balance_white_continuous):
                    node_balance_white_auto.SetIntValue(node_balance_white_continuous.GetValue())
                else:
                    print("Cannot turn on auto white balance in continuous mode")
                    node_balance_white_once = PySpin.CEnumEntryPtr(node_balance_white_auto.GetEntry("Once"))
                    if PySpin.IsReadable(node_balance_white_once):
                        node_balance_white_auto.SetIntValue(node_balance_white_once.GetValue())
                    else:
                        print("Cannot turn on auto white balance in Once mode")
        else:
            print("Cannot turn on auto white balance, or auto white balance is already on")

        balance_ratio_red = PySpin.CEnumEntryPtr(node_balance_ratio_select.GetEntryByName("Red"))
        balance_ratio_green = PySpin.CEnumEntryPtr(node_balance_ratio_select.GetEntryByName("Green"))
        balance_ratio_blue = PySpin.CEnumEntryPtr(node_balance_ratio_select.GetEntryByName("Blue"))
        node_balance_ratio = PySpin.CFloatPtr(self.nodemap.GetNode("BalanceRatio"))
        if (
            not PySpin.IsWritable(node_balance_ratio_select)
            or not PySpin.IsReadable(balance_ratio_red)
            or not PySpin.IsReadable(balance_ratio_green)
            or not PySpin.IsReadable(balance_ratio_blue)
        ):
            print("Unable to move balance ratio selector")
            return (0, 0, 0)

        node_balance_ratio_select.SetIntValue(balance_ratio_red.GetValue())
        if not PySpin.IsReadable(node_balance_ratio):
            print("Unable to read balance ratio for red")
            awb_r = 0
        else:
            awb_r = node_balance_ratio.GetValue()

        node_balance_ratio_select.SetIntValue(balance_ratio_green.GetValue())
        if not PySpin.IsReadable(node_balance_ratio):
            print("Unable to read balance ratio for green")
            awb_g = 0
        else:
            awb_g = node_balance_ratio.GetValue()

        node_balance_ratio_select.SetIntValue(balance_ratio_blue.GetValue())
        if not PySpin.IsReadable(node_balance_ratio):
            print("Unable to read balance ratio for blue")
            awb_b = 0
        else:
            awb_b = node_balance_ratio.GetValue()

        return (awb_r, awb_g, awb_b)

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        """Set the (R, G, B) white balance gains. Disables auto WB."""
        self.nodemap = self.camera.GetNodeMap()
        node_balance_white_auto = PySpin.CEnumerationPtr(self.nodemap.GetNode("BalanceWhiteAuto"))
        node_balance_ratio_select = PySpin.CEnumerationPtr(self.nodemap.GetNode("BalanceRatioSelector"))
        node_balance_white_auto_off = PySpin.CEnumEntryPtr(node_balance_white_auto.GetEntryByName("Off"))
        if not PySpin.IsReadable(node_balance_white_auto) or not PySpin.IsReadable(node_balance_white_auto_off):
            self._log.warning("Unable to check if white balance is auto or not")
        elif node_balance_white_auto.GetIntValue() != node_balance_white_auto_off.GetValue():
            self.auto_wb_value = node_balance_white_auto.GetIntValue()
            if PySpin.IsWritable(node_balance_white_auto):
                node_balance_white_auto.SetIntValue(node_balance_white_auto_off.GetValue())
            else:
                self._log.warning("Cannot turn off auto WB")

        balance_ratio_red = PySpin.CEnumEntryPtr(node_balance_ratio_select.GetEntryByName("Red"))
        balance_ratio_green = PySpin.CEnumEntryPtr(node_balance_ratio_select.GetEntryByName("Green"))
        balance_ratio_blue = PySpin.CEnumEntryPtr(node_balance_ratio_select.GetEntryByName("Blue"))
        node_balance_ratio = PySpin.CFloatPtr(self.nodemap.GetNode("BalanceRatio"))
        if (
            not PySpin.IsWritable(node_balance_ratio_select)
            or not PySpin.IsReadable(balance_ratio_red)
            or not PySpin.IsReadable(balance_ratio_green)
            or not PySpin.IsReadable(balance_ratio_blue)
        ):
            raise CameraError("Unable to move balance ratio selector")

        node_balance_ratio_select.SetIntValue(balance_ratio_red.GetValue())
        if not PySpin.IsWritable(node_balance_ratio):
            raise CameraError("Unable to write balance ratio for red")
        node_balance_ratio.SetValue(red_gain)

        node_balance_ratio_select.SetIntValue(balance_ratio_green.GetValue())
        if not PySpin.IsWritable(node_balance_ratio):
            raise CameraError("Unable to write balance ratio for green")
        node_balance_ratio.SetValue(green_gain)

        node_balance_ratio_select.SetIntValue(balance_ratio_blue.GetValue())
        if not PySpin.IsWritable(node_balance_ratio):
            raise CameraError("Unable to write balance ratio for blue")
        node_balance_ratio.SetValue(blue_gain)

    def set_auto_white_balance_gains(self, on: bool):
        """Turn auto white balance on or off."""
        self.nodemap = self.camera.GetNodeMap()
        node_balance_white_auto = PySpin.CEnumerationPtr(self.nodemap.GetNode("BalanceWhiteAuto"))

        if on:
            # Try continuous mode first
            node_balance_white_continuous = PySpin.CEnumEntryPtr(node_balance_white_auto.GetEntryByName("Continuous"))
            if PySpin.IsReadable(node_balance_white_continuous) and PySpin.IsWritable(node_balance_white_auto):
                node_balance_white_auto.SetIntValue(node_balance_white_continuous.GetValue())
            else:
                # Fallback to Once mode
                node_balance_white_once = PySpin.CEnumEntryPtr(node_balance_white_auto.GetEntryByName("Once"))
                if PySpin.IsReadable(node_balance_white_once) and PySpin.IsWritable(node_balance_white_auto):
                    node_balance_white_auto.SetIntValue(node_balance_white_once.GetValue())
                else:
                    raise CameraError("Cannot turn on auto white balance")
        else:
            # Turn off auto WB
            node_balance_white_auto_off = PySpin.CEnumEntryPtr(node_balance_white_auto.GetEntryByName("Off"))
            if PySpin.IsReadable(node_balance_white_auto_off) and PySpin.IsWritable(node_balance_white_auto):
                node_balance_white_auto.SetIntValue(node_balance_white_auto_off.GetValue())
            else:
                raise CameraError("Cannot turn off auto white balance")

    def set_reverse_x(self, value):
        self.nodemap = self.camera.GetNodeMap()
        node_reverse_x = PySpin.CBooleanPtr(self.nodemap.GetNode("ReverseX"))
        if not PySpin.IsWritable(node_reverse_x):
            print("Can't write to reverse X node")
            return
        else:
            node_reverse_x.SetValue(bool(value))

    def set_reverse_y(self, value):
        self.nodemap = self.camera.GetNodeMap()
        node_reverse_y = PySpin.CBooleanPtr(self.nodemap.GetNode("ReverseY"))
        if not PySpin.IsWritable(node_reverse_y):
            print("Can't write to reverse Y node")
            return
        else:
            node_reverse_y.SetValue(bool(value))

    def start_streaming(self):
        """Start camera frame streaming."""
        if not self.is_streaming:
            try:
                # Register the event handler for callbacks
                if not self.callback_is_enabled:
                    self.camera.RegisterEventHandler(self.image_event_handler)
                    self.callback_is_enabled = True
                self.camera.BeginAcquisition()
            except PySpin.SpinnakerException as ex:
                raise CameraError(f"Spinnaker exception during start_streaming: {str(ex)}")
        if self.camera.IsStreaming():
            self._log.debug("Camera is streaming")
            self.is_streaming = True
            self._trigger_sent.clear()

    def stop_streaming(self):
        """Stop camera frame streaming."""
        if self.is_streaming:
            try:
                if self.callback_is_enabled:
                    self.camera.UnregisterEventHandler(self.image_event_handler)
                    self.callback_is_enabled = False
                self.camera.EndAcquisition()
            except PySpin.SpinnakerException as ex:
                raise CameraError(f"Spinnaker exception during stop_streaming: {str(ex)}")
        if not self.camera.IsStreaming():
            self._log.debug("Camera is not streaming")
            self.is_streaming = False
            self._trigger_sent.clear()

    def get_is_streaming(self):
        """Returns whether the camera is currently streaming."""
        return self.is_streaming

    # Black level methods
    def set_black_level(self, black_level: float):
        """Sets the black level of captured images."""
        raise NotImplementedError("Black level control not implemented for FLIR cameras")

    def get_black_level(self) -> float:
        """Gets the black level set on the camera."""
        raise NotImplementedError("Black level control not implemented for FLIR cameras")

    def set_pixel_format(self, pixel_format: CameraPixelFormat, convert_if_not_native=False):
        """Set the pixel format of the camera."""
        # Convert enum to string if needed
        if isinstance(pixel_format, CameraPixelFormat):
            pixel_format_str = pixel_format.value
        else:
            pixel_format_str = str(pixel_format)

        with self._pause_streaming():
            self.nodemap = self.camera.GetNodeMap()

            node_pixel_format = PySpin.CEnumerationPtr(self.nodemap.GetNode("PixelFormat"))
            node_adc_bit_depth = PySpin.CEnumerationPtr(self.nodemap.GetNode("AdcBitDepth"))

            if PySpin.IsWritable(node_pixel_format) and PySpin.IsWritable(node_adc_bit_depth):
                pixel_selection = None
                pixel_size_byte = None
                adc_bit_depth = None
                fallback_pixel_selection = None
                conversion_pixel_format = None
                if pixel_format_str == "MONO8":
                    pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("Mono8"))
                    conversion_pixel_format = PySpin.PixelFormat_Mono8
                    pixel_size_byte = 1
                    adc_bit_depth = PySpin.CEnumEntryPtr(node_adc_bit_depth.GetEntryByName("Bit10"))
                if pixel_format_str == "MONO10":
                    pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("Mono10"))
                    fallback_pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("Mono10p"))
                    conversion_pixel_format = PySpin.PixelFormat_Mono8
                    pixel_size_byte = 1
                    adc_bit_depth = PySpin.CEnumEntryPtr(node_adc_bit_depth.GetEntryByName("Bit10"))
                if pixel_format_str == "MONO12":
                    pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("Mono12"))
                    fallback_pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("Mono12p"))
                    conversion_pixel_format = PySpin.PixelFormat_Mono16
                    pixel_size_byte = 2
                    adc_bit_depth = PySpin.CEnumEntryPtr(node_adc_bit_depth.GetEntryByName("Bit12"))
                if pixel_format_str == "MONO14":  # MONO14/16 are aliases of each other, since they both
                    # do ADC at bit depth 14
                    pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("Mono16"))
                    conversion_pixel_format = PySpin.PixelFormat_Mono16
                    pixel_size_byte = 2
                    adc_bit_depth = PySpin.CEnumEntryPtr(node_adc_bit_depth.GetEntryByName("Bit14"))
                if pixel_format_str == "MONO16":
                    pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("Mono16"))
                    conversion_pixel_format = PySpin.PixelFormat_Mono16
                    pixel_size_byte = 2
                    adc_bit_depth = PySpin.CEnumEntryPtr(node_adc_bit_depth.GetEntryByName("Bit14"))
                if pixel_format_str == "BAYER_RG8":
                    pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("BayerRG8"))
                    conversion_pixel_format = PySpin.PixelFormat_BayerRG8
                    pixel_size_byte = 1
                    adc_bit_depth = PySpin.CEnumEntryPtr(node_adc_bit_depth.GetEntryByName("Bit10"))
                if pixel_format_str == "BAYER_RG12":
                    pixel_selection = PySpin.CEnumEntryPtr(node_pixel_format.GetEntryByName("BayerRG12"))
                    conversion_pixel_format = PySpin.PixelFormat_BayerRG12
                    pixel_size_byte = 2
                    adc_bit_depth = PySpin.CEnumEntryPtr(node_adc_bit_depth.GetEntryByName("Bit12"))

                if pixel_selection is not None and adc_bit_depth is not None:
                    if PySpin.IsReadable(pixel_selection):
                        node_pixel_format.SetIntValue(pixel_selection.GetValue())
                        self.pixel_size_byte = pixel_size_byte
                        self.pixel_format = pixel_format_str
                        self.convert_pixel_format = False
                        if PySpin.IsReadable(adc_bit_depth):
                            node_adc_bit_depth.SetIntValue(adc_bit_depth.GetValue())
                    elif PySpin.IsReadable(fallback_pixel_selection):
                        node_pixel_format.SetIntValue(fallback_pixel_selection.GetValue())
                        self.pixel_size_byte = pixel_size_byte
                        self.pixel_format = pixel_format_str
                        self.conversion_pixel_format = conversion_pixel_format
                        self.convert_pixel_format = True
                        if PySpin.IsReadable(adc_bit_depth):
                            node_adc_bit_depth.SetIntValue(adc_bit_depth.GetValue())
                    else:
                        self.convert_pixel_format = convert_if_not_native
                        if convert_if_not_native:
                            self.conversion_pixel_format = conversion_pixel_format
                        self._log.warning("Pixel format not available for this camera")
                        if PySpin.IsReadable(adc_bit_depth):
                            node_adc_bit_depth.SetIntValue(adc_bit_depth.GetValue())
                            self._log.info("Still able to set ADC bit depth to " + adc_bit_depth.GetSymbolic())
                else:
                    raise ValueError(f"Pixel format {pixel_format_str} not implemented for FLIR camera")
            else:
                raise CameraError("pixel format is not writable")

            # update the exposure delay and strobe delay
            self.exposure_delay_us = self.exposure_delay_us_8bit * self.pixel_size_byte
            self.strobe_delay_us = self.exposure_delay_us + self.row_period_us * self.pixel_size_byte * (
                self.row_numbers - 1
            )

    def get_pixel_format(self) -> CameraPixelFormat:
        """Returns the current pixel format."""
        if self.pixel_format:
            try:
                return CameraPixelFormat(self.pixel_format)
            except ValueError:
                self._log.warning(f"Unknown pixel format {self.pixel_format}, returning MONO8")
                return CameraPixelFormat.MONO8
        return CameraPixelFormat.MONO8

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        """Returns the list of pixel formats supported by the camera."""
        return [
            CameraPixelFormat.MONO8,
            CameraPixelFormat.MONO10,
            CameraPixelFormat.MONO12,
            CameraPixelFormat.MONO14,
            CameraPixelFormat.MONO16,
            CameraPixelFormat.BAYER_RG8,
            CameraPixelFormat.BAYER_RG12,
        ]

    def set_frame_format(self, frame_format: CameraFrameFormat):
        """Set the frame format. FLIR cameras only support RAW."""
        if frame_format != CameraFrameFormat.RAW:
            raise ValueError("Only the RAW frame format is supported by FLIR cameras.")
        return True

    def get_frame_format(self) -> CameraFrameFormat:
        """Returns the current frame format."""
        return CameraFrameFormat.RAW

    def get_resolution(self) -> Tuple[int, int]:
        """Returns the maximum resolution of the camera under the current binning setting."""
        return (self.Width, self.Height)

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        """Set the binning factor of the camera."""
        # FLIR cameras typically handle binning through the Spinnaker API
        # For now, only support 1x1 binning (no binning)
        if binning_factor_x != 1 or binning_factor_y != 1:
            raise NotImplementedError("Binning has not been implemented for this FLIR camera yet.")

    def get_binning(self) -> Tuple[int, int]:
        """Return the (binning_factor_x, binning_factor_y) of the camera right now."""
        return (1, 1)

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        """Return the list of binning options supported by the camera."""
        return [(1, 1)]

    def get_pixel_size_unbinned_um(self) -> float:
        """Returns the pixel size without binning in microns."""
        return self.PIXEL_SIZE_UM

    def get_pixel_size_binned_um(self) -> float:
        """Returns the pixel size after binning in microns."""
        binning = self.get_binning()
        return self.PIXEL_SIZE_UM * binning[0]

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        """Set the acquisition mode of the camera."""
        with self._pause_streaming():
            self.nodemap = self.camera.GetNodeMap()
            node_trigger_mode = PySpin.CEnumerationPtr(self.nodemap.GetNode("TriggerMode"))

            if acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
                node_trigger_mode_off = PySpin.CEnumEntryPtr(node_trigger_mode.GetEntryByName("Off"))
                if not PySpin.IsWritable(node_trigger_mode) or not PySpin.IsReadable(node_trigger_mode_off):
                    raise CameraError("Cannot toggle TriggerMode to Off")
                node_trigger_mode.SetIntValue(node_trigger_mode_off.GetValue())
                self.trigger_mode = TriggerMode.CONTINUOUS

            elif acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
                node_trigger_mode_on = PySpin.CEnumEntryPtr(node_trigger_mode.GetEntryByName("On"))
                if not PySpin.IsWritable(node_trigger_mode) or not PySpin.IsReadable(node_trigger_mode_on):
                    raise CameraError("Cannot toggle TriggerMode to On")
                node_trigger_mode.SetIntValue(node_trigger_mode_on.GetValue())

                node_trigger_source = PySpin.CEnumerationPtr(self.nodemap.GetNode("TriggerSource"))
                node_trigger_source_software = PySpin.CEnumEntryPtr(node_trigger_source.GetEntryByName("Software"))
                if not PySpin.IsWritable(node_trigger_source) or not PySpin.IsReadable(node_trigger_source_software):
                    raise CameraError("Cannot set Trigger source to Software")
                node_trigger_source.SetIntValue(node_trigger_source_software.GetValue())
                self.trigger_mode = TriggerMode.SOFTWARE

            elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
                node_trigger_mode_on = PySpin.CEnumEntryPtr(node_trigger_mode.GetEntryByName("On"))
                if not PySpin.IsWritable(node_trigger_mode) or not PySpin.IsReadable(node_trigger_mode_on):
                    raise CameraError("Cannot toggle TriggerMode to On")
                node_trigger_mode.SetIntValue(node_trigger_mode_on.GetValue())

                # Use Line2 as default hardware trigger source
                node_trigger_source = PySpin.CEnumerationPtr(self.nodemap.GetNode("TriggerSource"))
                node_trigger_source_line2 = PySpin.CEnumEntryPtr(node_trigger_source.GetEntryByName("Line2"))
                if not PySpin.IsWritable(node_trigger_source) or not PySpin.IsReadable(node_trigger_source_line2):
                    raise CameraError("Cannot set Trigger source to Line2")
                node_trigger_source.SetIntValue(node_trigger_source_line2.GetValue())

                # Set trigger activation to rising edge
                node_trigger_activation = PySpin.CEnumerationPtr(self.nodemap.GetNode("TriggerActivation"))
                node_trigger_activation_rising = PySpin.CEnumEntryPtr(
                    node_trigger_activation.GetEntryByName("RisingEdge")
                )
                if not PySpin.IsWritable(node_trigger_activation) or not PySpin.IsReadable(
                    node_trigger_activation_rising
                ):
                    raise CameraError("Cannot set trigger activation mode to RisingEdge")
                node_trigger_activation.SetIntValue(node_trigger_activation_rising.GetValue())

                self.frame_ID_offset_hardware_trigger = None
                self.trigger_mode = TriggerMode.HARDWARE

            else:
                raise ValueError(f"Unsupported acquisition mode: {acquisition_mode}")

            self.update_camera_exposure_time()
        return True

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        """Returns the current acquisition mode."""
        if self.trigger_mode == TriggerMode.CONTINUOUS:
            return CameraAcquisitionMode.CONTINUOUS
        elif self.trigger_mode == TriggerMode.SOFTWARE:
            return CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif self.trigger_mode == TriggerMode.HARDWARE:
            return CameraAcquisitionMode.HARDWARE_TRIGGER
        else:
            # Default to continuous if not set
            return CameraAcquisitionMode.CONTINUOUS

    def send_trigger(self, illumination_time: Optional[float] = None):
        """Send a trigger to the camera."""
        if not self.get_is_streaming():
            raise CameraError("Camera is not streaming, cannot send trigger")

        if not self.get_ready_for_trigger():
            raise CameraError(
                f"Requested trigger too early (last trigger was {time.time() - self._last_trigger_timestamp} [s] ago), refusing."
            )

        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            if not self._hw_trigger_fn:
                raise CameraError("In HARDWARE_TRIGGER mode, but no hw trigger function given.")
            self._hw_trigger_fn(illumination_time)
        elif self.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            self.nodemap = self.camera.GetNodeMap()
            node_trigger = PySpin.CCommandPtr(self.nodemap.GetNode("TriggerSoftware"))
            if not PySpin.IsWritable(node_trigger):
                raise CameraError("Trigger node not writable")
            node_trigger.Execute()

        self._last_trigger_timestamp = time.time()
        self._trigger_sent.set()

    def get_ready_for_trigger(self) -> bool:
        """Returns true if the camera is ready for another trigger."""
        if time.time() - self._last_trigger_timestamp > 1.5 * ((self.get_total_frame_time() + 4) / 1000.0):
            self._trigger_sent.clear()
        return not self._trigger_sent.is_set()

    def read_camera_frame(self) -> Optional[CameraFrame]:
        """
        Read a frame from the camera and return it as a CameraFrame object.
        This should be called with the event handler disabled in most cases since the event
        handler will automatically propagate frames to callbacks.
        """
        if not self.camera.IsStreaming():
            self._log.error("Cannot read frame, camera not streaming")
            return None

        try:
            raw_image = self.camera.GetNextImage(1000)
            if raw_image.IsIncomplete():
                self._log.warning(f"Image incomplete with image status {raw_image.GetImageStatus()}")
                raw_image.Release()
                return None

            if self.is_color and "mono" not in self.pixel_format.lower():
                if (
                    "10" in self.pixel_format
                    or "12" in self.pixel_format
                    or "14" in self.pixel_format
                    or "16" in self.pixel_format
                ):
                    rgb_image = self.one_frame_post_processor.Convert(raw_image, PySpin.PixelFormat_RGB16)
                else:
                    rgb_image = self.one_frame_post_processor.Convert(raw_image, PySpin.PixelFormat_RGB8)
                numpy_image = rgb_image.GetNDArray()
                if self.pixel_format == "BAYER_RG12":
                    numpy_image = numpy_image << 4
            else:
                if self.convert_pixel_format:
                    converted_image = self.one_frame_post_processor.Convert(raw_image, self.conversion_pixel_format)
                    numpy_image = converted_image.GetNDArray()
                    if self.conversion_pixel_format == PySpin.PixelFormat_Mono12:
                        numpy_image = numpy_image << 4
                else:
                    try:
                        numpy_image = raw_image.GetNDArray()
                    except PySpin.SpinnakerException:
                        self._log.warning("Encountered problem getting ndarray, falling back to conversion to Mono8")
                        converted_image = self.one_frame_post_processor.Convert(raw_image, PySpin.PixelFormat_Mono8)
                        numpy_image = converted_image.GetNDArray()
                    if self.pixel_format == "MONO12":
                        numpy_image = numpy_image << 4

            # Process frame and create CameraFrame object
            processed_frame = self._process_raw_frame(numpy_image)

            with self._frame_lock:
                self.frame_ID_software = self.frame_ID_software + 1
                self.frame_ID = raw_image.GetFrameID()
                if self.trigger_mode == TriggerMode.HARDWARE:
                    if self.frame_ID_offset_hardware_trigger == None:
                        self.frame_ID_offset_hardware_trigger = self.frame_ID
                    self.frame_ID = self.frame_ID - self.frame_ID_offset_hardware_trigger
                self.timestamp = time.time()

                camera_frame = CameraFrame(
                    frame_id=self.frame_ID,
                    timestamp=self.timestamp,
                    frame=processed_frame,
                    frame_format=self.get_frame_format(),
                    frame_pixel_format=self.get_pixel_format(),
                )
                self.current_frame = camera_frame

            raw_image.Release()

            # Propagate to callbacks
            self._propogate_frame(camera_frame)

            return camera_frame

        except PySpin.SpinnakerException as ex:
            self._log.error(f"Spinnaker exception during read_camera_frame: {str(ex)}")
            return None

    def get_frame_id(self) -> int:
        """Returns the frame id of the current frame."""
        with self._frame_lock:
            return self.frame_ID if self.current_frame else -1

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        """Set the region of interest of the camera."""
        with self._pause_streaming():
            self.nodemap = self.camera.GetNodeMap()
            node_width = PySpin.CIntegerPtr(self.nodemap.GetNode("Width"))
            node_height = PySpin.CIntegerPtr(self.nodemap.GetNode("Height"))
            node_width_max = PySpin.CIntegerPtr(self.nodemap.GetNode("WidthMax"))
            node_height_max = PySpin.CIntegerPtr(self.nodemap.GetNode("HeightMax"))
            node_offset_x = PySpin.CIntegerPtr(self.nodemap.GetNode("OffsetX"))
            node_offset_y = PySpin.CIntegerPtr(self.nodemap.GetNode("OffsetY"))

            # update the camera setting
            if PySpin.IsWritable(node_width):
                node_min = node_width.GetMin()
                node_inc = node_width.GetInc()
                diff = width - node_min
                num_incs = diff // node_inc
                width = node_min + num_incs * node_inc
                self.Width = width
                node_width.SetValue(min(max(int(width), 0), node_width_max.GetValue()))
            else:
                raise CameraError("Width is not implemented or not writable")

            # update the camera setting
            if PySpin.IsWritable(node_height):
                node_min = node_height.GetMin()
                node_inc = node_height.GetInc()
                diff = height - node_min
                num_incs = diff // node_inc
                height = node_min + num_incs * node_inc

                self.Height = height
                node_height.SetValue(min(max(int(height), 0), node_height_max.GetValue()))
            else:
                raise CameraError("Height is not implemented or not writable")

            # update the camera setting
            if PySpin.IsWritable(node_offset_x):
                node_min = node_offset_x.GetMin()
                node_max = node_offset_x.GetMax()
                node_inc = node_offset_x.GetInc()
                diff = offset_x - node_min
                num_incs = diff // node_inc
                offset_x = node_min + num_incs * node_inc

                self.OffsetX = offset_x
                node_offset_x.SetValue(min(int(offset_x), node_max))
            else:
                raise CameraError("OffsetX is not implemented or not writable")

            # update the camera setting
            if PySpin.IsWritable(node_offset_y):
                node_min = node_offset_y.GetMin()
                node_max = node_offset_y.GetMax()
                node_inc = node_offset_y.GetInc()
                diff = offset_y - node_min
                num_incs = diff // node_inc
                offset_y = node_min + num_incs * node_inc

                self.OffsetY = offset_y
                node_offset_y.SetValue(min(int(offset_y), node_max))
            else:
                raise CameraError("OffsetY is not implemented or not writable")

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        """Returns the region of interest as a tuple of (x corner, y corner, width, height)."""
        return (self.OffsetX, self.OffsetY, self.Width, self.Height)

    def set_temperature(self, temperature_deg_c: Optional[float]):
        """Set the desired temperature of the camera in degrees C."""
        raise NotImplementedError("Temperature control not implemented for FLIR cameras")

    def get_temperature(self) -> float:
        """Get the current temperature of the camera in deg C."""
        raise NotImplementedError("Temperature reading not implemented for FLIR cameras")

    def set_temperature_reading_callback(self, callback: Callable):
        """Set the callback to be called when the temperature reading changes."""
        raise NotImplementedError("Temperature reading callback not implemented for FLIR cameras")

    def reset_camera_acquisition_counter(self):
        self.nodemap = self.camera.GetNodeMap()
        node_counter_event_source = PySpin.CEnumerationPtr(self.nodemap.GetNode("CounterEventSource"))
        node_counter_event_source_line2 = PySpin.CEnumEntryPtr(node_counter_event_source.GetEntryByName("Line2"))
        if PySpin.IsWritable(node_counter_event_source) and PySpin.IsReadable(node_counter_event_source_line2):
            node_counter_event_source.SetIntValue(node_counter_event_source_line2.GetValue())
        else:
            print("CounterEventSource is not implemented or not writable, or Line 2 is not an option")

        node_counter_reset = PySpin.CCommandPtr(self.nodemap.GetNode("CounterReset"))

        if PySpin.IsImplemented(node_counter_reset) and PySpin.IsWritable(node_counter_reset):
            node_counter_reset.Execute()
        else:
            print("CounterReset is not implemented")

    def set_line3_to_strobe(self):  # FLIR cams don't have the right Line layout for this
        # self.camera.StrobeSwitch.set(gx.GxSwitchEntry.ON)
        # self.nodemap = self.camera.GetNodeMap()

        # node_line_selector = PySpin.CEnumerationPtr(self.nodemap.GetNode('LineSelector'))

        # node_line3 = PySpin.CEnumEntryPtr(node_line_selector.GetEntryByName('Line3'))

        # self.camera.LineSelector.set(gx.GxLineSelectorEntry.LINE3)
        # self.camera.LineMode.set(gx.GxLineModeEntry.OUTPUT)
        # self.camera.LineSource.set(gx.GxLineSourceEntry.STROBE)
        pass

    def set_line3_to_exposure_active(self):  # BlackFly cam has no output on Line 3
        # self.camera.StrobeSwitch.set(gx.GxSwitchEntry.ON)
        # self.camera.LineSelector.set(gx.GxLineSelectorEntry.LINE3)
        # self.camera.LineMode.set(gx.GxLineModeEntry.OUTPUT)
        # self.camera.LineSource.set(gx.GxLineSourceEntry.EXPOSURE_ACTIVE)
        pass

    def __del__(self):
        try:
            self.stop_streaming()
            self.camera.DeInit()
            del self.camera
        except AttributeError:
            pass
        self.camera_list.Clear()
        self.py_spin_system.ReleaseInstance()
