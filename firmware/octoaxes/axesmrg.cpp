#include "axesmrg.h"
#include "build_opt.h"

AxisManager axisManager;  // define the global instance

AxisManager::AxisManager() {
  axisCount = 0;
  // initialize the pointer array to nullptr
  for (uint8_t i = 0; i < MAX_AXES; i++) {
    axes[i] = nullptr;
  }
}

AxisManager::~AxisManager() {
  // release resources
  for (uint8_t i = 0; i < axisCount; i++) {
    if (axes[i] != nullptr) {
      delete axes[i];
      axes[i] = nullptr;
    }
  }
}

bool AxisManager::addAxis(Axis* axis) {
  if (axisCount >= MAX_AXES || axis == nullptr) {
    DEBUG_PRINTLN("Cannot add axis: maximum limit reached or null axis");
    return false;
  }
  
  axes[axisCount] = axis;
  axisCount++;
  
  DEBUG_PRINT("Axis added: ");
  DEBUG_PRINTLN(axis->getAxisName());  // fix: use the correct function name getAxisName()
  DEBUG_PRINT("Total axes: ");
  DEBUG_PRINTLN(axisCount);
  
  return true;
}

bool AxisManager::beginAll() {
  DEBUG_PRINTLN("beginAll:START");  // debug point
  bool allSuccess = true;

  for (uint8_t i = 0; i < axisCount; i++) {
    if (axes[i] != nullptr) {
      bool success = false;

      // select the matching config by axis name
      String axisName = String(axes[i]->getAxisName());  // fix: use getAxisName() and convert to String

      DEBUG_PRINT("beginAll:INIT_AXIS:");
      DEBUG_PRINTLN(axisName);  // debug point

      // fix: use the equals() method for string comparison
      if (axisName.equals("X")) {
        success = axes[i]->begin(AxisConfigs::X_AXIS);
      } else if (axisName.equals("Y")) {
        success = axes[i]->begin(AxisConfigs::Y_AXIS);
      } else if (axisName.equals("Z")) {
        success = axes[i]->begin(AxisConfigs::Z_AXIS);
      } else if (axisName.equals("W")) {
        success = axes[i]->begin(AxisConfigs::W_AXIS);
      } else if (axisName.equals("Turret")) {
        success = axes[i]->begin(AxisConfigs::EXPAND1_AXIS);
      } else if (axisName.equals("E3")) {
        success = axes[i]->begin(AxisConfigs::EXPAND3_AXIS);
      } else if (axisName.equals("E4")) {
        success = axes[i]->begin(AxisConfigs::EXPAND4_AXIS);
      } else if (axisName.equals("W2")) {
        // W2 = the second filter wheel, reusing the EXPAND4_AXIS config (filter wheel + invert_direction=true).
        // Paired with the legacy Squid protocol (AXIS_W2=6 / MOVE_W2=19 / INITFILTERWHEEL_W2=252).
        success = axes[i]->begin(AxisConfigs::EXPAND4_AXIS);
      } else {
        DEBUG_PRINT("Unknown axis configuration for: ");
        DEBUG_PRINTLN(axisName);
        success = false;
      }

      DEBUG_PRINT("beginAll:AFTER_BEGIN:");
      DEBUG_PRINTLN(axisName);  // debug point

      if (!success) {
        DEBUG_PRINT("Failed to initialize axis: ");
        DEBUG_PRINTLN(axisName);
        // Compatibility: a begin() failure means the TMC4361A SPI did not respond (board not plugged in /
        // chip damaged / broken wiring). Delete this Axis instance and set the slot to nullptr so that later
        // findAxisByName returns nullptr, and every handler's if (axis) guard turns the command into a silent
        // no-op (the response packet reports any_moving=false and COMPLETED immediately, so the host's
        // wait_till_operation_is_completed wakes up at once).
        // This avoids later SPI operations hitting a dead chip, wasting the bus, and producing false-positive status.
        delete axes[i];
        axes[i] = nullptr;
        allSuccess = false;
      } else {
        DEBUG_PRINT("Successfully initialized axis: ");
        DEBUG_PRINTLN(axisName);
      }
    }
  }

  return allSuccess;
}

void AxisManager::updateAll() {
  for (uint8_t i = 0; i < axisCount; i++) {
    if (axes[i] != nullptr) {
      axes[i]->update();
    }
  }
}

Axis* AxisManager::findAxisByName(const String& axisName) {
  for (uint8_t i = 0; i < axisCount; i++) {
    // fix: use getAxisName() and convert to String for comparison
    if (axes[i] != nullptr && String(axes[i]->getAxisName()).equals(axisName)) {
      return axes[i];
    }
  }
  return nullptr;
}

bool AxisManager::processCommand(const String& command) {
  DEBUG_PRINT("AxisMgr:CMD:");
  DEBUG_PRINTLN(command);  // debug point A - command received

  // Command format: "axisName:commandBody", e.g. "E3:HOMING"
  int colonIndex = command.indexOf(':');

  if (colonIndex == -1) {
    DEBUG_PRINTLN("Invalid command format. Expected: AXIS:COMMAND");
    return false;
  }

  String axisName = command.substring(0, colonIndex);
  String cmd = command.substring(colonIndex + 1);

  axisName.trim();
  cmd.trim();

  DEBUG_PRINT("AxisMgr:AXIS=");
  DEBUG_PRINT(axisName);
  DEBUG_PRINT(",CMD=");
  DEBUG_PRINTLN(cmd);  // debug point B - parse result

  if (axisName.length() == 0 || cmd.length() == 0) {
    DEBUG_PRINTLN("Empty axis name or command");
    return false;
  }

  // find the matching axis
  DEBUG_PRINT("AxisMgr:FIND_AXIS,count=");
  DEBUG_PRINTLN(axisCount);  // debug point C - axis count

  Axis* targetAxis = findAxisByName(axisName);
  if (targetAxis == nullptr) {
    DEBUG_PRINT("Axis not found: ");
    DEBUG_PRINTLN(axisName);
    return false;
  }

  DEBUG_PRINTLN("AxisMgr:AXIS_FOUND");  // debug point D - axis found

  // forward the command to the matching axis for handling
  bool success = targetAxis->processCommand(cmd);
  
  if (success) {
    DEBUG_PRINT("Command '");
    DEBUG_PRINT(cmd);
    DEBUG_PRINT("' sent to axis ");
    DEBUG_PRINTLN(axisName);
  } else {
    DEBUG_PRINT("Failed to process command '");
    DEBUG_PRINT(cmd);
    DEBUG_PRINT("' on axis ");
    DEBUG_PRINTLN(axisName);
  }
  
  return success;
}

Axis* AxisManager::getAxis(uint8_t index) {
  if (index < axisCount) {
    return axes[index];
  }
  return nullptr;
}
