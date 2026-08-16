/*!
  \file    laser.ino 
  \brief   modif for test laser

  \version 2024-07-17, V1.0
	\author	 kevin.wang
	\note    none
*/
#include <cstdlib>
#include <cstdio>

#include <FastLED.h>

#include <elapsedMillis.h>
#include <CRC32.h>

const char gtitle[] = "Lase_Engine_Firmware";
const char gversion[] = "V1.27";
char gtmpbuf[100];

// Teensy4.1 board v2 def

// RGB LED define
static const int LED_R = 36;
static const int LED_G = 37;
static const int LED_B = 38;

// laser driver en
static const int LASER_550nm = 19;
static const int LASER_402nm = 18;
static const int LASER_470nm = 17;
static const int LASER_638nm = 16;
static const int LASER_735nm = 15;

// despeckler
static const int Dispecker_pin   = 33;

// laser status read port
static const int STATUS_402nm_TTL = 31;
static const int STATUS_470nm_TTL = 30;
static const int STATUS_550nm_TTL = 32;
static const int STATUS_638nm_TTL = 29;
static const int STATUS_735nm_TTL = 28;

// true: would display the key status
static const bool config_enable_key_status_led = false;
// the pin status of KEY
static const int Interlock_pin = 9;

const int NUM_LASER_CHANNELS = 5;
const int NUM_TEMP_CHANNELS = 6;
const int NUM_VOLTAGE_CHANNELS = 6;
const int NUM_CURRENT_CHANNELS = 6;
// Idle timeout before an ACTIVE channel is put back to sleep. 4 hours on every
// channel, including both 55x modules (previously 30 minutes), so a long
// acquisition with idle stretches does not drop channels out of ACTIVE.
const unsigned long CHANNELS_SLEEP_TIMEOUT[NUM_TEMP_CHANNELS] = {
  4 * 60UL * 60UL * 1000UL,
  4 * 60UL * 60UL * 1000UL,
  4 * 60UL * 60UL * 1000UL,
  4 * 60UL * 60UL * 1000UL,
  4 * 60UL * 60UL * 1000UL,
  4 * 60UL * 60UL * 1000UL
};

unsigned long lastActiveTime[NUM_TEMP_CHANNELS] = {0};
bool lastActiveTimeRecordFlag[NUM_TEMP_CHANNELS] = {false};

// If due to worktime is over 8 hours, the wakeupCoolDownTime would be 10 minuts
// others is 1 seconds
const unsigned long SHORT_WAKEUP_COOLDOWN_TIME = 1UL * 1000UL;
unsigned long wakeupCoolDownTime[NUM_TEMP_CHANNELS] = {
  SHORT_WAKEUP_COOLDOWN_TIME,
  SHORT_WAKEUP_COOLDOWN_TIME,
  SHORT_WAKEUP_COOLDOWN_TIME,
  SHORT_WAKEUP_COOLDOWN_TIME,
  SHORT_WAKEUP_COOLDOWN_TIME,
  SHORT_WAKEUP_COOLDOWN_TIME
};
unsigned long recordSleepTime[NUM_TEMP_CHANNELS];

const float TEMP_ERROR_THRESHOLD = 5;
const unsigned long ERROR_DURATION = 5000UL; // 5 seconds in milliseconds
const unsigned long ACTIVE_DURATION = 5000UL; // 5 seconds in milliseconds

const unsigned long EACH_QUERY_DISTANCE = 200UL; // 200 in milliseconds

enum CommandType{
	NONE = 0,
  TEMPERATURE = 1,
 	VOLTAGE = 2,
	CURRENT = 3,
  HITEMPSETPOINT = 4,
  ADJUSTTEMP = 5,
  ENABLETCM = 6,
  SWITCHTCMSTATUS
};

const uint8_t MAX_QUERY_TYPE_INDEX = 11;
CommandType query_frequnce[MAX_QUERY_TYPE_INDEX] = { TEMPERATURE, VOLTAGE, CURRENT, TEMPERATURE, VOLTAGE, TEMPERATURE, VOLTAGE, TEMPERATURE, CURRENT, HITEMPSETPOINT, SWITCHTCMSTATUS };
uint8_t current_query_type_index = 0;

const int8_t ERR_OUT_OF_RANGE = 100;

/*
 *
 * PREPARE_SLEEP: (middle status) do the action before enter real SLEEP status
 * WAKE_UP: (middle status) do the action for return status from SLEEP to WARMING_UP
 * CHECK_ERROR: (middle status) before enter ERROR status, do some check for comfirming
 *
 */
enum ChannelState {
  WARMING_UP,
  CHECK_ACTIVE,
  ACTIVE,
  WAKE_UP,
  SLEEP,
  PREPARE_SLEEP,
  CHECK_ERROR,
  ERROR
};

CRC32 crc;
// Pins for laser channels (adjust as needed)
const int laserPins[NUM_LASER_CHANNELS] = {LASER_402nm, LASER_470nm, LASER_638nm, LASER_735nm, LASER_550nm};

// Add a global array to track laser pin states, avoid to disable or enable lasers status too many times
// false: means the laser pin is at disable action status 
// true: means the laser pin is at enable action status 
bool laserPinActionStates[NUM_LASER_CHANNELS] = {false};

// read the real laser status pins
const int laserStatuspins[NUM_LASER_CHANNELS] = {STATUS_402nm_TTL, STATUS_470nm_TTL, STATUS_638nm_TTL, STATUS_735nm_TTL, STATUS_550nm_TTL};

uint8_t laserStatus[NUM_LASER_CHANNELS] = {0};
unsigned long lastLaserStatusChangeTime[NUM_LASER_CHANNELS] = {0};
elapsedMillis timeSinceLastQueryLaserStatus = 0;

// Temperature setpoints and channel states
float tempSetpoints[NUM_TEMP_CHANNELS] = {25.0, 25.0, 25.0, 25.0, 25.0, 99.7};
// indicate the channels' adjust temperature whether have been queried
bool getTempReady[NUM_TEMP_CHANNELS] = {false};
bool enableTCMReady[NUM_TEMP_CHANNELS] = {false};

float tempCurrentPoints[NUM_TEMP_CHANNELS] = {0.6, 0.5, 0.4, 0.3, 0.2, 0.1};
float highTempCurrentPoints[NUM_TEMP_CHANNELS] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6};

// Volatge value of channels
float voltageCurrentPoints[NUM_VOLTAGE_CHANNELS] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6};

// current value of channels
float currentCurrentPoints[NUM_CURRENT_CHANNELS] = {0, 0, 0, 0, 0.1, 0.2};

ChannelState channelStates[NUM_TEMP_CHANNELS] = {WARMING_UP};

// Temperature channels whose TEC controller is not wired to this engine.
// 638nm (2) and 735nm (3) are driven by standalone TEC controllers that are
// left powered on continuously, so this board has no TCM link to them and
// their temperature/voltage/current readings are meaningless. They are pinned
// to ACTIVE: reported ready to the host, skipped by the state machine and by
// the idle-sleep rule, and enabled by doEnableLasersAction() like any other
// ACTIVE channel. Wake/sleep commands for them are ignored.
const bool CHANNEL_TEC_EXTERNAL[NUM_TEMP_CHANNELS] = {
  false,  // 0: 405nm
  false,  // 1: 470nm
  true,   // 2: 638nm  - external TEC controller
  true,   // 3: 735nm  - external TEC controller
  false,  // 4: 55x cool
  false   // 5: 55x hot
};

// Hard cap on each startup TCM handshake. Reaching it means a module that we
// expect on the bus did not answer; carry on into loop() regardless so the
// board stays reachable over USB and can be diagnosed and reflashed.
const unsigned long TCM_STARTUP_TIMEOUT = 10UL * 1000UL;
unsigned long lastChannelStatesChangeTime[NUM_TEMP_CHANNELS] = {0};

elapsedMillis timeInErrorState[NUM_TEMP_CHANNELS] = {0};

// when sttaus change, need wait sometime to confirm device status
elapsedMillis timeStatusCheck[NUM_TEMP_CHANNELS] = {0};

elapsedMillis timeSinceLastQueryTemp = 0;
uint8_t gQueryTemperatureChannelIndex = 0;
uint8_t gQueryVoltageChannelIndex = 0;
uint8_t gQueryCurrentChannelIndex = 0;
uint8_t gQueryHiTempSetPointChannelIndex = 0;
uint8_t gQueryAdjustTempChannelIndex = 0;
uint8_t gEnableTCMChannelIndex = 0;
uint8_t gProcessChannelStatusIndex = 0;

// TCM modules protocol process variables
bool reply_frame_analyzing_flag = false;

char tcm_command_buf[256];
uint8_t tcm_command_buf_length = 0;

char tcm_reply_buf[256];
uint8_t tcm_reply_buf_length = 0;

// for judge whether the replay value is correct or not
char tcm_reply_title[256];
uint8_t tcm_reply_title_length = 0;
uint8_t tcm_reply_address = 0;
uint8_t tcm_reply_module = 0;
CommandType tcm_reply_command_type = NONE;

// host protocol process variables
uint8_t host_protocol_buf[256];
uint8_t host_protocol_buf_length = 0;

uint8_t key_status = 0; 

enum LEDState {
  RED,
  BLUE,
  GREEN,
  YELLOW
};

LEDState led_state = GREEN;
void set_status_LED(LEDState status) {
	switch (status) {
		case RED:
			if (led_state == RED)
				return;
  		digitalWrite(LED_R, HIGH);
  		digitalWrite(LED_G, LOW);
  		digitalWrite(LED_B, LOW);
			led_state = RED;
			break;

		case BLUE:
			if (led_state == BLUE)
				return;
  		digitalWrite(LED_R, LOW);
  		digitalWrite(LED_G, LOW);
  		digitalWrite(LED_B, HIGH);
			led_state = BLUE;
			break;

		case GREEN:
			if (led_state == GREEN)
				return;
  		digitalWrite(LED_R, LOW);
  		digitalWrite(LED_G, HIGH);
  		digitalWrite(LED_B, LOW);
			led_state = GREEN;
			break;

    case YELLOW:
      if (led_state == YELLOW)
        return;
  		digitalWrite(LED_R, HIGH);
  		digitalWrite(LED_G, HIGH);
  		digitalWrite(LED_B, LOW);
			led_state = YELLOW;
			break;

		default:
			break;
	}
}

void indicate_device_status() {
	bool flag = false;

  if (config_enable_key_status_led) {
    // if key_status is at OFF status, change the light to RED + GREEN
    if (key_status == 0) {
      set_status_LED(YELLOW);
      return;
    }
  }

  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    if (channelStates[i] == ERROR)
			flag = true;
	}
	if (flag == true) {
		set_status_LED(RED);
		return;
	}

	// there is not error channel
	// reset flag
	flag = false;
  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    if (channelStates[i] != ACTIVE)
			flag = true;
	}
	if (flag == true) {
		set_status_LED(BLUE);
		return;
	}

	// all channels are ACTIVE
	set_status_LED(GREEN);
}

void setParameters(const uint8_t* buffer, size_t size) {
  if (size == 1 + NUM_TEMP_CHANNELS * sizeof(float)) {
    for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
      memcpy(&tempSetpoints[i], buffer + 1 + i * sizeof(float), sizeof(float));
    }
    sendACK();
  } else {
    sendNAK();
  }
}

void disableLaser(int channel) {
	// 5 channels laser and 6 temperature channels
	if (channel >= NUM_LASER_CHANNELS)
		channel = NUM_LASER_CHANNELS - 1;

	if (channel < NUM_LASER_CHANNELS) {
		// Only write if the pin is not already disabled
		if (laserPinActionStates[channel] != false) {
			digitalWrite(laserPins[channel], HIGH);
			laserPinActionStates[channel] = false; // Update state
		}
	}
}

void enableLaser(int channel) {
	// 5 channels laser and 6 temperature channels
	if (channel >= NUM_LASER_CHANNELS)
		channel = NUM_LASER_CHANNELS - 1;

	if (channel < NUM_LASER_CHANNELS) {
		// Only write if the pin is not already enabled
		if (laserPinActionStates[channel] != true) {
			digitalWrite(laserPins[channel], LOW);
			laserPinActionStates[channel] = true; // Update state
		}
	}
}

/*
TC1:TCACTUALTEMP?@1		channel:0
TC1:TCACTTEMP?@2			channel:1
TC1:TCACTUALTEMP?@3		channel:2
TC1:TCACTUALTEMP?@4		channel:3
TC1:TCACTUALTEMP?@5		channel:4
TC2:TCACTUALTEMP?@5		channel:5
 */
float readTemperature(int channel) {
	return tempCurrentPoints[channel];
}

/*
TC1:TCOTPHT?@1
TC1:TCOTPHT?@2
TC1:TCOTPHT?@3
TC1:TCOTPHT?@4
TC1:TCOTPHT?@5
TC2:TCOTPHT?@5
 */
float readHighTempSetPoint(int channel) {
	return highTempCurrentPoints[channel];
}

/*
TC1:TCACTUALVOLTAGE?@1
TC1:TCACTVOL?@2
TC1:TCACTUALVOLTAGE?@3
TC1:TCACTUALVOLTAGE?@4
TC1:TCACTUALVOLTAGE?@5
TC2:TCACTUALVOLTAGE?@5
*/
float readTECVoltage(int channel) {
	return voltageCurrentPoints[channel];
}

/*
0
0
0
0
TC1:TCACTCUR?@5
TC2:TCACTCUR?@5
*/
float readTECCurrent(int channel) {
	return currentCurrentPoints[channel];
}

/*
	address: 1~5
	module_index: 1~2
	return: 0~5
 */
uint8_t getChannelIndex(uint8_t address, uint8_t module_index) {
	if (address < 5 && address > 0)
		return address - 1;
	else if(address == 5) {
    if (module_index == 1)
      return 4;
    else if (module_index == 2)
      return 5;
	}
  return ERR_OUT_OF_RANGE;
}

/*
	channel: 0~5
*/
void getAddressModuleFromChannel(uint8_t channel, uint8_t* address, uint8_t* module_index) {
	if (channel < 4 && channel >= 0) {
		*address = channel + 1;
		*module_index = 1;
	}
	else {
		if (channel == 4) {
			*address = 5;
			*module_index = 1;
		}
		else if (channel == 5) {
			*address = 5;
			*module_index = 2;
		}
		else {
			*address = ERR_OUT_OF_RANGE; 
			*module_index = ERR_OUT_OF_RANGE;
		}
	}
}

/*
	channel: 0~5
 */
void tcmSwitchTCMStatusCommand(uint8_t channel_index) {
  if (channelStates[channel_index] == PREPARE_SLEEP) {
		// TCSW=0 disable the TCM temperary controller
    tcmDisableEnableTCMCommand(channel_index, false);
  }
  else if (channelStates[channel_index] == WAKE_UP) {
		// TCSW=1 enable the TCM temperary controller
    tcmDisableEnableTCMCommand(channel_index, true);
  }
}

/*
	channel: 0~5
  flag:  True: enable TCM; False: disable TCM
 */
void tcmDisableEnableTCMCommand(uint8_t channel_index, bool flag) {
	uint8_t address = 1; uint8_t module_index = 1;
	getAddressModuleFromChannel(channel_index, &address, &module_index);
	if (address == ERR_OUT_OF_RANGE && module_index == ERR_OUT_OF_RANGE)
		return;

	tcm_command_buf_length = 0;

  if (flag == false)
    sprintf(tcm_command_buf, "TC%d:TCSW=0@%d%c", module_index, address, 0x0D);
  else
    sprintf(tcm_command_buf, "TC%d:TCSW=1@%d%c", module_index, address, 0x0D);

	Serial5.write(tcm_command_buf, strlen(tcm_command_buf));

  sprintf(tcm_reply_title, "CMD:REPLY=");

	tcm_reply_title_length = strlen(tcm_reply_title);
	tcm_reply_address = address;
	tcm_reply_module = module_index;
	tcm_reply_command_type = SWITCHTCMSTATUS;
		
	// reset tcm ptotocol analyzing buffer
	tcm_reply_buf_length = 0;
	
	// enable analyzing frame
	reply_frame_analyzing_flag = true;
}

/*
	channel: 0~5
 */
void tcmStartupEnableTCMCommand(uint8_t channel_index) {
	uint8_t address = 1; uint8_t module_index = 1;
	getAddressModuleFromChannel(channel_index, &address, &module_index);
	if (address == ERR_OUT_OF_RANGE && module_index == ERR_OUT_OF_RANGE)
		return;

	tcm_command_buf_length = 0;

  sprintf(tcm_command_buf, "TC%d:TCSW=1@%d%c", module_index, address, 0x0D);

	Serial5.write(tcm_command_buf, strlen(tcm_command_buf));

  sprintf(tcm_reply_title, "CMD:REPLY=");

	tcm_reply_title_length = strlen(tcm_reply_title);
	tcm_reply_address = address;
	tcm_reply_module = module_index;
	tcm_reply_command_type = ENABLETCM;
		
	// reset tcm ptotocol analyzing buffer
	tcm_reply_buf_length = 0;
	
	// enable analyzing frame
	reply_frame_analyzing_flag = true;
}

/*
	channel_index: 0~5

	address: 1~5
	module_index: 1~2
 */
void tcmQueryTemperatureCommand(uint8_t channel_index) {
	uint8_t address = 1; uint8_t module_index = 1;
	getAddressModuleFromChannel(channel_index, &address, &module_index);
	if (address == ERR_OUT_OF_RANGE && module_index == ERR_OUT_OF_RANGE)
		return;

	tcm_command_buf_length = 0;

	if (address == 2) {
		sprintf(tcm_command_buf, "TC%d:TCACTTEMP?@%d%c", module_index, address, 0x0D);
	}
	else {
		sprintf(tcm_command_buf, "TC%d:TCACTUALTEMP?@%d%c", module_index, address, 0x0D);
	}
	Serial5.write(tcm_command_buf, strlen(tcm_command_buf));
	
	if (address == 2) {
		sprintf(tcm_reply_title, "TC%d:TCACTTEMP=", module_index);
	}
	else {
		sprintf(tcm_reply_title, "TC%d:TCACTUALTEMP=", module_index);
	}

	tcm_reply_title_length = strlen(tcm_reply_title);
	tcm_reply_address = address;
	tcm_reply_module = module_index;
	tcm_reply_command_type = TEMPERATURE;
		
	// reset tcm ptotocol analyzing buffer
	tcm_reply_buf_length = 0;
	
	// enable analyzing frame
	reply_frame_analyzing_flag = true;
}

/*
	channel_index: 0~5
 */
void tcmQueryHiTempSetPointCommand(uint8_t channel_index) {
	uint8_t address = 1; uint8_t module_index = 1;
	getAddressModuleFromChannel(channel_index, &address, &module_index);
	if (address == ERR_OUT_OF_RANGE && module_index == ERR_OUT_OF_RANGE)
		return;

	tcm_command_buf_length = 0;

  sprintf(tcm_command_buf, "TC%d:TCOTPHT?@%d%c", module_index, address, 0x0D);
	Serial5.write(tcm_command_buf, strlen(tcm_command_buf));

  sprintf(tcm_reply_title, "TC%d:TCOTPHT=", module_index);

	tcm_reply_title_length = strlen(tcm_reply_title);
	tcm_reply_address = address;
	tcm_reply_module = module_index;
	tcm_reply_command_type = HITEMPSETPOINT;
		
	// reset tcm ptotocol analyzing buffer
	tcm_reply_buf_length = 0;
	
	// enable analyzing frame
	reply_frame_analyzing_flag = true;
}

/*
	channel_index: 0~5

	address: 1~5
	module_index: 1~2
 */
void tcmQueryVoltageCommand(uint8_t channel_index) {
	uint8_t address = 1; uint8_t module_index = 1;
	getAddressModuleFromChannel(channel_index, &address, &module_index);
	if (address == ERR_OUT_OF_RANGE && module_index == ERR_OUT_OF_RANGE)
		return;

	tcm_command_buf_length = 0;

	if (address == 2) {
		sprintf(tcm_command_buf, "TC%d:TCACTVOL?@%d%c", module_index, address, 0x0D);
	}
	else {
		sprintf(tcm_command_buf, "TC%d:TCACTUALVOLTAGE?@%d%c", module_index, address, 0x0D);
	}
	Serial5.write(tcm_command_buf, strlen(tcm_command_buf));

	if (address == 2) {
		sprintf(tcm_reply_title, "TC%d:TCACTVOL=", module_index);
	}
	else {
		sprintf(tcm_reply_title, "TC%d:TCACTUALVOLTAGE=", module_index);
	}

	tcm_reply_title_length = strlen(tcm_reply_title);
	tcm_reply_address = address;
	tcm_reply_module = module_index;
	tcm_reply_command_type = VOLTAGE;
		
	// reset tcm ptotocol analyzing buffer
	tcm_reply_buf_length = 0;
	
	// enable analyzing frame
	reply_frame_analyzing_flag = true;
}

/*
	channel_index: 0~5

	address: 1~5
	module_index: 1~2
 */
void tcmQueryCurrentCommand(uint8_t channel_index) {
	uint8_t address = 1; uint8_t module_index = 1;
	getAddressModuleFromChannel(channel_index, &address, &module_index);
	if (address == ERR_OUT_OF_RANGE && module_index == ERR_OUT_OF_RANGE)
		return;

	tcm_command_buf_length = 0;

	sprintf(tcm_command_buf, "TC%d:TCACTCUR?@%d%c", module_index, address, 0x0D);

	Serial5.write(tcm_command_buf, strlen(tcm_command_buf));

	sprintf(tcm_reply_title, "TC%d:TCACTCUR=", module_index);

	tcm_reply_title_length = strlen(tcm_reply_title);
	tcm_reply_address = address;
	tcm_reply_module = module_index;
	tcm_reply_command_type = CURRENT;

	// reset tcm ptotocol analyzing buffer
	tcm_reply_buf_length = 0;

	// enable analyzing frame
	reply_frame_analyzing_flag = true;
}

/*
	channel_index: 0~5

	address: 1~5
	module_index: 1~2
 */
void tcmQueryAdjustTempCommand(uint8_t channel_index) {
	uint8_t address = 1; uint8_t module_index = 1;
	getAddressModuleFromChannel(channel_index, &address, &module_index);
	if (address == ERR_OUT_OF_RANGE && module_index == ERR_OUT_OF_RANGE)
		return;

	tcm_command_buf_length = 0;

	if (address == 2 || address == 5) {
		sprintf(tcm_command_buf, "TC%d:TCADJTEMP?@%d%c", module_index, address, 0x0D);
	}
	else {
		sprintf(tcm_command_buf, "TC%d:TCADJUSTTEMP?@%d%c", module_index, address, 0x0D);
	}

	Serial5.write(tcm_command_buf, strlen(tcm_command_buf));

	if (address == 2 || address == 5) {
		sprintf(tcm_reply_title, "TC%d:TCADJTEMP=", module_index);
	}
	else {
		sprintf(tcm_reply_title, "TC%d:TCADJUSTTEMP=", module_index);
	}

	tcm_reply_title_length = strlen(tcm_reply_title);
	tcm_reply_address = address;
	tcm_reply_module = module_index;
	tcm_reply_command_type = ADJUSTTEMP;
		
	// reset tcm ptotocol analyzing buffer
	tcm_reply_buf_length = 0;
	
	// enable analyzing frame
	reply_frame_analyzing_flag = true;
}

/*
	upload data to upstream host, for example PC
 */
void uploadData(uint8_t* data, uint8_t length)
{
	Serial.write(data, length);
	// flag indicate one frame is over
	uint8_t end[2] = {0x0A, 0x0D};
	Serial.write(end, 2);
}

void sendACK() {
  uint8_t ack = 'A';
  uint32_t ackCRC = crc.calculate(&ack, 1);
  uint8_t ackPacket[5];
  ackPacket[0] = ack;
  memcpy(ackPacket + 1, &ackCRC, 4);
	uploadData(ackPacket, 5);
}

void sendNAK() {
  uint8_t nak = 'N';
  uint32_t nakCRC = crc.calculate(&nak, 1);
  uint8_t nakPacket[5];
  nakPacket[0] = nak;
  memcpy(nakPacket + 1, &nakCRC, 4);
	uploadData(nakPacket, 5);
}

/*
 * query laser channels status shortly
 */
void queryLaserStatus() {
  uint8_t tmp = 0;

	// each 200ms to query laser status 
	if (timeSinceLastQueryLaserStatus < EACH_QUERY_DISTANCE)
		return;
	timeSinceLastQueryLaserStatus = 0;

  for (int i = 0; i < NUM_LASER_CHANNELS; i++) {
    tmp = digitalRead(laserStatuspins[i]);
    if (laserStatus[i] != tmp) {
      laserStatus[i] = tmp;
      lastLaserStatusChangeTime[i] = millis();
    }
  }

  // also record the key status
  key_status = digitalRead(Interlock_pin);
}

void sendStatus() {
  uint8_t statusPacket[1 + NUM_LASER_CHANNELS + NUM_TEMP_CHANNELS * 7 + NUM_TEMP_CHANNELS * 2 + NUM_TEMP_CHANNELS * 2];
  statusPacket[0] = 'S'; // Status packet identifier

  for (int i = 0; i < NUM_LASER_CHANNELS; i++) {
    statusPacket[i + 1] = laserStatus[i];
  }

  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    int offset = 1 + NUM_LASER_CHANNELS + i * 7;
    statusPacket[offset] = channelStates[i];
    
    // Convert temperature to 2-byte representation
    int16_t temp = (int16_t)(readTemperature(i) * 100); // Temperature in centidegrees
    statusPacket[offset + 1] = temp >> 8;
    statusPacket[offset + 2] = temp & 0xFF;
    
		temp = (int16_t)(readTECVoltage(i) * 100);
    statusPacket[offset + 3] = temp >> 8;
    statusPacket[offset + 4] = temp & 0xFF;

		temp = (int16_t)(readTECCurrent(i) * 100);
    statusPacket[offset + 5] = temp >> 8;
    statusPacket[offset + 6] = temp & 0xFF;
  }

  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    int offset = 1 + NUM_LASER_CHANNELS + NUM_TEMP_CHANNELS * 7 + i * 2;

    int16_t temp = (int16_t)((readTemperature(i) - tempSetpoints[i]) * 100); // Temperature setpoints
    statusPacket[offset + 0] = temp >> 8;
    statusPacket[offset + 1] = temp & 0xFF;
	}

  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    int offset = 1 + NUM_LASER_CHANNELS + NUM_TEMP_CHANNELS * 7 + NUM_TEMP_CHANNELS * 2 + i * 2;

    int16_t temp = (int16_t)(readHighTempSetPoint(i) * 100); // High Temperature setpoints
    statusPacket[offset + 0] = temp >> 8;
    statusPacket[offset + 1] = temp & 0xFF;
	}

  uint32_t packetCRC = crc.calculate(statusPacket, sizeof(statusPacket));
  uint8_t finalPacket[sizeof(statusPacket) + 4];
  memcpy(finalPacket, statusPacket, sizeof(statusPacket));
  memcpy(finalPacket + sizeof(statusPacket), &packetCRC, 4);

	uploadData(finalPacket, sizeof(finalPacket));
}

/*
 * channel: from 0 to 4
 */
void sendChannelStatus(uint32_t channel) {
  uint8_t statusPacket[1 + 1 + 1];
  statusPacket[0] = 'G'; // Status packet identifier
  statusPacket[1] = channel;
  statusPacket[2] = laserStatus[channel];

  uint32_t packetCRC = crc.calculate(statusPacket, sizeof(statusPacket));
  uint8_t finalPacket[sizeof(statusPacket) + 4];
  memcpy(finalPacket, statusPacket, sizeof(statusPacket));
  memcpy(finalPacket + sizeof(statusPacket), &packetCRC, 4);

  uploadData(finalPacket, sizeof(finalPacket));
}

void onPacketReceived(const uint8_t* buffer, size_t size) {
  if (size < 5) return; // Minimum packet size (1 byte command + 4 bytes CRC)

  uint32_t receivedCRC;
  memcpy(&receivedCRC, buffer + size - 4, 4);
  uint32_t calculatedCRC = crc.calculate(buffer, size - 4);

  if (receivedCRC != calculatedCRC) {
    sendNAK();
    return;
  }

  switch (buffer[0]) {
    case 'Q': // Query status
      sendStatus();
      break;
    case 'P': // Set parameters
      setParameters(buffer, size - 4);
      break;
    case 'S': // put one channel to sleep 
      {
        uint32_t channel;
        channel = uint32_t(buffer[1] + (buffer[2]<<8) + (buffer[3]<<16) + (buffer[4]<<24));
        if (channel == 4) {
          doSleepAction(4);
          doSleepAction(5);
        }
        else
          doSleepAction(channel);
      }
      break;
    case 'W': // Reset device status 
      {
        uint32_t channel;
        channel = uint32_t(buffer[1] + (buffer[2]<<8) + (buffer[3]<<16) + (buffer[4]<<24));
        if (channel == 4) {
          doWakeupAction(4);
          doWakeupAction(5);
        }
        else
          doWakeupAction(channel);
      }
      break;
    case 'G': // Query channel status
      {
        uint32_t channel;
        channel = uint32_t(buffer[1] + (buffer[2]<<8) + (buffer[3]<<16) + (buffer[4]<<24));
        sendChannelStatus(channel);
      }
      break;
  }
}

/*
	get a char position from a char array 

	return: offset address of the char, if not found return -1 
				
 */
int getCharOffset(char* arrayValue, char searchChar) {
	char *p = strchr(arrayValue, searchChar);
	if (p != NULL) {
		return p - arrayValue;
	}
	else {
		return -1; 
	}
}

/*
	convert char array to float	

 */
float convertChararrayToFloat(char * arrayValue, int valueLength) {
	char inArray[valueLength + 1]	= {0};
	for (int i = 0; i < valueLength; i++) {
		inArray[i] = arrayValue[i];
	}

	String value(inArray);
	return value.toFloat();
}

/*
	get information from one frame reply

	return: true, the retvalue is the available value
				false, the retvalue is the inavailable value
 */
bool analyzeValueFromTCMProtocol(float *retvalue) {
	if (strncmp(tcm_reply_title, tcm_reply_buf, tcm_reply_title_length) == 0) {
		int offset = getCharOffset(&tcm_reply_buf[tcm_reply_title_length], '@');
		if (offset == -1)
			return false;

		*retvalue = convertChararrayToFloat(&tcm_reply_buf[tcm_reply_title_length], offset);
		return true;
	}
	else
		return false;
}

/*
	query loop, call it in the main loop
*/
void queryTCMDataMainLoop() {
	// each 200ms to query one channel data from TMC module
	if (timeSinceLastQueryTemp < EACH_QUERY_DISTANCE)
		return;
	timeSinceLastQueryTemp = 0;
	
	switch (query_frequnce[current_query_type_index++]) {
		case TEMPERATURE:
			{
				// send the query frame to one of TCM modules
				tcmQueryTemperatureCommand(gQueryTemperatureChannelIndex++);
				if (gQueryTemperatureChannelIndex == NUM_TEMP_CHANNELS)
					gQueryTemperatureChannelIndex = 0;
			}
			break;
		case VOLTAGE: 
			{
				// send the query frame to one of TCM modules
				tcmQueryVoltageCommand(gQueryVoltageChannelIndex++);
				if (gQueryVoltageChannelIndex == NUM_VOLTAGE_CHANNELS)
					gQueryVoltageChannelIndex = 0;
			}
			break;
		case CURRENT:
			{
				// only module 5's two current need to be query
				if (gQueryCurrentChannelIndex < 4)
					gQueryCurrentChannelIndex = 4;

				// send the query frame to one of TCM modules
				tcmQueryCurrentCommand(gQueryCurrentChannelIndex++);
				if (gQueryCurrentChannelIndex == NUM_CURRENT_CHANNELS)
					gQueryCurrentChannelIndex = 0;
			}
			break;
		case HITEMPSETPOINT:
			{
				// send the query frame to one of TCM modules
				tcmQueryHiTempSetPointCommand(gQueryHiTempSetPointChannelIndex++);
				if (gQueryHiTempSetPointChannelIndex == NUM_TEMP_CHANNELS)
					gQueryHiTempSetPointChannelIndex = 0;
			}
			break;
		case SWITCHTCMSTATUS:
			{
				tcmSwitchTCMStatusCommand(gProcessChannelStatusIndex++);
				if (gProcessChannelStatusIndex == NUM_TEMP_CHANNELS)
					gProcessChannelStatusIndex = 0;
			}
			break;
		default:
			break;
	}

	if (current_query_type_index == MAX_QUERY_TYPE_INDEX)
		current_query_type_index = 0;
}

/*
	analyzing host(such as PC) frame
 */
void analyzingHostFrame() {
	if (Serial.available()) {
		host_protocol_buf[host_protocol_buf_length++] = Serial.read();
		if (host_protocol_buf[host_protocol_buf_length - 1] == 0x0D &&
				host_protocol_buf[host_protocol_buf_length - 2] == 0x0A) {
			onPacketReceived(host_protocol_buf, host_protocol_buf_length - 2);

			host_protocol_buf_length = 0;
		}
	}
}

/*
 * nomatter what, enable all channel TCMs when power up
 */
// Advance a TCM bus index past any channel that has no TCM module on UART5.
// The startup query loops only step forward when a reply arrives, so without
// this they would re-query an absent module forever and setup() would never
// return (leaving the board enumerated but completely unresponsive).
uint8_t nextTcmChannelIndex(uint8_t idx) {
  for (int n = 0; n < NUM_TEMP_CHANNELS; n++) {
    idx++;
    if (idx >= NUM_TEMP_CHANNELS)
      idx = 0;
    if (!CHANNEL_TEC_EXTERNAL[idx])
      return idx;
  }
  return 0;
}

void enableAllTCMs() {
  timeSinceLastQueryTemp = 0;

  // Channels with an external TEC controller have nothing on the TCM bus.
  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    if (CHANNEL_TEC_EXTERNAL[i])
      enableTCMReady[i] = true;
  }
  if (CHANNEL_TEC_EXTERNAL[gEnableTCMChannelIndex])
    gEnableTCMChannelIndex = nextTcmChannelIndex(gEnableTCMChannelIndex);

  unsigned long startTime = millis();
  while(enableTCMReady[NUM_TEMP_CHANNELS - 1] == false) {
    if (millis() - startTime >= TCM_STARTUP_TIMEOUT)
      break;

    // each 200ms to query one channel data from TMC module
    if (timeSinceLastQueryTemp >= EACH_QUERY_DISTANCE) {
      timeSinceLastQueryTemp = 0;

      tcmStartupEnableTCMCommand(gEnableTCMChannelIndex);
    }

    // TCM modules protocol process
    analyzingTCMFrame();
  }
}

/*
 get adjust temperature value from TCM, before enter main loop
 */
void getAdjustTemperature() {
  timeSinceLastQueryTemp = 0;

  // Channels with an external TEC controller have nothing on the TCM bus, so
  // there is no setpoint to fetch and no reply will ever come.
  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    if (CHANNEL_TEC_EXTERNAL[i])
      getTempReady[i] = true;
  }
  if (CHANNEL_TEC_EXTERNAL[gQueryAdjustTempChannelIndex])
    gQueryAdjustTempChannelIndex = nextTcmChannelIndex(gQueryAdjustTempChannelIndex);

  unsigned long startTime = millis();
  while(getTempReady[NUM_TEMP_CHANNELS - 1] == false) {
    if (millis() - startTime >= TCM_STARTUP_TIMEOUT)
      break;

    // each 200ms to query one channel data from TMC module
    if (timeSinceLastQueryTemp >= EACH_QUERY_DISTANCE) {
      timeSinceLastQueryTemp = 0;

      // send the query frame to one of TCM modules
      tcmQueryAdjustTempCommand(gQueryAdjustTempChannelIndex);
    }

    // TCM modules protocol process
    analyzingTCMFrame();
  }
}

/*
	analyzing TCM modules fram
 */
void analyzingTCMFrame() {
	if (reply_frame_analyzing_flag) {
		if (Serial5.available()) {
			tcm_reply_buf[tcm_reply_buf_length++] = Serial5.read();
			// read the end flag of frame
			if (tcm_reply_buf[tcm_reply_buf_length - 1] == 0x0D) {

				switch (tcm_reply_command_type) {
					case TEMPERATURE:
						{
							float tvalue = 0;
							if (analyzeValueFromTCMProtocol(&tvalue)) {
								uint8_t tindex = getChannelIndex(tcm_reply_address, tcm_reply_module);
								if (tindex != ERR_OUT_OF_RANGE) {
									tempCurrentPoints[tindex] = tvalue;
								}
							}
						}
						break;
					case VOLTAGE:
						{
							float tvalue = 0;
							if (analyzeValueFromTCMProtocol(&tvalue)) {
								uint8_t tindex = getChannelIndex(tcm_reply_address, tcm_reply_module);
								if (tindex != ERR_OUT_OF_RANGE) {
									voltageCurrentPoints[tindex] = tvalue;
								}
							}
						}
						break;
					case CURRENT:
						{
							float tvalue = 0;
							if (analyzeValueFromTCMProtocol(&tvalue)) {
								uint8_t tindex = getChannelIndex(tcm_reply_address, tcm_reply_module);
								if (tindex != ERR_OUT_OF_RANGE) {
										currentCurrentPoints[tindex] = tvalue;
								}
							}
						}
						break;
					case HITEMPSETPOINT:
						{
							float tvalue = 0;
							if (analyzeValueFromTCMProtocol(&tvalue)) {
								uint8_t tindex = getChannelIndex(tcm_reply_address, tcm_reply_module);
								if (tindex != ERR_OUT_OF_RANGE) {
										highTempCurrentPoints[tindex] = tvalue;
								}
							}
						}
						break;
					case ADJUSTTEMP:
						{
							float tvalue = 0;
							if (analyzeValueFromTCMProtocol(&tvalue)) {
								uint8_t tindex = getChannelIndex(tcm_reply_address, tcm_reply_module);
								if (tindex != ERR_OUT_OF_RANGE) {
										tempSetpoints[tindex] = tvalue;
										getTempReady[tindex] = true;
                    // only be successful could deal with next channel
                    gQueryAdjustTempChannelIndex = nextTcmChannelIndex(gQueryAdjustTempChannelIndex);
								}
							}
						}
						break;
          case ENABLETCM:
            {
              float tvalue = 0;
              if (analyzeValueFromTCMProtocol(&tvalue)) {
                uint8_t tindex = getChannelIndex(tcm_reply_address, tcm_reply_module);
                if (tindex != ERR_OUT_OF_RANGE) {
                  if (int(tvalue) == 1) {
                    enableTCMReady[tindex] = true;
                    // only be successful could deal with next channel
                    gEnableTCMChannelIndex = nextTcmChannelIndex(gEnableTCMChannelIndex);
                  }
                }
              }
            }
						break;
					case SWITCHTCMSTATUS:
						{
							float tvalue = 0;
							if (analyzeValueFromTCMProtocol(&tvalue)) {
								uint8_t tindex = getChannelIndex(tcm_reply_address, tcm_reply_module);
                if (tindex != ERR_OUT_OF_RANGE) {
                  if (int(tvalue) == 1) {
                    if (channelStates[tindex] == PREPARE_SLEEP)
                      updateChannelStatus(tindex, SLEEP);
                    else if(channelStates[tindex] == WAKE_UP)
                      updateChannelStatus(tindex, WARMING_UP);
                  }
                }
							}
						}
						break;
					default:
						break;
				}

				// finish frame analyzing, reset flag
				reply_frame_analyzing_flag = false;
				tcm_reply_buf_length = 0;
				tcm_reply_address = 0;
				tcm_reply_module = 0;
			}
		}
	}
}

/*
 * when Channel status change, update the value and the change time
 */
void updateChannelStatus(int channel, ChannelState status) {
  if (channelStates[channel] != status)
    lastChannelStatesChangeTime[channel] = millis();
  // means the channel realy enter to SLEEP status
  channelStates[channel] = status;
}

/*
 * channel_index: 0~5
 */
unsigned long getLaserStatusChangeTime(int channel_index) {
	// from 0 ~ 3
	if (channel_index < (NUM_LASER_CHANNELS - 1))
		return lastLaserStatusChangeTime[channel_index];

	// 4, 5 return last laser channel value
	return lastLaserStatusChangeTime[NUM_LASER_CHANNELS - 1];
}

/*
 * channel_index: 0~5
 */
uint8_t getLaserStatus(int channel_index) {
	// from 0 ~ 3
	if (channel_index < (NUM_LASER_CHANNELS - 1))
		return laserStatus[channel_index];

	// 4, 5 return last laser channel value
	return laserStatus[NUM_LASER_CHANNELS - 1];
}

void setup() {
  Serial.begin(115200);
	delayMicroseconds(100000);	

	/*
	char cmd;
	while(1) {
		if (Serial.available()) {
			cmd = Serial.read();
			cmd = 'R';
			switch(cmd) {
				case 'R':
					goto Run;
					break;
			}
		}
	}
	*/

//Run:
	/*
  sprintf(gtmpbuf, "%s %s", gtitle, gversion);
  Serial.println(gtmpbuf);
	*/

  // TCM104x module UART5
  Serial5.begin(57600);
	delayMicroseconds(100000);	

  pinMode(LED_R, OUTPUT);
  digitalWrite(LED_R, LOW);

  pinMode(LED_G, OUTPUT);
  digitalWrite(LED_G, LOW);

  pinMode(LED_B, OUTPUT);
  digitalWrite(LED_B, LOW);

  digitalWrite(LED_G, HIGH);

  // set the control pins to OUTPUT status
  // and disable the control pins as well
  for (int i = 0; i < NUM_LASER_CHANNELS; i++) {
    pinMode(laserPins[i], OUTPUT);
    digitalWrite(laserPins[i], HIGH);
  }

  pinMode(Dispecker_pin, OUTPUT);
  digitalWrite(Dispecker_pin, HIGH);
	
	// laser port start pins initialize
  for (int i = 0; i < NUM_LASER_CHANNELS; i++) {
    pinMode(laserStatuspins[i], INPUT);
  }

  pinMode(Interlock_pin, INPUT);

  // initialize the last lasert status change time
  for (int i = 0; i < NUM_LASER_CHANNELS; i++) {
    lastLaserStatusChangeTime[i] = millis();
  }

  // before enter main loop, get adjust temperature from TCMs
  getAdjustTemperature();

  // enable all channels TCM
  // sometimes application would disable TCM before it power off 
  // so need be enable all channels TCM
  enableAllTCMs();

	timeSinceLastQueryTemp = 0;

  // Initialize the last transition
  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    lastChannelStatesChangeTime[i] = millis();
  }
  
  // only the channels status reach ACTIVE status could enable laser
  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    disableLaser(i);
  }

  // Channels with an external TEC controller have no thermal loop to run here,
  // so start them ACTIVE and leave them there.
  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    if (CHANNEL_TEC_EXTERNAL[i]) {
      updateChannelStatus(i, ACTIVE);
    }
  }
}

// do real sleep action
void doSleepAction(int i) {
  // Nothing to park: the TEC controller is not ours to switch off, and the
  // laser stays enabled. Ignore so a host sleep command can't clear ACTIVE.
  if (CHANNEL_TEC_EXTERNAL[i])
    return;

  disableLaser(i);
  updateChannelStatus(i, PREPARE_SLEEP);
  recordSleepTime[i] = millis();
  wakeupCoolDownTime[i] = SHORT_WAKEUP_COOLDOWN_TIME;
}

void doWakeupAction(int i) {
  // Already ACTIVE and never sleeps; a wake would only knock it out of ACTIVE
  // and into a warm-up cycle that can never complete without a TCM link.
  if (CHANNEL_TEC_EXTERNAL[i])
    return;

  // do not need enable laser immediately.
  // when channel status enter ACTIVE, then anble laser
  //enableLaser(i);
  updateChannelStatus(i, WAKE_UP);

  // enforce to reset laserStatus ChangeTime
  // laser chanel is 5 and status chanel is 6
  if (i == 5)
    i = 4;

  // enforce to reset laserStatus ChangeTime
  // otherwise the application would fall into sleep
  // again
  laserStatus[i] = digitalRead(laserStatuspins[i]);
  lastLaserStatusChangeTime[i] = millis();
}

void doEnableLasersAction() {
  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    if (i != 4 && i != 5) {
      if (channelStates[i] == ACTIVE) {
        enableLaser(i);
      }
    }
    else {
      if (channelStates[4] == ACTIVE && channelStates[5] == ACTIVE) {
        enableLaser(i);
      }
    }
  }
}

void loop() {
  // query laser status
  queryLaserStatus();

	// query TCM module data
	queryTCMDataMainLoop();

  for (int i = 0; i < NUM_TEMP_CHANNELS; i++) {
    // External TEC controller: temperature readings here are meaningless, so
    // running the thermal state machine on them would drop ACTIVE. Skip both
    // the transitions and the idle-sleep rule below.
    if (CHANNEL_TEC_EXTERNAL[i])
      continue;

    float currentTemp = readTemperature(i);
    float tempDiff = currentTemp - tempSetpoints[i];

    switch (channelStates[i]) {
      case WARMING_UP:
        if (abs(tempDiff) <= 0.5) {
          updateChannelStatus(i, CHECK_ACTIVE);
        }
				else if (tempDiff > TEMP_ERROR_THRESHOLD) {
          updateChannelStatus(i, CHECK_ERROR);
				}
        break;
      case CHECK_ACTIVE:
        if (abs(tempDiff) > 0.5) {
					if (tempDiff > TEMP_ERROR_THRESHOLD) {
          	updateChannelStatus(i, CHECK_ERROR);
					}
					else {
          	updateChannelStatus(i, WARMING_UP);
					}
        }
        else {
          if ((millis() - lastChannelStatesChangeTime[i]) >= ACTIVE_DURATION) {
            //enableLaser(i);
            updateChannelStatus(i, ACTIVE);
          }
        }
        break;
      case ACTIVE:
        if (tempDiff >= TEMP_ERROR_THRESHOLD) {
          updateChannelStatus(i, CHECK_ERROR);
        }
        else {
          if (tempDiff <= -0.5) {
            updateChannelStatus(i, WARMING_UP);
          }
        }
        break;
      case CHECK_ERROR:
        if (tempDiff < TEMP_ERROR_THRESHOLD) {
          if ( tempDiff >= -0.5 ) {
            updateChannelStatus(i, ACTIVE);
          }
          else {
            updateChannelStatus(i, WARMING_UP);
          }
        }
        else {
          if ((millis() - lastChannelStatesChangeTime[i]) >= ERROR_DURATION) {
            disableLaser(i);
            updateChannelStatus(i, ERROR);
          }
        }
        break;
      case ERROR:
        if (abs(tempDiff) <= 0.5) {
          updateChannelStatus(i, CHECK_ACTIVE);
        }
        break;
      case SLEEP:
        if (getLaserStatus(i) == 1) {
          // need keep the wakeup signed be over cool down time
          if (millis() - recordSleepTime[i] > wakeupCoolDownTime[i])
            updateChannelStatus(i, WAKE_UP);
        }
        break;
      case WAKE_UP:
        // do nothing, just wait for TCM be changed to anble status
				// then enter WARMING_UP status
        break;
      case PREPARE_SLEEP:
        // do nothing, just wait for TCM be changed to disable status 
				// then enter SLEEP status 
        break;
    }

    if (channelStates[i] == WARMING_UP || channelStates[i] == ACTIVE || channelStates[i] == ERROR) {
      // the lasert Status is lower and kept the status over SLEEP_TIMEOUT, then be ready to SLEEP status
      if (getLaserStatus(i) == 0 && (millis() - getLaserStatusChangeTime(i)) >= CHANNELS_SLEEP_TIMEOUT[i]) {
        doSleepAction(i);
      }
    }
  }

  // only the chanel's status is at ACTIVE
  // we could enable the channel laser 
  // especially for channel 4 and 5
  doEnableLasersAction();

  // indicate the device status used LED color
  // Red: if there is one channel ERROR
  // Gree: all channels are ACTIVE 
  // Blue: others 
  indicate_device_status();

	/*
  // code just for debug
	if (Serial.available()) {
		cmd = Serial.read();

		switch (cmd) {
			case 'N':
				sendNAK();
				break;
			case 'A':
				sendACK();
				break;
			case 'T':
				break;
			case 'R':
				for (int i = 0; i < 6; i++) {
					sprintf(gtmpbuf, "ch%d: %f", i, tempCurrentPoints[i]);
  				Serial.println(gtmpbuf);
				}
				break;
		}
	}
  // code just for debug
	*/

	// analyzing host frame
	analyzingHostFrame();

	// TCM modules protocol process
	analyzingTCMFrame();
}
