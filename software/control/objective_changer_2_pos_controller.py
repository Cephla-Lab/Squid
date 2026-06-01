import os
import sys
import time
import serial

from control.Xeryon import Xeryon, Stage
from control._def import *  # to remove once we create ObjectiveChangerConfig
import squid.abc
from typing import Optional


class ObjectiveChanger2PosController:
    def __init__(self, sn: str, stage: Optional[squid.abc.AbstractStage] = None):
        super().__init__()
        port = [p.device for p in serial.tools.list_ports.comports() if sn == p.serial_number]
        self.controller = Xeryon(port[0], 115200)
        self.axisX = self.controller.addAxis(Stage.XLA_1250_3N, "Z")
        self.controller.start()
        self.controller.reset()

        self.stage = stage

        self.position1 = -19
        self.position2 = 19

        self.current_position = None
        self.retracted = False  # moved down by self.position2_offset for position 2

        self.position2_offset = XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM

    def home(self):
        self.axisX.stopScan()
        self.axisX.findIndex()

    def moveToZero(self):
        self.axisX.setDPOS(0)
        self.current_position = 0

    def moveToPosition1(self, move_z=True):
        self.axisX.setDPOS(self.position1)
        if self.stage is not None and self.current_position == 2 and self.retracted:
            # revert retracting z by self.position2_offset
            if move_z:
                self.stage.move_z(self.position2_offset)
            self.retracted = False
        self.current_position = 1

    def moveToPosition2(self, move_z=True):
        self.axisX.setDPOS(self.position2)
        if self.stage is not None and self.current_position == 1:
            # retract z by self.position2_offset
            if move_z:
                self.stage.move_z(-self.position2_offset)
            self.retracted = True
        self.current_position = 2

    def currentPosition(self) -> int:
        return self.current_position

    def setSpeed(self, value: float):
        self.axisX.setSpeed(value)

    def move_to_objective(self, objective_name: str) -> None:
        """Unified dispatcher to moveToPosition1 / moveToPosition2 based on the
        per-machine XERYON_OBJECTIVE_SWITCHER_POS_1/POS_2 lists. Short-circuits
        if already at the target position. Raises KeyError for unknown names."""
        from control._def import XERYON_OBJECTIVE_SWITCHER_POS_1, XERYON_OBJECTIVE_SWITCHER_POS_2

        if objective_name in XERYON_OBJECTIVE_SWITCHER_POS_1:
            if self.currentPosition() != 1:
                self.moveToPosition1()
        elif objective_name in XERYON_OBJECTIVE_SWITCHER_POS_2:
            if self.currentPosition() != 2:
                self.moveToPosition2()
        else:
            raise KeyError(f"Unknown objective '{objective_name}' for Xeryon 2-pos changer")


class ObjectiveChanger2PosController_Simulation:
    def __init__(self, sn: str, stage: Optional[squid.abc.AbstractStage] = None):
        super().__init__()

        self.stage = stage

        self.position1 = -19
        self.position2 = 19

        self.current_position = None
        self.retracted = False  # moved down by self.position2_offset for position 2

        self.position2_offset = XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM

    def home(self):
        pass

    def moveToPosition1(self, move_z=True):
        if self.stage is not None and self.current_position == 2 and self.retracted:
            # revert retracting z by self.position2_offset
            self.stage.move_z(self.position2_offset)
            self.retracted = False
        self.current_position = 1

    def moveToPosition2(self, move_z=True):
        if self.stage is not None and self.current_position == 1:
            # retract z by self.position2_offset
            self.stage.move_z(-self.position2_offset)
            self.retracted = True
        self.current_position = 2

    def currentPosition(self) -> int:
        return self.current_position

    def setSpeed(self, value: float):
        pass

    def move_to_objective(self, objective_name: str) -> None:
        """Unified dispatcher to moveToPosition1 / moveToPosition2 based on the
        per-machine XERYON_OBJECTIVE_SWITCHER_POS_1/POS_2 lists. Short-circuits
        if already at the target position. Raises KeyError for unknown names."""
        from control._def import XERYON_OBJECTIVE_SWITCHER_POS_1, XERYON_OBJECTIVE_SWITCHER_POS_2

        if objective_name in XERYON_OBJECTIVE_SWITCHER_POS_1:
            if self.currentPosition() != 1:
                self.moveToPosition1()
        elif objective_name in XERYON_OBJECTIVE_SWITCHER_POS_2:
            if self.currentPosition() != 2:
                self.moveToPosition2()
        else:
            raise KeyError(f"Unknown objective '{objective_name}' for Xeryon 2-pos changer")
