#ifndef STEP_AXIS_H
#define STEP_AXIS_H

#include "axis.h"

class StepAxis : public Axis {
public:
  // Constructor
  StepAxis(uint8_t csPin, uint8_t axisIndex, const char* axisName);
  
  // Override the base-class init function to add stepper-axis-specific configuration
  bool begin(const AxisConfig& config) override;
  
  // Stepper-axis-specific features
  void setBacklashCompensation(float backlashMM);
  void enableBacklashCompensation(bool enable);
  
  // Override the motion-control functions to add stepper-axis-specific logic
  bool moveToPosition(float positionMM) override;
  bool moveRelative(float distanceMM) override;
  
  virtual bool handleSetLimits(const String& command) override;

private:
  float _backlashMM;
  bool _backlashCompensationEnabled;
  
  // Stepper-axis-specific methods
  void applyBacklashCompensation(int32_t direction);

	void performHomingSequence() override;
	void performLeavingHome() override;
};

#endif

