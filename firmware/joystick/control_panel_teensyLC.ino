#include <PacketSerial.h>
#include <Wire.h>
#include "TM1650.h"

// Forward declarations
static inline int sgn(int val);
uint32_t twos_complement(long signedLong, int N);
static uint8_t crc8_ccitt(const uint8_t *data, uint8_t n);

PacketSerial packetSerial;
TM1650 d;

// CRC-8-CCITT lookup table (poly 0x07, init 0x00) — same as firmware/octoaxes/serial.cpp
static const uint8_t CRC_TABLE[256] = {
    0x00, 0x07, 0x0E, 0x09, 0x1C, 0x1B, 0x12, 0x15, 0x38, 0x3F, 0x36, 0x31,
    0x24, 0x23, 0x2A, 0x2D, 0x70, 0x77, 0x7E, 0x79, 0x6C, 0x6B, 0x62, 0x65,
    0x48, 0x4F, 0x46, 0x41, 0x54, 0x53, 0x5A, 0x5D, 0xE0, 0xE7, 0xEE, 0xE9,
    0xFC, 0xFB, 0xF2, 0xF5, 0xD8, 0xDF, 0xD6, 0xD1, 0xC4, 0xC3, 0xCA, 0xCD,
    0x90, 0x97, 0x9E, 0x99, 0x8C, 0x8B, 0x82, 0x85, 0xA8, 0xAF, 0xA6, 0xA1,
    0xB4, 0xB3, 0xBA, 0xBD, 0xC7, 0xC0, 0xC9, 0xCE, 0xDB, 0xDC, 0xD5, 0xD2,
    0xFF, 0xF8, 0xF1, 0xF6, 0xE3, 0xE4, 0xED, 0xEA, 0xB7, 0xB0, 0xB9, 0xBE,
    0xAB, 0xAC, 0xA5, 0xA2, 0x8F, 0x88, 0x81, 0x86, 0x93, 0x94, 0x9D, 0x9A,
    0x27, 0x20, 0x29, 0x2E, 0x3B, 0x3C, 0x35, 0x32, 0x1F, 0x18, 0x11, 0x16,
    0x03, 0x04, 0x0D, 0x0A, 0x57, 0x50, 0x59, 0x5E, 0x4B, 0x4C, 0x45, 0x42,
    0x6F, 0x68, 0x61, 0x66, 0x73, 0x74, 0x7D, 0x7A, 0x89, 0x8E, 0x87, 0x80,
    0x95, 0x92, 0x9B, 0x9C, 0xB1, 0xB6, 0xBF, 0xB8, 0xAD, 0xAA, 0xA3, 0xA4,
    0xF9, 0xFE, 0xF7, 0xF0, 0xE5, 0xE2, 0xEB, 0xEC, 0xC1, 0xC6, 0xCF, 0xC8,
    0xDD, 0xDA, 0xD3, 0xD4, 0x69, 0x6E, 0x67, 0x60, 0x75, 0x72, 0x7B, 0x7C,
    0x51, 0x56, 0x5F, 0x58, 0x4D, 0x4A, 0x43, 0x44, 0x19, 0x1E, 0x17, 0x10,
    0x05, 0x02, 0x0B, 0x0C, 0x21, 0x26, 0x2F, 0x28, 0x3D, 0x3A, 0x33, 0x34,
    0x4E, 0x49, 0x40, 0x47, 0x52, 0x55, 0x5C, 0x5B, 0x76, 0x71, 0x78, 0x7F,
    0x6A, 0x6D, 0x64, 0x63, 0x3E, 0x39, 0x30, 0x37, 0x22, 0x25, 0x2C, 0x2B,
    0x06, 0x01, 0x08, 0x0F, 0x1A, 0x1D, 0x14, 0x13, 0xAE, 0xA9, 0xA0, 0xA7,
    0xB2, 0xB5, 0xBC, 0xBB, 0x96, 0x91, 0x98, 0x9F, 0x8A, 0x8D, 0x84, 0x83,
    0xDE, 0xD9, 0xD0, 0xD7, 0xC2, 0xC5, 0xCC, 0xCB, 0xE6, 0xE1, 0xE8, 0xEF,
    0xFA, 0xFD, 0xF4, 0xF3};

// pin defination
static const int pin_LED[6] = {9,10,11,12,13,14};
static const int pin_joystick_x = 17;
static const int pin_joystick_y = 16;
static const int pin_joystick_btn = 15;
static const int pin_pot1 = 21;
static const int pin_pot2 = 20;
static const int pin_key1 = 22;
static const int pin_key2 = 23;
static const int pin_encoder_1_A = 4;
static const int pin_encoder_1_B = 3;
static const int pin_encoder_2_A = 6;
static const int pin_encoder_2_B = 5;
static const int pin_encoder_3_A = 8;
static const int pin_encoder_3_B = 7;

// state variables
bool joystick_button_pressed = false;
bool key_pressed[6] = {false};
// wait for ack bit to be set; until it's set, keep sending btn pressed msg
// on the controller, finish the cycle when the btn pressed bit is cleared.
// repeat the same between the host computer and the main controller

// joystick
int joystick_offset_x = 338;
int joystick_offset_y = 339;
int joystick_delta_x = 0;
int joystick_delta_y = 0;
int joystick_deadband = 25;

// input related
volatile long encoder_pos = 0; // encoder pos >> 4 is the focus position
int input_sensitivity_xy = 0;
int input_sensitivity_z = 0;
volatile int encoder_step_size = 1;

// communication
static const int JOYSTICK_MSG_LENGTH = 10;// 4 bytes for encoder, 2 bytes for joystick x, 2 bytes for joystick y, 1 byte for buttons, 1 byte CRC
uint8_t packet[JOYSTICK_MSG_LENGTH] = {};
uint16_t tmp_uint16;
uint32_t tmp_uint32;

// display
char display_str[] = "0088";

// testing
int i_testing = 0;

// other settings
int focus_encoder_sensitivity_division = 4;

void setup() 
{

  // I2C for seven segment display
  Wire.begin(); 
  delay(200);
  d.init();
  display_str[2] = '0' + 10;
  display_str[3] = '0' + 10;
    
  // pin init.
  for(int i=0;i<6;i++)
  {
    pinMode(pin_LED[i],OUTPUT);
    digitalWrite(pin_LED[i],HIGH);
  }
  pinMode(pin_encoder_1_A,INPUT_PULLUP);
  pinMode(pin_encoder_1_B,INPUT_PULLUP);
  pinMode(pin_encoder_2_A,INPUT_PULLUP);
  pinMode(pin_encoder_2_B,INPUT_PULLUP);
  pinMode(pin_encoder_3_A,INPUT_PULLUP);
  pinMode(pin_encoder_3_B,INPUT_PULLUP);
  
  pinMode(pin_joystick_btn,INPUT_PULLUP);

  // encoder interrupt
  attachInterrupt(digitalPinToInterrupt(pin_encoder_1_A), ISR_encoder_1_A, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pin_encoder_1_B), ISR_encoder_1_B, CHANGE);

  // set up the packet serial 
  Serial1.begin(115200);
  packetSerial.setStream(&Serial1);
  packetSerial.setPacketHandler(&onPacketReceived);

  // for debugging 
  Serial.begin(20000000);

  // get joystick offset
  delayMicroseconds(5000);
  joystick_offset_x = analogRead(pin_joystick_x);
  joystick_offset_y = analogRead(pin_joystick_y);
  
}

void loop() 
{
  
  // update input sensitivity
  input_sensitivity_xy = (1023-analogRead(pin_pot2))/100; // sensitivity 0-10;
  if(input_sensitivity_xy>9)
    input_sensitivity_xy = 9;
  
  input_sensitivity_z = (1023-analogRead(pin_pot1))/100; // sensitivity 0-10;
  if(input_sensitivity_z>8)
    input_sensitivity_z = 8;
    
  int step = 1 << input_sensitivity_z;  // 2^input_sensitivity_z, integer arithmetic
  if (step > 256) step = 256;
  encoder_step_size = step;

  // display input sensitivity
  display_str[0] = '0' + input_sensitivity_xy;
  display_str[1] = '0' + input_sensitivity_z;
  d.displayString(display_str);

  // read joystick
  joystick_delta_x = analogRead(pin_joystick_x) - joystick_offset_x;
#ifdef REGION_OVERSEAS
  joystick_delta_x = sgn(joystick_delta_x)*max(abs(joystick_delta_x)-joystick_deadband,0)*pow(2,input_sensitivity_xy)/4;
#else
  joystick_delta_x = sgn(joystick_delta_x)*max(abs(joystick_delta_x)-joystick_deadband,0)*pow(2,input_sensitivity_xy)/8;
#endif
  joystick_delta_x = sgn(joystick_delta_x)*min(abs(joystick_delta_x),32767);
  joystick_delta_y = analogRead(pin_joystick_y) - joystick_offset_y;
#ifdef REGION_OVERSEAS
  joystick_delta_y = sgn(joystick_delta_y)*max(abs(joystick_delta_y)-joystick_deadband,0)*pow(2,input_sensitivity_xy)/4;
#else
  joystick_delta_y = sgn(joystick_delta_y)*max(abs(joystick_delta_y)-joystick_deadband,0)*pow(2,input_sensitivity_xy)/8;
#endif
  joystick_delta_y = sgn(joystick_delta_y)*min(abs(joystick_delta_y),32767);

  // read key
  // debouncing to be added

  // send to controller
  int32_t encoder_pos_ = encoder_pos / 4; // reduce resolution
  // align to a multiple of 16 to set the minimum step granularity of the focus wheel
  encoder_pos_ = (encoder_pos_ / 16) * 16;
  tmp_uint32 = twos_complement(encoder_pos_,4); 
  packet[0] = byte(tmp_uint32>>24);
  packet[1] = byte((tmp_uint32>>16)%256);
  packet[2] = byte((tmp_uint32>>8)%256);
  packet[3] = byte(tmp_uint32%256);
  tmp_uint16 = twos_complement(joystick_delta_x,2); 
  packet[4] = byte((tmp_uint16>>8)%256);
  packet[5] = byte(tmp_uint16%256);
  tmp_uint16 = twos_complement(joystick_delta_y,2); 
  packet[6] = byte((tmp_uint16>>8)%256);
  packet[7] = byte(tmp_uint16%256);
  packet[8] = byte(digitalRead(pin_joystick_btn)); // for testing only, to be replaced with joystick_button_pressed

  //  // testing
  //  packet[8] = i_testing++;
  //  if(i_testing==255)
  //    i_testing = 0;

  // CRC-8-CCITT over packet[0..8]; map 0x00 to 0x01 to reserve 0 as the legacy marker
  uint8_t crc = crc8_ccitt(packet, 9);
  if (crc == 0x00) crc = 0x01;
  packet[9] = crc;
  packetSerial.send(packet, JOYSTICK_MSG_LENGTH);

  // process incoming packets
  packetSerial.update();

  // delay
  delayMicroseconds(2000);

  // debug
  Serial.print(encoder_pos_);
  //Serial.print("\t");
  //Serial.print(tmp_uint32);
  Serial.print("\t dx:");
  Serial.print(joystick_delta_x);
  Serial.print("\t dy:");
  Serial.print(joystick_delta_y);
  Serial.print("\t btn:");
  Serial.print(digitalRead(pin_joystick_btn));
  Serial.println("\t");
  // uint64_t tmp = pow(256,4); print does not work
  // Serial.println( tmp );
  
}

// handling packest from the controller
void onPacketReceived(const uint8_t* buffer, size_t size)
{
  
}

// interrupts
void ISR_encoder_1_A()
{
  if(digitalRead(pin_encoder_1_B)==0 && digitalRead(pin_encoder_1_A)==1)
    encoder_pos = encoder_pos + encoder_step_size;
  else if (digitalRead(pin_encoder_1_B)==1 && digitalRead(pin_encoder_1_A)==0)
    encoder_pos = encoder_pos + encoder_step_size;
  else
    encoder_pos = encoder_pos - encoder_step_size;
}

void ISR_encoder_1_B()
{
  if(digitalRead(pin_encoder_1_B)==0 && digitalRead(pin_encoder_1_A)==1 )
    encoder_pos = encoder_pos - encoder_step_size;
  else if (digitalRead(pin_encoder_1_B)==1 && digitalRead(pin_encoder_1_A)==0)
    encoder_pos = encoder_pos - encoder_step_size;
  else
    encoder_pos = encoder_pos + encoder_step_size;
}

// utils
uint32_t twos_complement(long signedLong,int N)
{
  uint32_t NBytesUnsigned = 0;
  if(signedLong>=0)
    NBytesUnsigned = signedLong;
  else
    NBytesUnsigned = signedLong + uint64_t(pow(256,N));
  return NBytesUnsigned;
}

static inline int sgn(int val) {
 if (val < 0) return -1;
 if (val==0) return 0;
 return 1;
}

static uint8_t crc8_ccitt(const uint8_t *data, uint8_t n) {
  uint8_t v = 0;
  for (uint8_t i = 0; i < n; i++)
    v = CRC_TABLE[v ^ data[i]];
  return v;
}
