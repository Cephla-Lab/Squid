#ifndef TEST_STUB_SPI_H
#define TEST_STUB_SPI_H

/*
  Empty host shim for <SPI.h>, used ONLY by env:native.

  src/tmc/TMC4361A_Utils.h includes <SPI.h>, and both tmc2660.cpp and
  tmc2240.cpp include that header for the tmc4361A_* primitives they call.
  Nothing in the three modules under test touches an SPI object — every bus
  access goes through tmc4361A_writeInt/readInt/setBits/rstBits, which the test
  replaces with recorders — so an empty header is enough to make the
  translation unit compile on the host.

  Deliberately empty rather than a fake SPIClass: anything declared here that
  the drivers started using would compile on the host and then behave
  differently on the board.
*/

#endif /* TEST_STUB_SPI_H */
