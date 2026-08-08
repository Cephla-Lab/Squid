#include "axesmrg.h"
#include "build_opt.h"
#include "filterwheel.h"
#include "illumination.h"
#include "joystick.h"
#include "trigger.h"
#include "objectives.h"
#include "serial.h"
#include "stepaxis.h"
#include "tmc/hal/TMC_SPI.h"
#include "tmc/motion/MotorControl.h"
#include "tmc/ic/TMC4361A/TMC4361A.h"
#include "utils.h"

void initializeClock(uint8_t clk_pin, uint32_t frequence) {
  pinMode(clk_pin, OUTPUT);
  analogWriteFrequency(clk_pin, frequence);
  analogWrite(clk_pin, 128);
}

void initializeSPIAndPins() {
  // Disable all axes
  for (uint8_t i = 0; i < sizeof(Pins::CONTROL_PINS); i++) {
    pinMode(Pins::CONTROL_PINS[i], OUTPUT);
    digitalWrite(Pins::CONTROL_PINS[i], HIGH);
  }

  for (uint8_t i = 0; i < sizeof(Pins::STANDARD_CONTROL_PINS); i++) {
    pinMode(Pins::STANDARD_CONTROL_PINS[i], OUTPUT);
    digitalWrite(Pins::STANDARD_CONTROL_PINS[i], HIGH);
  }

  // Initialize SPI
  SPI.begin();
  delay(50); // 50ms delay, using explicit time units
}

bool initializePowerManagement() {
  pinMode(Pins::POWER_GOOD, INPUT_PULLUP);

  // Disable the DAC pins
  pinMode(Pins::DAC8050x_CS, OUTPUT);
  digitalWrite(Pins::DAC8050x_CS, HIGH);

  delay(100);

  // Wait for power to be ready
  unsigned long startTime = millis();
  while (!digitalRead(Pins::POWER_GOOD)) {
    if (millis() - startTime > 5000) { // 5-second timeout
      DEBUG_PRINTLN("Power management initialization timeout");
      return false;
    }
    delay(50);
  }

  return true;
}

bool initializeSystem() {
  // Initialize power management
  if (!initializePowerManagement()) {
    return false;
  }

  // Initialize the clock
  initializeClock(Pins::TMC4361_STANDARD_CLK,
                  SystemConfig::TMC4361_CLOCK_FREQUENCY);
  initializeClock(Pins::TMC4361_EXPAND_CLK,
                  SystemConfig::TMC4361_CLOCK_FREQUENCY);

  // Initialize SPI and pins
  initializeSPIAndPins();

  // Initialize the illumination system (pins, LED matrix, DAC, interlock)
  illumination_init();

  // Initialize the trigger system (pins, strobe timer)
  trigger_init();

  // Initialize the new-architecture motion-control subsystem
  motor_initSubsystem();

  // Create axis objects and add them to the manager
  //
  // Important (2026-05-08 fix): the axisName <-> CS pin mapping is aligned with the legacy Squid hardware wiring
  //
  // legacy Squid firmware internal axis index vs protocol axis number mapping (def_v1.h:11-21):
  //   Protocol: AXIS_X=0, AXIS_Y=1
  // Internal: x=1, y=0  (the comment explicitly says "Internal indices match hardware wiring")
  // -> legacy Squid hardware actual wiring:
  // pin_TMC4361_CS[0]=41 -> physical Y motor (because internal y=0)
  // pin_TMC4361_CS[1]=36 -> physical X motor (because internal x=1)
  //
  // Therefore axisName="X" must be bound to CS=36 (Pins::Y_AXIS_CS) to correctly drive the physical X motor.
  // Previously axisName="X" + Pins::X_AXIS_CS=41 -> actually drove the physical Y motor,
  // causing the legacy Squid jog-X freeze (X moving to 79.9mm actually triggered the physical Y limit).
  //
  // axisIndex (icID) is just the internal array index and does not affect the physical CS mapping.
  Axis *xAxis  = new StepAxis  (Pins::Y_AXIS_CS,  0, "X");   // CS=36 = physical X motor
  Axis *yAxis  = new StepAxis  (Pins::X_AXIS_CS,  1, "Y");   // CS=41 = physical Y motor
  Axis *zAxis  = new StepAxis  (Pins::Z_AXIS_CS,  2, "Z");
  Axis *wAxis  = new FilterWheel(Pins::W_AXIS_CS,  3, "W");
  // W2 = the second filter wheel, taking over the original E4 hardware (CS=pin 16, CLK=pin 28 = TMC4361_EXPAND_CLK),
  // fully consistent with legacy Squid pin_TMC4361_CS[4]=16 / pin_TMC4361_CLK_W2=28.
  // the board may be absent: axesmrg.cpp::beginAll deletes + nullptrs this slot when SPI does not respond,
  // so all W2 handlers' if (axis) guards turn commands into a silent no-op without affecting other axes.
  Axis *w2Axis = new FilterWheel(Pins::W2_AXIS_CS, 4, "W2");
  // E1 = objective changer (4 objectives), connected to the EXPAND1 hardware (CS=pin 19, CLK=pin 28 = TMC4361_EXPAND_CLK).
  // 2026-05-29: on this board the icID=5 slot is the objective changer (the W filter wheel is left unchanged).
  // the protocol uses dedicated MOVE_TURRET/MOVETO_TURRET + HOME_OR_ZERO axis=7 (protocolAxisToName case 7 -> "Turret").
  // the board may be absent: axesmrg.cpp::beginAll deletes + nullptrs this slot when SPI does not respond.
  Axis *turretAxis = new Objectives (Pins::EXPAND1_AXIS_CS, 5, "Turret", 4);

  // add in axisIndex order: X(0), Y(1), Z(2), W(3), W2(4), E1(5)
  if (!axisManager.addAxis(xAxis)  || !axisManager.addAxis(yAxis)  ||
      !axisManager.addAxis(zAxis)  || !axisManager.addAxis(wAxis)  ||
      !axisManager.addAxis(w2Axis) || !axisManager.addAxis(turretAxis)) {
    DEBUG_PRINTLN("Failed to add axes to manager");
    return false;
  }

  // Initialize all axes
  // Note: beginAll() returning false means **at least one axis failed begin** (typical case:
  // TMC4361A SPI not responding, so after motor_initMotionController writes SW_RESET, reading
  // VERSION_NO returns 0/-1). **No longer treated as fatal** -- serial communication and debug commands
  // (S:VERSION / S:HWINFO / S:DUMPREGS) must remain available, otherwise the SPI failure root cause cannot be diagnosed
  // on-site. The failed axis is already identified by axis.cpp's DEBUG_PRINT(_axisName +
  // ":BEGIN_FAIL ...") printed to the serial port.
  if (!axisManager.beginAll()) {
    DEBUG_PRINTLN("WARNING: beginAll() reported partial axis failure (see :BEGIN_FAIL above). Continuing so serial diagnostics remain available.");
  }

  // Initialize the hand controller (Serial5 + PacketSerial)
  joystick_init();

  return true;
}

void setup() {
  // Initialize the serial port
  serialProtocol.begin(115200, 300);

  // Initialize the status indicator LED
  initializeStartupLED();

  // clear the APA102 matrix as early as possible to minimize the "startup glow" window.
  // the subsequent initializePowerManagement (waiting for PG) + delay + clock + SPI init
  // may total hundreds of ms to 5s, during which the APA102 stays in its power-on default lit state.
  illumination_init_matrix_early();

  DEBUG_PRINTLN("Initializing system...");

  // Initialize the system
  if (!initializeSystem()) {
    DEBUG_PRINTLN("System initialization failed!");
    while (1) {
      delay(1000); // halt execution
    }
  }

  DEBUG_PRINTLN("System initialized successfully");
}

void loop() {
  static bool firstLoop = true;
  if (firstLoop) {
    DEBUG_PRINTLN("MAIN_LOOP_ENTERED");  // confirm entry into the main loop
    firstLoop = false;
  }

  // Safety interlock check: when the interlock opens, directly pull the TTL laser ports low (hardcoded GPIO, zero overhead)
  if (!illumination_interlock_ok()) {
    digitalWrite(Pins::ILLUMINATION_D1, LOW);
    digitalWrite(Pins::ILLUMINATION_D2, LOW);
    digitalWrite(Pins::ILLUMINATION_D3, LOW);
    digitalWrite(Pins::ILLUMINATION_D4, LOW);
    digitalWrite(Pins::ILLUMINATION_D5, LOW);
  }

  // Serial watchdog: automatically turn off all illumination after a communication-loss timeout
  watchdog_check();

  // Update trigger-pulse recovery
  trigger_update();

  // Process serial debug commands
  serialProtocol.processSerialCommands();

  // 10ms periodic position reporting (compatible with the legacy Squid protocol)
  serialProtocol.send_position_update();

  // Update the hand controller (PacketSerial receive + joystick/focus-wheel control)
  joystick_update();

  // Update all axis state machines
  axisManager.updateAll();

}
