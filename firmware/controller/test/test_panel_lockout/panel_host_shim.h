#ifndef TEST_PANEL_HOST_SHIM_H
#define TEST_PANEL_HOST_SHIM_H

/*
  Host stand-ins for the Arduino / Teensy core, for test_panel_lockout ONLY.

  test_panel_lockout.cpp includes this header BEFORE it includes the firmware
  sources, because the <Arduino.h> that env:native resolves is
  test/test_driver_sequence/stubs/Arduino.h (delayMicroseconds and nothing
  else) and globals.h / constants.h / functions.cpp need more of the core than
  that. <FastLED.h> and <PacketSerial.h> resolve to this directory's stubs/
  (-I test/test_panel_lockout/stubs in [env:native]); they are empty because
  the types live here.

  Nothing here models hardware. The joystick paths under test reach the motor
  only through tmc4361A_setSpeed() / tmc4361A_stop(), which the test replaces
  with recorders; everything else only has to compile and do nothing.
*/

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

typedef uint8_t byte;

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define MSBFIRST 1
#define SPI_MODE0 0
#define SPI_MODE2 2
#define SPI_MODE3 3

static inline int digitalRead(int) { return LOW; }
static inline void digitalWrite(int, int) {}
static inline void digitalWriteFast(int, int) {}
static inline void pinMode(int, int) {}
static inline void analogWrite(int, int) {}
static inline void analogWriteResolution(int) {}
static inline void analogWriteFrequency(int, float) {}
static inline void delay(unsigned long) {}
static inline unsigned long micros() { return 0; }
static inline unsigned long millis() { return 0; }

/* Assignable and readable like the Teensy type; the test sets it to say whether
   check_joystick()'s tick is due. */
class elapsedMicros
{
public:
  elapsedMicros() : us(0) {}
  elapsedMicros(unsigned long v) : us(v) {}
  operator unsigned long() const { return us; }
  elapsedMicros &operator=(unsigned long v) { us = v; return *this; }
  unsigned long us;
};
typedef elapsedMicros elapsedMillis;

class IntervalTimer
{
public:
  bool begin(void (*)(), unsigned long) { return true; }
  void end() {}
};

struct HostPrint
{
  template <typename T> void print(T) {}
  template <typename T> void print(T, int) {}
  template <typename T> void println(T) {}
  template <typename T> void println(T, int) {}
  void println() {}
  void begin(unsigned long) {}
  size_t write(const uint8_t *, size_t n) { return n; }
  int available() { return 0; }
  int read() { return -1; }
  void flush() {}
};
static HostPrint Serial, SerialUSB, Serial5, Serial8;

struct SPISettings
{
  SPISettings(uint32_t, uint8_t, uint8_t) {}
};
struct HostSPI
{
  void begin() {}
  void beginTransaction(SPISettings) {}
  void endTransaction() {}
  uint8_t transfer(uint8_t) { return 0; }
  uint16_t transfer16(uint16_t) { return 0; }
};
static HostSPI SPI;

struct CRGB
{
  uint8_t r, g, b;
  CRGB() : r(0), g(0), b(0) {}
  CRGB(int v) : r(v), g(v), b(v) {}
  CRGB(uint8_t r_, uint8_t g_, uint8_t b_) : r(r_), g(g_), b(b_) {}
  void setRGB(uint8_t r_, uint8_t g_, uint8_t b_) { r = r_; g = g_; b = b_; }
};
struct HostFastLED
{
  void show() {}
  void clear() {}
  void setBrightness(uint8_t) {}
};
static HostFastLED FastLED;

class PacketSerial
{
public:
  typedef void (*Handler)(const uint8_t *, size_t);
  void setStream(HostPrint *) {}
  void setPacketHandler(Handler) {}
  void update() {}
  void send(const uint8_t *, size_t) {}
};

#endif /* TEST_PANEL_HOST_SHIM_H */
