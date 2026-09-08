#ifndef INCLUDED_BUILD_OPT_H
#define INCLUDED_BUILD_OPT_H

#define ENABLE_LED_INDICATOR

// ENABLE_DEBUG is only enabled in debug builds (the platformio.ini debug env passes -D DEBUG)
// Production builds (teensy41) do not define DEBUG; the serial port outputs only binary protocol packets, no ASCII text
#ifdef DEBUG
  #define ENABLE_DEBUG
#endif

#ifdef ENABLE_DEBUG
  #define DEBUG_PRINT(x) SerialUSB.print(x)
  #define DEBUG_PRINTLN(x) SerialUSB.println(x)
  #define DEBUG_PRINTF(x, y) SerialUSB.print(x, y)
  #define DEBUG_PRINTLNF(x, y) SerialUSB.println(x, y)
#else
  #define DEBUG_PRINT(x)
  #define DEBUG_PRINTLN(x)
  #define DEBUG_PRINTF(x, y)
  #define DEBUG_PRINTLNF(x, y)
#endif

// Uncomment the line below -> temporarily disable the 24-byte binary position reporting so SerialUSB outputs only ASCII,
// making it easy to view DEBUG_PRINT output in the Arduino IDE Serial Monitor / a plain terminal
// without binary garbage interfering. Note: once disabled the host cannot receive position reports and cannot connect.
// === Enable when debugging [FOCUS] / other ASCII output; comment back out when done ===
// #define DISABLE_BINARY_POS_UPDATE

#endif /* INCLUDED_BUILD_OPT_H */
