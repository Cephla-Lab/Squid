#ifndef AXES_MANAGER_H
#define AXES_MANAGER_H

#include <Arduino.h>
#include "axis.h"
#include "config.h"

class AxisManager {
private:
  static const uint8_t MAX_AXES = 8;  // supports up to 8 axes
  Axis* axes[MAX_AXES];              // array of axis object pointers
  uint8_t axisCount;                 // current number of axes
  
public:
  AxisManager();
  ~AxisManager();
  
  // Add an axis to the manager
  bool addAxis(Axis* axis);

  // Initialize all axes
  bool beginAll();

  // Update all axis state machines
  void updateAll();

  // Process a serial command
  bool processCommand(const String& command);

  // Get the number of axes
  uint8_t getAxisCount() const { return axisCount; }

  // Get an axis by index
  Axis* getAxis(uint8_t index);

  // Find an axis object by name
  Axis* findAxisByName(const String& axisName);
};

extern AxisManager axisManager;  // global axis manager instance

#endif
