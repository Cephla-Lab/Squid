import serial
import threading
from enum import Enum
import time
import math
import serial.tools.list_ports
import re

SETTINGS_FILENAME = "control/Xeryon_settings.txt"
LIBRARY_VERSION = "v1.88"

# DEBUG MODE
# This variable is set to True if you are in debug mode.
# This is handy when no stage is connected.
# It ignores some checks, e.g.: if DPOS=.. command is send and in Debug mode, the EPOS isn't checked if it's in range.
DEBUG_MODE = False

# OUTPUT_TO_CONSOLE
# If set to True, a lot of data is outputted to the console.
# e.g.: If you set DPOS=..., the new DPOS and EPOS are printed to the console.
OUTPUT_TO_CONSOLE = True  # False: this blocks all output to the console.

# DISABLE WAITING
# If set to True, the library won't wait until the position is reached.
# All blocking functions will be disabled.
# NOTE: If you enable this, after finding the index of each stage, do a +/- 5 second timeout (time.sleep(5))
DISABLE_WAITING = False

# AUTO_SEND_SETTINGS
# If set to True, the library will automatically send the settings in the settings_default.txt
# to the connected stages on startup.
AUTO_SEND_SETTINGS = True

# AUTO_SEND_ENBL
# "ENBL=1" needs to be send when an error occurs.
# Errors like: thermal error (bit 2&3), error limit (bit 16) or safety timeout (bit18)
# Set it to True to automatically send "ENBL=1" when these errors occur, bypassing this 'safety' feature.
AUTO_SEND_ENBL = False

# The value's of these commands don't get stored in this library.
NOT_SETTING_COMMANDS = [
    "DPOS",
    "EPOS",
    "HOME",
    "ZERO",
    "RSET",
    "INDX",
    "STEP",
    "MOVE",
    "STOP",
    "CONT",
    "SAVE",
    "STAT",
    "TIME",
    "SRNO",
    "SOFT",
    "XLA3",
    "XLA1",
    "XRT1",
    "XRT3",
    "XLS1",
    "XLS3",
    "SFRQ",
    "SYNC",
]
DEFAULT_POLI_VALUE = 200
AMPLITUDE_MULTIPLIER = 1456.0
PHASE_MULTIPLIER = 182


class Xeryon:
    axis_list = None  # A list storing all the axis in the system.
    axis_letter_list = None  # A list storing all the axis_letters in the system.
    master_settings = None

    def __init__(self, COM_port=None, baudrate=115200):
        """
        :param COM_port: Specify the COM port used
        :type COM_port: string
        :param baudrate: Specify the baudrate
        :type baudrate: int
        :return: Return a Xeryon object.

        Main Xeryon Drive Class, initialize with the COM port and baudrate for communication with the driver.
        """
        self.comm = Communication(self, COM_port, baudrate)  # Startup communication
        self.axis_list = []
        self.axis_letter_list = []
        self.master_settings = {}

    def isSingleAxisSystem(self):
        """
        :return: Returns True if it's a single axis system, False if its a multiple axis system.
        """
        return len(self.getAllAxis()) <= 1

    def start(self, external_communication_thread=False, external_settings_default=None):
        """
        :return: Nothing.
        This functions NEEDS to be ran before any commands are executed.
        This function starts the serial communication and configures the settings with the controller.
        """

        if len(self.getAllAxis()) <= 0:
            raise Exception(
                "Cannot start the system without stages. The stages don't have to be connnected, only initialized in the software."
            )

        comm = self.getCommunication().start(external_communication_thread)  # Start communication

        for axis in self.getAllAxis():
            axis.reset()

        time.sleep(0.2)

        self.readSettings(external_settings_default)  # Read settings file
        if AUTO_SEND_SETTINGS:
            self.sendMasterSettings()
            for axis in self.getAllAxis():  # Loop trough each axis:
                axis.sendSettings()  # Send the settings

        # Enable all axes
        for axis in self.getAllAxis():
            axis.sendCommand("ENBL=1")

        # ask for LLIM & HLIM value's
        # TODO: Put ECHO on temporarily so each questioned value is returned
        for axis in self.getAllAxis():
            axis.sendCommand("HLIM=?")
            axis.sendCommand("LLIM=?")
            axis.sendCommand("SSPD=?")
            axis.sendCommand("PTO2=?")
            axis.sendCommand("PTOL=?")
            if "XRTA" in str(axis.stage):
                axis.sendCommand("ENBL=3")

        if external_communication_thread:
            return comm

    def stop(self):
        """
        :return: None
        This function sends STOP to the controller and closes the communication.
        """
        for axis in self.getAllAxis():  # Send STOP to each axis.
            axis.sendCommand("ZERO=0")
            axis.sendCommand("STOP=0")
            axis.was_valid_DPOS = False
        self.getCommunication().closeCommunication()  # Close communication
        outputConsole("Program stopped running.")

    def stopMovements(self):
        """
        Just stop moving.
        """
        for axis in self.getAllAxis():
            axis.sendCommand("STOP=0")
            axis.was_valid_DPOS = False

    def reset(self):
        """
        :return: None
        This function sends RESET to the controller, and resends all settings.
        """
        for axis in self.getAllAxis():
            axis.reset()
        time.sleep(0.2)

        self.readSettings()  # Read settings file again

        if AUTO_SEND_SETTINGS:
            for axis in self.getAllAxis():
                axis.sendSettings()  # Update settings

    def getAllAxis(self):
        """
        :return: A list containing all axis objects belonging to this controller.
        """
        return self.axis_list

    def addAxis(self, stage, axis_letter):
        """
        :param stage: Specify the type of stage that is connected.
        :type stage: Stage
        :return: Returns an Axis object
        """
        newAxis = Axis(self, axis_letter, stage)
        self.axis_list.append(newAxis)  # Add axis to axis list.
        self.axis_letter_list.append(axis_letter)
        return newAxis

    # End User Commands
    def getCommunication(self):
        """
        :return: The communication class.
        """
        return self.comm

    def getAxis(self, letter):
        """
        :param letter: Specify the axis letter
        :return: Returns the correct axis object. Or None if the axis does not exist.
        """
        if self.axis_letter_list.count(letter) == 1:  # Axis letter found
            indx = self.axis_letter_list.index(letter)
            if len(self.getAllAxis()) > indx:
                return self.getAllAxis()[indx]  # Return axis
        return None

    def readSettings(self, external_settings_default=None):
        """
        :return: None
        This function reads the settings.txt file and processes each line.
        It first determines for what axis the setting is, then it reads the setting and saves it.
        If there are commands for axis that don't exist, it just ignores them.
        """
        try:
            if external_settings_default is None:
                file = open(SETTINGS_FILENAME, "r")
            else:
                file = open(external_settings_default, "r")

            for line in file.readlines():  # For each line:
                if "=" in line and line.find("%") != 0:  # Check if it's a command and not a comment or blank line.

                    line = line.strip("\n\r").replace(" ", "")  # Strip spaces and newlines.
                    axis = self.getAllAxis()[0]  # Default select the first axis.
                    if ":" in line:  # Check if axis is specified
                        axis = self.getAxis(line.split(":")[0])
                        if axis is None:  # Check if specified axis exists
                            continue  # No valid axis? ==> IGNORE and loop further.
                        line = line.split(":")[1]  # Strip "X:" from command
                    elif not self.isSingleAxisSystem():
                        # This line doesn't contain ":", so it doesn't specify an axis.
                        # BUT It's a multi-axis system ==> so these settings are for the master.
                        if "%" in line:  # Ignore comments
                            line = line.split("%")[0]
                        self.setMasterSetting(line.split("=")[0], line.split("=")[1], True)
                        continue

                    if "%" in line:  # Ignore comments
                        line = line.split("%")[0]

                    tag = line.split("=")[0]
                    value = line.split("=")[1]

                    axis.setSetting(tag, value, True, doNotSendThrough=True)  # Update settings for specified axis.

            file.close()  # Close file
        except FileNotFoundError as e:
            if external_settings_default is None:
                outputConsole("No settings_default.txt found.")
            else:
                raise e
            # self.stop()  # Make sure the thread also stops.
            # raise Exception(
            # "ERROR: settings_default.txt file not found. Place it in the same folder as Xeryon.py. \n "
            # "The settings_default.txt is delivered in the same folder as the Windows Interface. \n " + str(e))
        except Exception as e:
            raise e

    def setMasterSetting(self, tag, value, fromSettingsFile=False):
        """
        In multi-axis systems, commands without an axis specified are for the master.
        This function adds a setting (tag, value) to the list of settings for the master.
        """
        self.master_settings.update({tag: value})
        if not fromSettingsFile:
            self.comm.sendCommand(str(tag) + "=" + str(value))
        if "COM" in tag:
            self.setCOMPort(str(value))

    def sendMasterSettings(self, axis=False):
        """
        In multi-axis systems, commands without an axis specified are for the master.
        This function sends the stored settings to the controller;
        """
        prefix = ""
        if axis is not False:
            prefix = str(self.getAllAxis()[0].getLetter()) + ":"

        for tag, value in self.master_settings.items():
            self.comm.sendCommand(str(prefix) + str(tag) + "=" + str(value))

    def saveMasterSettings(self, axis=False):
        """
        In multi-axis systems, commands without an axis specified are for the master.
        This function saves the master settings on the controller.
        """
        if axis is None:
            self.comm.sendCommand("SAVE=0")
        else:
            self.comm.sendCommand(str(self.getAllAxis()[0].getLetter()) + ":SAVE=0")

    def setCOMPort(self, com_port):
        self.getCommunication().setCOMPort(com_port)

    def findCOMPort(self):
        """
        This function loops through every available COM-port.
        It check's if it contains any signature of Xeryon.
        :return:
        """
        if OUTPUT_TO_CONSOLE:
            print(
                "Automatically searching for COM-Port. If you want to speed things up you should manually provide it inside the controller object."
            )
        ports = list(serial.tools.list_ports.comports())
        com_port = None
        for port in ports:
            if "04D8" in str(port.hwid):
                self.setCOMPort(str(port.device))
                break


class Units(Enum):
    """
    This class is only made for making the program more readable.
    """

    mm = (0, "mm")
    mu = (1, "mu")
    nm = (2, "nm")
    inch = (3, "inches")
    minch = (4, "milli inches")
    enc = (5, "encoder units")
    rad = (6, "radians")
    mrad = (7, "mrad")
    deg = (8, "degrees")

    def __init__(self, ID, str_name):
        self.ID = ID
        self.str_name = str_name

    def __str__(self):
        return self.str_name

    def getUnit(self, str):
        for unit in Units:
            if unit.str_name in str:
                return unit
        return None


class Axis:
    axis_letter = None  # Stores the axis letter for this specific axis.
    xeryon_object = None  # Stores the "Xeryon" object.
    axis_data = None  # Stores all the data the controller sends.
    settings = None  # Stores all the settings from the settings file
    stage = None  # Specifies the type of stage used in this axis.
    units = Units.mm  # Specifies the units this axis is currently working in.
    update_nb = 0  # This number increments each time an update is recieved from the controller.
    was_valid_DPOS = False  # if True, the STEP command takes DPOS as the refrence. It's called "targeted_position=1/0" in the Microcontroller
    def_poli_value = str(DEFAULT_POLI_VALUE)

    isLogging = False  # Stores if this axis is currently "Logging": it's storing its axis_data.
    logs = {}  # This stores all the data. It's a dictionary of the form:

    previous_epos = [0, 0]  # Two samples to calculate speed
    previous_time = [0, 0]

    # { "EPOS": [...,...,...], "DPOS": [...,...,...], "STAT":[...,...,...],...}

    def findIndex(self, forceWaiting=False, direction=0):
        """
        :return: None
        This function finds the index, after finding the index it goes to the index position.
        It blocks the program until the index is found.
        """
        self.__sendCommand("INDX=" + str(direction))
        self.was_valid_DPOS = False

        if DISABLE_WAITING is False or forceWaiting is True:
            self.__waitForUpdate()  # Waits a couple of updates, so the EncoderValid flag is valid and doesn't lagg behind.
            self.__waitForUpdate()
            outputConsole("Searching index for axis " + str(self) + ".")
            while not self.isEncoderValid():  # While index not found, wait.
                if not self.isSearchingIndex():  # Check if searching for index bit is true.
                    outputConsole("Index is not found, but stopped searching for index.", True)
                    return False
                    break
                time.sleep(0.2)

        if self.isEncoderValid():
            outputConsole("Index of axis " + str(self) + " found.")
            return True

    def move(self, value):
        value = int(value)
        direction = 0
        if value > 0:
            direction = 1
        elif value < 0:
            direction = -1
        self.sendCommand("MOVE=" + str(direction))

    def setDPOS(self, value, differentUnits=None, outputToConsole=True, forceWaiting=False):
        """
        :param value: The new value DPOS has to become.
        :param differentUnits: If the value isn't specified in the current units, specify the correct units.
        :type differentUnits: Units
        :param outputToConsole: Default set to True. If set to False, this function won't output text to the console.
        :return: None
        Note: This function makes use of the sendCommand function, which is blocking the program until the position is reached.
        """
        unit = self.units  # Current units
        if differentUnits is not None:  # If the value given are in different units than the current units:
            unit = differentUnits  # Then specify the unit in differentUnits argument.

        DPOS = int(self.convertUnitsToEncoder(value, unit))  # Convert into encoder units.
        error = False

        self.__sendCommand("DPOS=" + str(DPOS))
        self.was_valid_DPOS = True  # And keep it True in order to avoid an accumulating error.
        # self.__waitForUpdate()

        # Block all futher processes until position is reached.
        if (
            DEBUG_MODE is False and DISABLE_WAITING is False or forceWaiting is True
        ):  # This check isn't nessecary in DEBUG mode or when DISABLE_WAITING is True
            # send_time = getActualTime()
            # distance = abs(int(DPOS) - int(self.getData("EPOS")))  # For calculating timeout time.

            # Wait some updates. This is so the flags (e.g. left end stop) of the previous command aren't received.
            # self.__waitForUpdate()

            # Wait until EPOS is within PTO2 AND positionReached status is received.
            while not (self.__isWithinTol(DPOS) and self.isPositionReached()):

                # Check if stage is at left end or right end. ==> out of range movement.
                if self.isAtLeftEnd() or self.isAtRightEnd():
                    # TODO: fix this so it does not go off while positioning on the limit value.
                    outputConsole("DPOS is out or range. (1) " + getDposEposString(value, self.getEPOS(), unit), True)
                    error = True
                    return False

                # # Position reached flag is set, but EPOS not within tolerance of DPOS.
                # if self.isPositionReached() and not self.__isWithinTol(DPOS):
                # # if self.isPositionReached():
                #     # Check if it's a lineair stage and DPOS is beyond it's limits.
                #     if self.stage.isLineair and (
                #             int(self.getSetting("LLIM")) > int(DPOS) or int(self.getSetting("HLIM")) < int(DPOS)):
                #         outputConsole("DPOS is out or range.(2)" + getDposEposString(value, self.getEPOS(), unit), True)
                #         error = True
                #         break

                #     # EPOS is not within tolerance of DPOS, unknown reason.
                #     outputConsole("Position not reached. (3) " + getDposEposString(value, self.getEPOS(), unit), True)
                #     error = True
                #     break

                # if self.isEncoderError():
                #     outputConsole("Position not reached. (4). Encoder gave an error.", True)
                #     error = True
                #     return False

                if self.isErrorLimit():
                    outputConsole("Position not reached. (5) ELIM Triggered.", True)
                    error = True
                    return False

                if self.isSafetyTimeoutTriggered():
                    outputConsole("Position not reached. (6) TOU2 (Timeout 2) triggered.", True)
                    error = True
                    return False

                if self.isPositionFailTriggered():
                    outputConsole(
                        "Position not reached. (8) TOU3 (Timeout 3) triggered, 'position fail' status bit 21 went high. ",
                        True,
                    )
                    error = True
                    return False

                if self.isThermalProtection1() or self.isThermalProtection2():
                    outputConsole("Position not reached. (7) amplifier error.", True)
                    error = True
                    return False

                # # This movement took too long, timeout time is estimated with speed & distance.
                # if self.__timeOutReached(send_time, distance):
                #     outputConsole(
                #         "Position not reached, timeout reached. (4) " + getDposEposString(value, self.getEPOS(), unit),
                #         True)
                #     error = True
                #     break
                # Keep polling ==> if timeout is not done, the computer will poll too fast. The microcontroller can't follow.

                time.sleep(0.01)

        if outputToConsole and error is False and DISABLE_WAITING is False:  # Output new DPOS & EPOS if necessary
            outputConsole(getDposEposString(value, self.getEPOS(), unit))

        return True

    def setTRGS(self, value):
        """
        Define the start of the trigger pulses.
        Expressed in the current units.
        :param value: Start position to trigger the pulses. Expressed in the current units.
        :return:
        """
        value_in_encoder_positions = int(self.convertUnitsToEncoder(value))
        self.sendCommand("TRGS=" + str(value_in_encoder_positions))

    def setTRGW(self, value):
        """
        Define the width of the trigger pulses.
        Expressed in the current units.
        :param value: Width of the trigger pulses. Expressed in the current units.
        :return:
        """
        value_in_encoder_positions = int(self.convertUnitsToEncoder(value))
        self.sendCommand("TRGW=" + str(value_in_encoder_positions))

    def setTRGP(self, value):
        """
        Define the pitch of the trigger pulses.
        Expressed in the current units.
        :param value: Pitch of the trigger pulses. Expressed in the current units.
        :return:
        """
        value_in_encoder_positions = int(self.convertUnitsToEncoder(value))
        self.sendCommand("TRGP=" + str(value_in_encoder_positions))

    def setTRGN(self, value):
        """
        Define the number of trigger pulses.
        :param value: Number of trigger pulses.
        :return:
        """
        self.sendCommand("TRGN=" + str(int(value)))

    def getDPOS(self):
        """
        :return: Return the desired position (DPOS) in the current units.
        """
        return self.convertEncoderUnitsToUnits(self.getData("DPOS"), self.units)

    def getUnit(self):
        """
        :return: Return the current units this stage is working in.
        """
        return self.units

    def step(self, value, forceWaiting=False):
        """
        :param value: The amount it needs to step (specified in the current units)
        If this axis has a rotating stage, this function handles the "wrapping". (Going around in a full circle)
        This function makes use of sendCommand, which blocks the program until the desired position is reached.
        """
        step = self.convertUnitsToEncoder(value, self.units)
        if self.was_valid_DPOS:
            # If the previous DPOS was valid, DPOS is taken as a refrence.
            new_DPOS = int(self.getData("DPOS")) + step
        else:
            new_DPOS = int(self.getData("EPOS")) + step

        if not self.stage.isLineair:  # Rotating Stage
            # Below is the amount of encoder units in one revolution.
            # From -180 => +180
            # -180 *(val // 180 % 2) + (val % 180)
            encoderUnitsPerRevolution = self.convertUnitsToEncoder(360, Units.deg)
            new_DPOS = -encoderUnitsPerRevolution / 2 * (new_DPOS // (encoderUnitsPerRevolution / 2) % 2) + (
                new_DPOS % (encoderUnitsPerRevolution / 2)
            )

        self.setDPOS(
            new_DPOS, Units.enc, False, forceWaiting=forceWaiting
        )  # This is used so position is checked in here.
        if DISABLE_WAITING is False:
            self.__waitForUpdate()  # Waits a couple of updates, so the EPOS is valid and doesn't lagg behind.
            outputConsole(
                "Stepped: "
                + str(self.convertEncoderUnitsToUnits(step, self.units))
                + " "
                + str(self.units)
                + " "
                + getDposEposString(self.getDPOS(), self.getEPOS(), self.units)
            )

    def getEPOS(self):
        """
        :return: Returns the EPOS in the correct units this axis is working in.
        """
        return self.convertEncoderUnitsToUnits(self.getData("EPOS"), self.units)

    def setUnits(self, units):
        """
        :param units: The units this axis needs to work in.
        :type units: Units
        """
        self.units = units

    def startLogging(self, increase_poli=True):
        """
        This function starts logging all data that the controller sends.
        It updates the POLI (Polling Interval) to get more data.
        """
        self.isLogging = True
        if increase_poli:
            self.xeryon_object.getAllAxis()[0].setSetting("POLI", "1")  # also adapt it for the master
            self.setSetting("POLI", "1")
        self.__waitForUpdate()  # To make sure the POLI is set.
        # DISABLE_WAITING isn't checked here, because it is really necessary.

    def endLogging(self, convertTimeAndEpos=False):
        """
        This function stops the logging of all the data.
        It updates the POLI (Polling Interval) back to the default value.
        """
        self.isLogging = False
        logs = self.logs  # Store logs
        self.logs = {}  # Reset logs

        # Process time & epos logs
        if convertTimeAndEpos:
            timestamps = [0]
            for i in range(1, len(logs["TIME"])):
                t = logs["TIME"][i]
                if t < logs["TIME"][i - 1]:
                    t += 2**16

                dT = (t - logs["TIME"][i - 1]) / 10  # /10 to convert to ms

                timestamps.append(round(timestamps[-1] + dT, 2))

            epos_in_units = [self.convertEncoderUnitsToUnits(pos) for pos in logs["EPOS"]]

            logs["TIME"] = timestamps
            logs["EPOS"] = epos_in_units

        self.setSetting("POLI", str(self.def_poli_value))  # Restore POLI back to default value.
        self.xeryon_object.getAllAxis()[0].setSetting("POLI", str(self.def_poli_value))  # also adapt it for the master
        return logs

    def getFrequency(self):
        return self.getData("FREQ")

    def setSetting(self, tag, value, fromSettingsFile=False, doNotSendThrough=False):
        """
        :param tag: The tag that needs to be stored
        :param value: The value
        :return: None
        This stores the settings in a list as specified in the settings file.
        """

        if fromSettingsFile:
            value = self.applySettingMultipliers(tag, value)
            if "MASS" in tag:
                tag = "CFRQ"
        if "?" not in str(value):
            self.settings.update({tag: value})
        # a change: settings are send when they are set.
        if not doNotSendThrough:
            self.__sendCommand(str(tag) + "=" + str(value))

    def startScan(self, direction, execTime=None, untilLimit=False):
        """
        :param direction: Positive or negative number.
        :param execTime: Specify the execution time in seconds. If no time is specified, it scans until scanStop() is used.
        :return:
        This function starts a scan.
        A scan is a continous movement with fixed speed. The speed is maintained by closed-loop control.
        A positive number sends the stage towards increasing encoder values.
        A negative number sends the stage towards decreasing encoder values.
        If a time is specified, the scan will go on for that amount of seconds
        If no time is specified, the scan will go on until scanStop() is ran.
        """
        self.__sendCommand("SCAN=" + str(int(direction)))
        self.was_valid_DPOS = False

        if execTime is not None:
            time.sleep(execTime)
            self.__sendCommand("SCAN=0")

        if untilLimit:  # Wait until the software limit is hit.
            self.__waitForUpdate()
            if int(direction) > 0:
                while not self.isAtRightEnd():
                    time.sleep(0.1)  # TODO: Find a better solution
            else:
                while not self.isAtLeftEnd():
                    time.sleep(0.1)  # TODO: Find a better solution

    def stopScan(self):
        """
        Stop scanning.
        """
        self.__sendCommand("SCAN=0")
        self.was_valid_DPOS = False

    def setSpeed(self, speed):
        """
        :param speed: The new speed this axis needs to operate on. The speed is specified in the current units/second.
        :type speed: int

        """
        if self.stage.isLineair:
            speed = int(
                self.convertEncoderUnitsToUnits(self.convertUnitsToEncoder(speed, self.units), Units.mu)
            )  # Convert to micrometer
        else:
            speed = self.convertEncoderUnitsToUnits(
                self.convertUnitsToEncoder(speed, self.units), Units.deg
            )  # Convert to degrees
            speed = int(speed) * 100  # *100 conversion factor.
        self.setSetting("SSPD", str(speed))

    def getSetting(self, tag):
        """
        :param tag: The tag that indicates the setting.
        :return: The value of the setting with the given tag.
        """
        return self.settings.get(tag)

    def setPTOL(self, value):
        """
        :param value: The new value for PTOL (in encoder units!)
        """
        self.setSetting("PTOL", value)

    def setPTO2(self, value):
        """
        :param value: The new value for PTO2 (in encoder units!)
        """
        self.setSetting("PTO2", value)

    def sendCommand(self, command):
        """
        :param command: the command that needs to be send.
        This function is used to let the user send commands.
        If one of the 'setting commands' are used, it is detected.
        This way the settings are saved in self.settings
        """

        tag = command.split("=")[0]
        value = str(command.split("=")[1])

        if tag in NOT_SETTING_COMMANDS:
            self.__sendCommand(command)  # These settings are not stored.
        else:
            self.setSetting(tag, value)  # These settings are stored

    def reset(self):
        """
        Reset this axis.
        """
        self.sendCommand("RSET=0")
        self.was_valid_DPOS = False

    """
        Here all the status bits are checked.
    """

    def isThermalProtection1(self, external_stat=None):
        """
        :return: True if the "Thermal Protection 1" flag is set to true.
        """
        return self.__getStatBitAtIndex(2, external_stat) == "1"

    def isThermalProtection2(self, external_stat=None):
        """
        :return: True if the "Thermal Protection 2" flag is set to true.
        """
        return self.__getStatBitAtIndex(3, external_stat) == "1"

    def isForceZero(self, external_stat=None):
        """
        :return: True if the "Force Zero" flag is set to true.
        """
        return self.__getStatBitAtIndex(4, external_stat) == "1"

    def isMotorOn(self, external_stat=None):
        """
        :return: True if the "Motor On" flag is set to true.
        """
        return self.__getStatBitAtIndex(5, external_stat) == "1"

    def isClosedLoop(self, external_stat=None):
        """
        :return: True if the "Closed Loop" flag is set to true.
        """
        return self.__getStatBitAtIndex(6, external_stat) == "1"

    def isEncoderAtIndex(self, external_stat=None):
        """
        :return: True if the "Encoder index" flag is set to true.
        """
        return self.__getStatBitAtIndex(7, external_stat) == "1"

    def isEncoderValid(self, external_stat=None):
        """
        :return: True if the "Encoder Valid" flag is set to true.
        """
        return self.__getStatBitAtIndex(8, external_stat) == "1"

    def isSearchingIndex(self, external_stat=None):
        """
        :return: True if the "Searching index" flag is set to true.
        """
        return self.__getStatBitAtIndex(9, external_stat) == "1"

    def isPositionReached(self, external_stat=None):
        """
        :return: True if the position reached flag is set to true.
        """
        return self.__getStatBitAtIndex(10, external_stat) == "1"

    def isEncoderError(self, external_stat=None):
        """
        :return: True if the "Encoder Error" flag is set to true.
        """
        return self.__getStatBitAtIndex(12, external_stat) == "1"

    def isScanning(self, external_stat=None):
        """
        :return: True if the "Scanning" flag is set to true.
        """
        return self.__getStatBitAtIndex(13, external_stat) == "1"

    def isAtLeftEnd(self, external_stat=None):
        """
        :return: True if the "Left end stop" flag is set to true.
        """
        return self.__getStatBitAtIndex(14, external_stat) == "1"

    def isAtRightEnd(self, external_stat=None):
        """
        :return: True if the "Right end stop" flag is set to true.
        """
        return self.__getStatBitAtIndex(15, external_stat) == "1"

    def isErrorLimit(self, external_stat=None):
        """
        :return: True if the "ErrorLimit" flag is set to true.
        """
        return self.__getStatBitAtIndex(16, external_stat) == "1"

    def isSearchingOptimalFrequency(self, external_stat=None):
        """
        :return: True if the "Searching Optimal Frequency" flag is set to true.
        """
        return self.__getStatBitAtIndex(17, external_stat) == "1"

    def isSafetyTimeoutTriggered(self, external_stat=None):
        """
        :return: True if the "Safety timeout triggered" flag is set to true.
        """
        return self.__getStatBitAtIndex(18, external_stat) == "1"

    def isPositionFailTriggered(self, external_stat=None):
        """
        :return: True if the "Position fail " flag is set to true.
        """
        return self.__getStatBitAtIndex(21, external_stat) == "1"

    def getLetter(self):
        """
        :return: The letter of the axis. If single axis system, it returns "X".
        """
        return self.axis_letter

    def applySettingMultipliers(self, tag, value):
        """
        Some settings have to be multiplied before it can be send to the controller.
        That's done in this function.
        :param tag:     The tag of the setting
        :param value:   The value of the setting
        :return:        Return an adjusted value for this setting.
        """
        # Apply multipliers (different units in settings file and in controller)
        if "MAMP" in tag or "MIMP" in tag or "OFSA" in tag or "OFSB" in tag or "AMPL" in tag or "MAM2" in tag:
            # Use amplitude multiplier.
            value = str(int(int(value) * self.stage.amplitudeMultiplier))
        elif "PHAC" in tag or "PHAS" in tag:
            value = str(int(int(value) * self.stage.phaseMultiplier))
        elif (
            "SSPD" in tag or "MSPD" in tag or "ISPD" in tag
        ):  # In the settigns file, SSPD is in mm/s ==> gets translated to mu/s
            value = str(int(float(value) * self.stage.speedMultiplier))
        elif "LLIM" in tag or "RLIM" in tag or "HLIM" in tag:
            # These are given in mm/deg and need to be converted to encoder units
            if self.stage.isLineair:
                value = str(self.convertUnitsToEncoder(value, Units.mm))
            else:
                value = str(self.convertUnitsToEncoder(value, Units.deg))
        elif "POLI" in tag:
            self.def_poli_value = value
        elif "MASS" in tag:
            value = str(self.__massToCFREQ(value))
        elif "ZON1" in tag or "ZON2" in tag:
            if self.stage.isLineair:
                value = str(self.convertUnitsToEncoder(value, Units.mm))
            else:
                value = str(self.convertUnitsToEncoder(value, Units.deg))
        return str(value)

    def __init__(self, xeryon_object, axis_letter, stage):
        """
        Initialize an Axis object.
        :param xeryon_object: This points to the Xeryon object.
        :type xeryon_object: Xeryon
        :param axis_letter: This specifies a specific letter to this axis.
        :type axis_letter: str
        :param stage: This specifies the stage used in this axis.
        :type stage: Stage
        """
        self.axis_letter = axis_letter
        self.xeryon_object = xeryon_object
        self.stage = stage
        self.axis_data = dict({"EPOS": 0, "DPOS": 0, "STAT": 0, "SSPD": 0, "TIME": 0})
        self.settings = dict({})
        if self.stage.isLineair:
            self.units = Units.mm
        else:
            self.units = Units.deg
        # self.settings = self.stage.defaultSettings # Load default settings

    def __massToCFREQ(self, mass):
        """
        Conversion table to change the value of the setting "MASS" into a value for the settings "CFRQ".
        :return:
        """
        mass = int(mass)
        if mass <= 50:
            return 100000
        if mass <= 100:
            return 60000
        if mass <= 250:
            return 30000
        if mass <= 500:
            return 10000
        if mass <= 1000:
            return 5000
        return 3000

    def __str__(self):
        return str(self.axis_letter)

    def __isWithinTol(self, DPOS):
        """
        :param DPOS: The desired position
        :return: True if EPOS is within PTO2 of DPOS. (PTO2 = Position Tolerance 2)
        """
        DPOS = abs(int(DPOS))
        if self.getSetting("PTO2") is not None:
            PTO2 = int(self.getSetting("PTO2"))
        elif self.getSetting("PTOL") is not None:
            PTO2 = int(self.getSetting("PTOL"))
        else:
            PTO2 = 10  # TODO
        EPOS = abs(int(self.getData("EPOS")))

        if DPOS - PTO2 <= EPOS <= DPOS + PTO2:
            return True

    def __timeOutReached(self, start_time, distance):
        """
        :param start_time:  The time the command started in ms.
        :param distance:    The distance the stage needs to travel.
        :return: True if the timeout time has been reached.
        The timeout time is calculated based on the speed (SSPD) and the distance.
        """
        t = getActualTime()
        speed = int(self.getSetting("SSPD"))
        timeout_t = distance / speed * 1000  # Convert seconds to milliseconds
        timeout_t *= 1.25  # 25% safety factor

        # For quick and tiny movements, the method above is not accurate.
        # If the timeout_t is smaller than the specified TOUT&TOU2, use TOUT+TOU2
        if self.getSetting("TOUT") is not None:
            TOUT = int(self.getSetting("TOUT")) * 3
            if TOUT > timeout_t:
                timeout_t = TOUT

        return (t - start_time) > timeout_t

    def receiveData(self, data):
        """
        :param data: The command that is received.
        :return: None
        This function processes the commands that are send to this axis.
        eg: if "EPOS=5" is send, it stores "EPOS", "5".
        If logging is enabled, this function will store the new incoming data.
        """
        if "=" in data:
            tag = data.split("=")[0]
            val = data.split("=")[1].rstrip("\n\r").replace(" ", "")

            if is_numeric(val):
                if (
                    tag not in NOT_SETTING_COMMANDS and "EPOS" not in tag and "DPOS" not in tag
                ):  # The received command is a setting that's requested.
                    self.setSetting(
                        tag, val, doNotSendThrough=True
                    )  # Do not send a received value, it can create a loop. (Setting, reading, setting)
                else:
                    self.axis_data[tag] = val

                if "STAT" in tag:
                    if self.isSafetyTimeoutTriggered():
                        outputConsole(
                            "The safety timeout was triggered (TOU2 command). "
                            "This means that the stage kept moving and oscillating around the desired position. "
                            "A reset is required now OR 'ENBL=1' should be send.",
                            True,
                        )

                    if self.isPositionFailTriggered():
                        outputConsole("Safety timeout TOU3 went off, the 'position fail' status bit went high.")

                    if (
                        self.isThermalProtection1()
                        or self.isThermalProtection2()
                        or self.isErrorLimit()
                        or self.isSafetyTimeoutTriggered()
                    ):
                        if self.isErrorLimit():
                            outputConsole(
                                "Error limit is reached (status bit 16). A reset is required now OR 'ENBL=1' should be send.",
                                True,
                            )

                        if self.isThermalProtection2() or self.isThermalProtection1():
                            outputConsole(
                                "Thermal protection 1 or 2 is raised (status bit 2 or 3). A reset is required now OR 'ENBL=1' should be send.",
                                True,
                            )

                        if self.isSafetyTimeoutTriggered():
                            outputConsole(
                                "Saftety timeout (TOU2 timeout reached) triggered. A reset is required now OR 'ENBL=1' should be send.",
                                True,
                            )

                        if AUTO_SEND_ENBL:
                            self.xeryon_object.setMasterSetting("ENBL", "1")
                            outputConsole("'ENBL=1' is automatically send.")

                if "EPOS" in tag:  # This uses "EPOS" as an indicator that a new round of data is coming in.
                    # previous_epos is a list containing two numbers: the two previous EPOS values
                    self.previous_epos = [self.previous_epos[-1], int(val)]
                    self.update_nb += 1  # This update_nb is for the function __waitForUpdate

                if self.isLogging:  # Log all received data if logging is enabled.
                    if tag not in ["SRNO", "XLS ", "XRTU", "XLA ", "XTRA", "SOFT", "SYNC"]:  # This data is useless.
                        if self.logs.get(tag) is None:
                            self.logs[tag] = []

                        self.logs[tag].append(int(val))

                if "TIME" in tag:
                    # CALCULATE SPEED
                    # previous time is a list containing the last two time samples.
                    self.previous_time = [self.previous_time[-1], int(val)]
                    t1 = self.previous_time[0]
                    t2 = int(val)
                    if t2 < t1:  # unwrap
                        t2 += 2**16

                    # calculate SSPD
                    if len(self.previous_epos) >= 2:
                        if t2 - t1 > 0:
                            self.axis_data["SSPD"] = (self.previous_epos[1] - self.previous_epos[0]) / (
                                (t2 - t1) * 10
                            )  # encoder units / ms

                            if self.isLogging:
                                if self.logs.get("SSPD") is None:
                                    self.logs["SSPD"] = []
                                self.logs["SSPD"].append(self.axis_data["SSPD"])

                    pass

    def getData(self, TAG):
        """
        :param TAG: The tag requested.
        :return: Returns the value of this tag stored, if no data it returns None.
        eg: get("DPOS") returns the value stored for "DPOS".
        """
        return self.axis_data.get(TAG)  # Returnt zelf None als TAG niet bestaat.

    def sendSettings(self):
        """
        :return: None
        This function sends ALL settings to the controller.
        """
        self.__sendCommand(
            str(self.stage.encoderResolutionCommand)
        )  # This sends: XLS =.. || XRTU=.. || XRTA=.. || XLA =..
        for tag in self.settings:
            self.__sendCommand(str(tag) + "=" + str(self.getSetting(tag)))

    def saveSettings(self):
        """
        :return: None
        This function just sends the "AXIS:SAVE" command to store the settings for this axis.
        """
        self.sendCommand("SAVE=0")

    def convertUnitsToEncoder(self, value, units=None):
        """
        :param value: The value that needs to be converted into encoder units.
        :param units: The units the value is in.
        :return: The value converted into encoder units.
        """
        if units is None:
            units = self.units
        value = float(value)
        if units == Units.mm:
            return round(value * 10**6 * 1 / self.stage.encoderResolution)
        elif units == Units.mu:
            return round(value * 10**3 * 1 / self.stage.encoderResolution)
        elif units == Units.nm:
            return round(value * 1 / self.stage.encoderResolution)
        elif units == Units.inch:
            return round(value * 25.4 * 10**6 * 1 / self.stage.encoderResolution)
        elif units == Units.minch:
            return round(value * 25.4 * 10**3 * 1 / self.stage.encoderResolution)
        elif units == Units.enc:
            return round(value)
        elif units == Units.mrad:
            return round(value * 10**3 * 1 / self.stage.encoderResolution)
        elif units == Units.rad:
            return round(value * 10**6 * 1 / self.stage.encoderResolution)
        elif units == Units.deg:
            return round(value * (2 * math.pi) / 360 * 10**6 / self.stage.encoderResolution)
        else:
            self.xeryon_object.stop()
            raise ("Unexpected unit")

    def convertEncoderUnitsToUnits(self, value, units=None):
        """
        :param value: The value (in encoder units) that needs to be converted.
        :param units:  The output unit.
        :return:  The value converted into the output unit.
        """
        if units is None:
            units = self.units
        value = float(value)
        if units == Units.mm:
            return value / (10**6 * 1 / self.stage.encoderResolution)
        elif units == Units.mu:
            return value / (10**3 * 1 / self.stage.encoderResolution)
        elif units == Units.nm:
            return value / (1 / self.stage.encoderResolution)
        elif units == Units.inch:
            return value / (25.4 * 10**6 * 1 / self.stage.encoderResolution)
        elif units == Units.minch:
            return value / (25.4 * 10**3 * 1 / self.stage.encoderResolution)
        elif units == Units.enc:
            return value
        elif units == Units.mrad:
            return value / (10**3 * 1 / self.stage.encoderResolution)
        elif units == Units.rad:
            return value / (10**6 * 1 / self.stage.encoderResolution)
        elif units == Units.deg:
            return value / ((2 * math.pi) / 360 * 10**6 / self.stage.encoderResolution)
        else:
            self.xeryon_object.stop()
            raise ("Unexpected unit")

    def __sendCommand(self, command):
        """
        :param command: The command that needs to be send.
        THIS IS A HIDDEN FUNCTION. Just to make sure that the SETTING commands are send via setSetting() and the other commands via sendCommand()
        This function is used to send a command to the controller.
        No "AXIS:" (e.g.: "X:") needs to be specified, just the command.
        """
        tag = command.split("=")[0]
        value = str(command.split("=")[1])

        prefix = ""  # In a multi axis system, prefix stores the "LETTER:".
        if not self.xeryon_object.isSingleAxisSystem():
            prefix = self.axis_letter + ":"

        # Construct and send the command.
        command = tag + "=" + str(value)
        self.xeryon_object.getCommunication().sendCommand(prefix + command)

    def __waitForUpdate(self):
        """
        This function waits a couple of update messages.
        :return:
        """
        wait_nb = 3  # This number defines how much updates need to be passed.

        # The wait number needs to adjust to POLI.
        if self.getSetting("POLI") is not None:
            wait_nb = wait_nb / int(self.def_poli_value) * int(self.getSetting("POLI"))

        start_nb = int(self.update_nb)
        while (int(self.update_nb) - start_nb) < wait_nb:
            time.sleep(0.01)  # Wait 10 ms

    def __getStatBitAtIndex(self, bit_index, external_stat=None):
        stat = self.getData("STAT")
        if external_stat is not None:
            stat = external_stat

        if stat is not None:
            bits = bin(int(stat)).replace("0b", "")[::-1]
            # [::-1 mirrors the string so the status bit numbering is the same.
            if len(bits) >= bit_index + 1:
                return bits[bit_index]
        return "0"


class Communication:
    ser = None  # Holds the serial connection.
    readyToSend = None  # List that contains commands that are ready to send.
    stop_thread = False  # Boolean for stopping the thread.
    thread = None
    xeryon_object = None  # Link to the "Xeryon" object.

    def __init__(self, xeryon_object, COM_port, baud):
        self.xeryon_object = xeryon_object
        self.COM_port = COM_port
        self.baud = baud
        self.readyToSend = []
        self.thread = None
        self.ser = None
        pass

    def start(self, external_communication_thread=False):
        """
        :return: None
        This starts the serial communication on the specified COM port and baudrate in a seperate thread.
        """
        if self.COM_port is None:
            self.xeryon_object.findCOMPort()
        if self.COM_port is None:  # No com port found
            raise Exception("No COM_port could automatically be found. You should provide it manually.")

        try:
            self.ser = serial.Serial(self.COM_port, self.baud, timeout=0.01)
            self.ser.flush()
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            if external_communication_thread is False:
                self.stop_thread = False
                self.thread = threading.Thread(target=self.__processData)
                self.thread.daemon = True
                self.thread.start()
            else:
                return self.__processData
        except Exception as e:
            outputConsole("An error occured while trying to connect to COM: " + str(self.COM_port), True, True)
            outputConsole(str(e), True, True)
            raise Exception("Could not conect to COM " + str(self.COM_port))

    def sendCommand(self, command):
        """
        :param command: The command that needs to be send.
        :return: None
        This function adds the command to the readyToSend list.
        """
        self.readyToSend.append(command)

    def setCOMPort(self, com_port):
        self.COM_port = com_port

    def __processData(self, external_while_loop=False):
        """
        :return: None
        This function is ran in a seperate thread.
        It continously listens for:
        1. If there is data to send
            Than it just writes the command.
            It strips all the new lines from the command and adds it's own.
        2. If there is data to read
            It reads the data line per line and checks if it contains "=".
            It determines the correct axis and passes that data to that axis class.
        3. Thread stop command.
        """
        try:
            while self.stop_thread is False and self.ser.is_open:  # Infinite loop

                # SEND 10 LINES, then go further to reading.
                dataToSend = list(self.readyToSend[0:10])
                self.readyToSend = self.readyToSend[10:]

                for command in dataToSend:  # Send commands.
                    self.ser.write(str.encode(command.rstrip("\n\r") + "\n"))

                max_to_read = 10
                try:
                    while self.ser.in_waiting > 0 and max_to_read > 0:  # While there is data to read
                        reading = self.ser.readline().decode()  # Read a single line

                        if "=" in reading:  # Line contains a command.

                            if len(reading.split(":")) == 2:  # check if an axis is specified
                                axis = self.xeryon_object.getAxis(reading.split(":")[0])
                                reading = reading.split(":")[1]
                                if axis is None:
                                    axis = self.xeryon_object.axis_list[0]
                                axis.receiveData(reading)

                            else:
                                # It's a single axis system
                                axis = self.xeryon_object.axis_list[0]
                                axis.receiveData(reading)

                        max_to_read -= 1
                except Exception as e:
                    print(str(e))

                if external_while_loop is True:
                    return None
            # Close the serial communication here, so we have a clean exit.
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.ser.close()
            print("Communication has stopped. ")
        except Exception as e:
            print("An error has occured that crashed the communication thread.")
            print(str(e))
            raise OSError("An error has occurred that crashed the communicaiton thread. \n" + str(e))

    def closeCommunication(self):
        self.stop_thread = True


class Stage(Enum):
    XLS_312 = (
        True,  # isLineair (True/False)
        "XLS1=312",  # Encoder Resolution Command (XLS =|XRTU=|XRTA=|XLA =)
        312.5,  # Encoder Resolution always in nanometer/microrad
        1000,
    )  # Speed multiplier

    XLS_1250 = (True, "XLS1=1251", 1250, 1000)
    XLS_1250_OLD = (True, "XLS1=1250", 1250, 1000)

    XLS_1250_OLD_2 = (True, "XLS1=1250", 312.5, 1000)

    XLS_78 = (True, "XLS1=78", 78.125, 1000)

    XLS_5 = (True, "XLS1=5", 5, 1000)

    XLS_1 = (True, "XLS1=1", 1, 1000)
    XLS_312_3N = (
        True,  # isLineair (True/False)
        "XLS3=312",  # Encoder Resolution Command (XLS =|XRTU=|XRTA=|XLA =)
        312.5,  # Encoder Resolution always in nanometer/microrad
        1000,
    )  # Speed multiplier

    XLS_1250_3N = (True, "XLS3=1251", 1250, 1000)

    XLS_1250_3N_OLD = (True, "XLS3=1250", 312.5, 1000)

    XLS_78_3N = (True, "XLS3=78", 78.125, 1000)

    XLS_5_3N = (True, "XLS3=5", 5, 1000)

    XLS_1_3N = (True, "XLS3=1", 1, 1000)

    XLA_312 = (True, "XLA1=312", 312.5, 1000)

    XLA_1250 = (True, "XLA1=1250", 1250, 1000)

    XLA_78 = (True, "XLA1=78", 78.125, 1000)

    XLA_OL = (True, "XLA1=0", 1, 1000)

    XLA_OL_3N = (True, "XLA3=0", 1, 1000)

    XLA_312_3N = (True, "XLA3=312", 312.5, 1000)

    XLA_1250_3N = (True, "XLA3=1250", 1250, 1000)

    XLA_78_3N = (True, "XLA3=78", 78.125, 1000)

    XLA_312_5N = (True, "XLA3=312", 312.5, 1000)

    XLA_1250_5N = (True, "XLA3=1250", 1250, 1000)

    XLA_78_5N = (True, "XLA3=78", 78.125, 1000)

    XLA_312_10N = (True, "XLA3=312", 312.5, 1000)

    XLA_1250_10N = (True, "XLA3=1250", 1250, 1000)

    XLA_78_10N = (True, "XLA3=78", 78.125, 1000)

    XLA_312_OLD = (True, "XLA=312", 312.5, 1000)

    XLA_1250_OLD = (True, "XLA=1250", 1250, 1000)

    XLA_78_OLD = (True, "XLA=78", 78.125, 1000)

    XRTA = (False, "XRTA=109", (2 * math.pi * 1e6) / 57600, 100)  # ?

    # TODO: CHECK RES
    # XRTU's 1N VERSION
    XRTU_40_3 = (False, "XRT1=2", (2 * math.pi * 1e6) / 2764800, 100)

    XRTU_40_19 = (False, "XRT1=18", (2 * math.pi * 1e6) / 345600, 100)
    XRTU_40_49 = (False, "XRT1=47", (2 * math.pi * 1e6) / 135000, 100)

    XRTU_40_109 = (False, "XRT1=73", (2 * math.pi * 1e6) / 86400, 100)  # CORRECT ???

    XRTU_30_3 = (False, "XRT1=3", (2 * math.pi * 1e6) / 1843200, 100)

    XRTU_30_19 = (False, "XRT1=19", (2 * math.pi * 1e6) / 360000, 100)

    XRTU_30_49 = (False, "XRT1=49", (2 * math.pi * 1e6) / 144000, 100)

    XRTU_30_109 = (False, "XRT1=109", (2 * math.pi * 1e6) / 57600, 100)

    XRTU_60_3 = (False, "XRT3=3", (2 * math.pi * 1e6) / 2073600, 100)
    XRTU_60_19 = (False, "XRT3=19", (2 * math.pi * 1e6) / 324000, 100)
    XRTU_60_49 = (False, "XRT3=49", (2 * math.pi * 1e6) / 129600, 100)
    XRTU_60_109 = (False, "XRT3=109", (2 * math.pi * 1e6) / 64800, 100)

    # For backwards compatibility

    XRTU_30_109_OLD = (False, "XRTU=109", (2 * math.pi * 1e6) / 57600, 100)
    XRTU_40_73_OLD = (False, "XRTU=73", (2 * math.pi * 1e6) / 86400, 100)
    XRTU_40_3_OLD = (False, "XRTU=3", (2 * math.pi * 1e6) / 1800000, 100)  # ?

    def __init__(self, isLineair, encoderResolutionCommand, encoderResolution, speedMultiplier):

        self.isLineair = isLineair
        self.encoderResolutionCommand = encoderResolutionCommand
        self.encoderResolution = encoderResolution  # ALTIJD IN nm / nanorad !!! ==> Verschillend met windows interface.
        self.speedMultiplier = speedMultiplier  # used.
        self.amplitudeMultiplier = AMPLITUDE_MULTIPLIER
        self.phaseMultiplier = PHASE_MULTIPLIER

    def getStage(self, stage_command):
        """
        Get stagetype by specifying "stage_command".
        'stage_command' is how the stage is specified in the config file.
        e.g.: XLS=312 or XRTU=40, ....
        :param stage_command: String containing "XLS=.." or "XRTU=..." or ...
        :return: Stagetype, or none if invalid stage command.
        """
        for stage in Stage:
            if stage_command in str(stage.encoderResolutionCommand).replace(" ", ""):
                return stage
        return None


def getActualTime():
    """
    :return: Returns the actual time in ms.
    """
    return int(round(time.time() * 1000))


def getDposEposString(DPOS, EPOS, Unit):
    """
    :return: A string containting the EPOS & DPOS value's and the current units.
    """
    return str("DPOS: " + str(DPOS) + " " + str(Unit) + " and EPOS: " + str(EPOS) + " " + str(Unit))


def outputConsole(message, error=False, force=True):
    if OUTPUT_TO_CONSOLE is True:
        if error is True:
            print("\033[91m" + "ERROR: " + message + "\033[0m")
        else:
            print(message)


def is_numeric(value):
    try:
        int(value)
        return True
    except ValueError:
        return False
    # return bool(re.match(r'^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$', value))
