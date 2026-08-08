#ifndef FILTER_WHEEL_H
#define FILTER_WHEEL_H

#include "axis.h"

class FilterWheel : public Axis {
public:
  // Constructor
  FilterWheel(uint8_t csPin, uint8_t axisIndex, const char* axisName, uint8_t filterCount = 8);
  
  // Override the base-class init function to add filter-wheel-specific configuration
  bool begin(const AxisConfig& config) override;
  
  // Filter-wheel-specific features
  bool moveToFilter(uint8_t filterPosition);
  uint8_t getCurrentFilter() const;
  uint8_t getFilterCount() const;
  
  // Override the state-machine update to add filter-wheel-specific logic
  void update() override;
  
  // Override command processing to add filter-wheel-specific commands
  bool processCommand(const String& command) override;
  
  // Set the filter-wheel position mapping
  void setFilterPositions(const float* positions, uint8_t count);
  
private:
	void performHomingSequence() override;
	void performLeavingHome() override;

  uint8_t _filterCount;
  uint8_t _currentFilter;
  float* _filterPositions; // position (mm) of each filter
  bool _slowApproach;      // two-phase homing flag: false=fast search for the sensing zone, true=slow precise approach
  
  // Filter-wheel-specific methods
	bool handleSetLimits(const String& command) override;

  bool handleMoveToFilter(const String& command);
  float getFilterPosition(uint8_t filterIndex) const;
  bool isValidFilterPosition(uint8_t filterPosition) const;
};

#endif
