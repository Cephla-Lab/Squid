#ifndef COMMAND_PROCESSOR_H
#define COMMAND_PROCESSOR_H

#include <Arduino.h>

class CommandProcessor {
public:
    CommandProcessor();
    ~CommandProcessor();
    
    // Command-handler function declarations
    void handleMoveX(const byte* data);
    void handleMoveY(const byte* data);
    void handleMoveZ(const byte* data);
    void handleMoveTheta(const byte* data);
    void handleMoveW(const byte* data);
    void handleHomeOrZero(const byte* data);
    void handleMoveToX(const byte* data);
    void handleMoveToY(const byte* data);
    void handleMoveToZ(const byte* data);
    void handleSetLim(const byte* data);
    void handleTurnOnIllumination(const byte* data);
    void handleTurnOffIllumination(const byte* data);
    void handleSetIllumination(const byte* data);
    void handleSetIlluminationLEDMatrix(const byte* data);
    void handleAckJoystickButtonPressed(const byte* data);
    void handleAnalogWriteOnboardDAC(const byte* data);
    void handleSetDAC80508RefDivGain(const byte* data);
    void handleSetIlluminationIntensityFactor(const byte* data);
    void handleSetPortIntensity(const byte* data);
    void handleTurnOnPort(const byte* data);
    void handleTurnOffPort(const byte* data);
    void handleSetPortIllumination(const byte* data);
    void handleSetMultiPortMask(const byte* data);
    void handleTurnOffAllPorts(const byte* data);
    void handleMoveW2(const byte* data);
    void handleMoveTurret(const byte* data);
    void handleMoveToTurret(const byte* data);
    void handleSetTriggerMode(const byte* data);
    void handleMoveToW(const byte* data);
    void handleSetLimSwitchPolarity(const byte* data);
    void handleConfigureStepperDriver(const byte* data);
    void handleSetMaxVelocityAcceleration(const byte* data);
    void handleSetLeadScrewPitch(const byte* data);
    void handleSetOffsetVelocity(const byte* data);
    void handleConfigureStagePID(const byte* data);
    void handleEnableStagePID(const byte* data);
    void handleDisableStagePID(const byte* data);
    void handleSetHomeSafetyMargin(const byte* data);
    void handleSetPIDArguments(const byte* data);
    void handleSendHardwareTrigger(const byte* data);
    void handleSetStrobeDelay(const byte* data);
    void handleSetAxisDisableEnable(const byte* data);
    void handleSetWatchdogTimeout(const byte* data);
    void handleSetPinLevel(const byte* data);
    void handleHeartbeat(const byte* data);
    void handleInitFilterWheel(const byte* data);
    void handleInitFilterWheelW2(const byte* data);
    void handleInitialize(const byte* data);
    void handleReset(const byte* data);
    
private:
    // Private member variables (add as needed)
};

extern CommandProcessor commandProcessor;

#endif
