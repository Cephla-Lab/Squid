#ifndef TEST_STUB_ARDUINO_H
#define TEST_STUB_ARDUINO_H

/*
  Minimal host shim for <Arduino.h>, used ONLY by env:native.

  tmc2240.cpp and driver_probe.cpp include <Arduino.h> for delayMicroseconds()
  and nothing else, so this shim carries exactly that one symbol. It is reached
  through a native-only include path (-I test/test_driver_sequence/stubs in
  [env:native] build_flags); env:teensy41 never sees this directory and keeps
  using the real Arduino core.

  The delay is accumulated rather than discarded because the settle times are
  part of the behaviour under test: tmc_driver_probe() must wait ~500 us after
  switching SPI_OUTPUT_FORMAT and ~50 us after each cover trigger, and a
  regression that drops them would otherwise be invisible on the host.

  The counter is DEFINED by test_driver_sequence.cpp. Any other native
  translation unit that includes this header without defining it will simply
  never emit the inline function, so no link error is possible for tests that
  do not use it.
*/

#include <stdint.h>

extern unsigned long tmc_test_delay_us_total;

static inline void delayMicroseconds(unsigned int us)
{
    tmc_test_delay_us_total += us;
}

#endif /* TEST_STUB_ARDUINO_H */
