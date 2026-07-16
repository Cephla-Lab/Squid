#ifndef OBJECTIVES_H
#define OBJECTIVES_H

#include "axis.h"

class Objectives : public Axis {
public:
  // Constructor
  Objectives(uint8_t csPin, uint8_t axisIndex, const char* axisName, uint8_t objectivesCount = 4);
  
  // Override the base-class init function to add filter-wheel-specific configuration
  bool begin(const AxisConfig& config) override;
  
  // Override the state-machine update to add filter-wheel-specific logic
  void update() override;
  
  // Override command processing to add filter-wheel-specific commands
  bool processCommand(const String& command) override;
  
private:
	void performHomingSequence() override;
	void performLeavingHome() override;

  uint8_t _objectivesCount;
  uint8_t _currentObjective;
  float* _objectivePositions;

  bool handleMoveToObjective(const String& command);
  bool handleSetLimits(const String& command) override;
};

#endif
