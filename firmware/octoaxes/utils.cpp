#include <Arduino.h> 

#include "utils.h"
#include "build_opt.h"

const unsigned long SETUP_LED_ON_IF_TRIPPED_DURATION = 200;
const unsigned long SETUP_LED_OFF_DURATION = 200;

void setLedOff()
{
  digitalWrite(LED_BUILTIN,LOW);
  delay(SETUP_LED_OFF_DURATION);
}

void setLedOn(unsigned long duration)
{
  digitalWrite(LED_BUILTIN,HIGH);
	delay(duration);
}

void initializeStartupLED()
{
#ifdef ENABLE_LED_INDICATOR
  pinMode(LED_BUILTIN,OUTPUT);
  setLedOff();
  setLedOn(SETUP_LED_ON_IF_TRIPPED_DURATION);
  setLedOff();
  setLedOn(SETUP_LED_ON_IF_TRIPPED_DURATION);
  setLedOff();
  setLedOn(SETUP_LED_ON_IF_TRIPPED_DURATION);
#endif
}
