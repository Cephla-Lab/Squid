#pragma once

// Bench-only self-test of the hardware sequencer: runs a canned program every 2 s with no
// host involved, so trigger / illumination / stack-axis timing can be put on a scope before
// any host code exists. NEVER ship a build with SEQ_SELFTEST defined.
//
//   PLATFORMIO_BUILD_FLAGS="-D SEQ_SELFTEST -D SEQ_SELFTEST_STACK_DAC=7" \
//       pio run -e teensy41_newctrl -t upload
//
// Required:  SEQ_SELFTEST_STACK_DAC=<0..7>  DAC80508 channel stepped as the stack axis. 7 is
//            the real objective piezo — it WILL move (about 1 um per layer around mid-range).
//            The build refuses to guess which output to drive.
// Optional:  SEQ_SELFTEST_TTL_MASK=<mask>   TTL ports strobed during exposures (bit 0 = D1).
//            Default 0: no light. Only set this with the lasers disconnected or safe.
//            SEQ_SELFTEST_READY_LINE        gate on the camera trigger-ready input (needs a
//            controller profile that has one, and a camera driving it).
#ifdef SEQ_SELFTEST
void seq_selftest_tick();  // call once per loop() pass
#endif
