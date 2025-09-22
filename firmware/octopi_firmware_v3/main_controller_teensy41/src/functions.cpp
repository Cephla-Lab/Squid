#include "functions.h"

void set_DAC8050x_gain(uint8_t div, uint8_t gains) 
{
  uint16_t value = 0;
  value = (div << 8) + gains; 
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE2));
  digitalWrite(DAC8050x_CS_pin, LOW);
  SPI.transfer(DAC8050x_GAIN_ADDR);
  SPI.transfer16(value);
  digitalWrite(DAC8050x_CS_pin, HIGH);
  SPI.endTransaction();
}

// REFDIV-E = 0 (no div), BUFF7-GAIN = 0 (no gain) 1 for channel 0-6, 2 for channel 7
void set_DAC8050x_default_gain()
{
  set_DAC8050x_gain(0x00, 0x80);
}

void set_DAC8050x_config()
{
  uint16_t value = 0;
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE2));
  digitalWrite(DAC8050x_CS_pin, LOW);
  SPI.transfer(DAC8050x_CONFIG_ADDR);
  SPI.transfer16(value);
  digitalWrite(DAC8050x_CS_pin, HIGH);
  SPI.endTransaction();
}

void set_DAC8050x_output(int channel, uint16_t value)
{
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE2));
  digitalWrite(DAC8050x_CS_pin, LOW);
  SPI.transfer(DAC8050x_DAC_ADDR + channel);
  SPI.transfer16(value);
  digitalWrite(DAC8050x_CS_pin, HIGH);
  SPI.endTransaction();
}

/***************************************************************************************************/
/*******************************************  LED Array  *******************************************/
/***************************************************************************************************/
void set_all(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  for (int i = 0; i < NUM_LEDS; i++)
    matrix[i].setRGB(r, g, b);
}

void set_left(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  for (int i = 0; i < NUM_LEDS / 2; i++)
    matrix[i].setRGB(r, g, b);
}

void set_right(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  for (int i = NUM_LEDS / 2; i < NUM_LEDS; i++)
    matrix[i].setRGB(r, g, b);
}

void set_top(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  static const int LED_matrix_top[] = {
        0, 1, 2, 3,
        15, 14, 13, 12,
        16, 17, 18, 19, 20, 21,
        39, 38, 37, 36, 35, 34,
        40, 41, 42, 43, 44, 45,
        63, 62, 61, 60, 59, 58,
        64, 65, 66, 67, 68, 69,
        87, 86, 85, 84, 83, 82,
        88, 89, 90, 91, 92, 93,
        111, 110, 109, 108, 107, 106,
        112, 113, 114, 115,
        127, 126, 125, 124};
  for (int i = 0; i < 64; i++)
    matrix[LED_matrix_top[i]].setRGB(r,g,b);
}

void set_bottom(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  static const int LED_matrix_bottom[] = {
        4, 5, 6, 7,
        11, 10, 9, 8,
        22, 23, 24, 25, 26, 27,
        33, 32, 31, 30, 29, 28,
        46, 47, 48, 49, 50, 51,
        57, 56, 55, 54, 53, 52,
        70, 71, 72, 73, 74, 75,
        81, 80, 79, 78, 77, 76,
        94, 95, 96, 97, 98, 99,
        105, 104, 103, 102, 101, 100,
        116, 117, 118, 119,
        123, 122, 121, 120};
  for (int i = 0; i < 64; i++)
    matrix[LED_matrix_bottom[i]].setRGB(r,g,b);
}

void set_low_na(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  // matrix[44].setRGB(r,g,b);
  matrix[45].setRGB(r, g, b);
  matrix[46].setRGB(r, g, b);
  // matrix[47].setRGB(r,g,b);
  matrix[56].setRGB(r, g, b);
  matrix[57].setRGB(r, g, b);
  matrix[58].setRGB(r, g, b);
  matrix[59].setRGB(r, g, b);
  matrix[68].setRGB(r, g, b);
  matrix[69].setRGB(r, g, b);
  matrix[70].setRGB(r, g, b);
  matrix[71].setRGB(r, g, b);
  // matrix[80].setRGB(r,g,b);
  matrix[81].setRGB(r, g, b);
  matrix[82].setRGB(r, g, b);
  // matrix[83].setRGB(r,g,b);
}

void set_left_dot(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  matrix[3].setRGB(r, g, b);
  matrix[4].setRGB(r, g, b);
  matrix[11].setRGB(r, g, b);
  matrix[12].setRGB(r, g, b);
}

void set_right_dot(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b)
{
  matrix[115].setRGB(r, g, b);
  matrix[116].setRGB(r, g, b);
  matrix[123].setRGB(r, g, b);
  matrix[124].setRGB(r, g, b);
}

void clear_matrix(CRGB * matrix)
{
  for (int i = 0; i < NUM_LEDS; i++)
    matrix[i].setRGB(0, 0, 0);
  FastLED.show();
}

void turn_on_LED_matrix_pattern(CRGB * matrix, int pattern, uint8_t led_matrix_r, uint8_t led_matrix_g, uint8_t led_matrix_b)
{

  led_matrix_r = (float(led_matrix_r) / 255) * LED_MATRIX_MAX_INTENSITY;
  led_matrix_g = (float(led_matrix_g) / 255) * LED_MATRIX_MAX_INTENSITY;
  led_matrix_b = (float(led_matrix_b) / 255) * LED_MATRIX_MAX_INTENSITY;

  // clear matrix
  set_all(matrix, 0, 0, 0);

  switch (pattern)
  {
    case ILLUMINATION_SOURCE_LED_ARRAY_FULL:
      set_all(matrix, led_matrix_g * GREEN_ADJUSTMENT_FACTOR, led_matrix_r * RED_ADJUSTMENT_FACTOR, led_matrix_b * BLUE_ADJUSTMENT_FACTOR);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF:
      set_left(matrix, led_matrix_g * GREEN_ADJUSTMENT_FACTOR, led_matrix_r * RED_ADJUSTMENT_FACTOR, led_matrix_b * BLUE_ADJUSTMENT_FACTOR);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF:
      set_right(matrix, led_matrix_g * GREEN_ADJUSTMENT_FACTOR, led_matrix_r * RED_ADJUSTMENT_FACTOR, led_matrix_b * BLUE_ADJUSTMENT_FACTOR);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR:
      set_left(matrix, 0, 0, led_matrix_b * BLUE_ADJUSTMENT_FACTOR);
      set_right(matrix, 0, led_matrix_r * RED_ADJUSTMENT_FACTOR, 0);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA:
      set_low_na(matrix, led_matrix_g * GREEN_ADJUSTMENT_FACTOR, led_matrix_r * RED_ADJUSTMENT_FACTOR, led_matrix_b * BLUE_ADJUSTMENT_FACTOR);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT:
      set_left_dot(matrix, led_matrix_g * GREEN_ADJUSTMENT_FACTOR, led_matrix_r * RED_ADJUSTMENT_FACTOR, led_matrix_b * BLUE_ADJUSTMENT_FACTOR);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT:
      set_right_dot(matrix, led_matrix_g * GREEN_ADJUSTMENT_FACTOR, led_matrix_r * RED_ADJUSTMENT_FACTOR, led_matrix_b * BLUE_ADJUSTMENT_FACTOR);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF:
      set_top(matrix, led_matrix_g*GREEN_ADJUSTMENT_FACTOR, led_matrix_r*RED_ADJUSTMENT_FACTOR, led_matrix_b*BLUE_ADJUSTMENT_FACTOR);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF:
      set_bottom(matrix, led_matrix_g*GREEN_ADJUSTMENT_FACTOR, led_matrix_r*RED_ADJUSTMENT_FACTOR, led_matrix_b*BLUE_ADJUSTMENT_FACTOR);
      break;
  }
  FastLED.show();
}

/***************************************************************************************************/
/************************************ camera trigger and strobe ************************************/
/***************************************************************************************************/
bool trigger_output_level[6] = {HIGH, HIGH, HIGH, HIGH, HIGH, HIGH};
bool control_strobe[6] = {false, false, false, false, false, false};
bool strobe_output_level[6] = {LOW, LOW, LOW, LOW, LOW, LOW};
bool strobe_on[6] = {false, false, false, false, false, false};
unsigned long strobe_delay[6] = {0, 0, 0, 0, 0, 0};
long illumination_on_time[6] = {0, 0, 0, 0, 0, 0};
long timestamp_trigger_rising_edge[6] = {0, 0, 0, 0, 0, 0};
IntervalTimer strobeTimer;

/***************************************************************************************************/
/***************************************** illumination ********************************************/
/***************************************************************************************************/

CRGB matrix[NUM_LEDS] = {0};

void turn_on_illumination()
{
  illumination_is_on = true;
  switch (illumination_source)
  {
    case ILLUMINATION_SOURCE_LED_ARRAY_FULL:
      turn_on_LED_matrix_pattern(matrix, ILLUMINATION_SOURCE_LED_ARRAY_FULL, led_matrix_r, led_matrix_g, led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF:
      turn_on_LED_matrix_pattern(matrix, ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF, led_matrix_r, led_matrix_g, led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF:
      turn_on_LED_matrix_pattern(matrix, ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF, led_matrix_r, led_matrix_g, led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR:
      turn_on_LED_matrix_pattern(matrix, ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR, led_matrix_r, led_matrix_g, led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA:
      turn_on_LED_matrix_pattern(matrix, ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA, led_matrix_r, led_matrix_g, led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT:
      turn_on_LED_matrix_pattern(matrix, ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT, led_matrix_r, led_matrix_g, led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT:
      turn_on_LED_matrix_pattern(matrix, ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT, led_matrix_r, led_matrix_g, led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF:
      turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF,led_matrix_r,led_matrix_g,led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF:
      turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF,led_matrix_r,led_matrix_g,led_matrix_b);
      break;
    case ILLUMINATION_SOURCE_LED_EXTERNAL_FET:
      break;
    case ILLUMINATION_SOURCE_405NM:
      if(digitalRead(LASER_INTERLOCK) == LOW)
        digitalWrite(LASER_405nm, HIGH);
      break;
    case ILLUMINATION_SOURCE_488NM:
      if(digitalRead(LASER_INTERLOCK) == LOW)
        digitalWrite(LASER_488nm, HIGH);
      break;
    case ILLUMINATION_SOURCE_638NM:
      if(digitalRead(LASER_INTERLOCK) == LOW)
        digitalWrite(LASER_638nm, HIGH);
      break;
    case ILLUMINATION_SOURCE_561NM:
      if(digitalRead(LASER_INTERLOCK) == LOW)
        digitalWrite(LASER_561nm, HIGH);
      break;
    case ILLUMINATION_SOURCE_730NM:
      if(digitalRead(LASER_INTERLOCK) == LOW)
        digitalWrite(LASER_730nm, HIGH);
      break;
  }
}

void turn_off_illumination()
{
  switch(illumination_source)
  {
    case ILLUMINATION_SOURCE_LED_ARRAY_FULL:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF:
      clear_matrix(matrix);
      break;
    case ILLUMINATION_SOURCE_LED_EXTERNAL_FET:
      break;
    case ILLUMINATION_SOURCE_405NM:
      digitalWrite(LASER_405nm, LOW);
      break;
    case ILLUMINATION_SOURCE_488NM:
      digitalWrite(LASER_488nm, LOW);
      break;
    case ILLUMINATION_SOURCE_638NM:
      digitalWrite(LASER_638nm, LOW);
      break;
    case ILLUMINATION_SOURCE_561NM:
      digitalWrite(LASER_561nm, LOW);
      break;
    case ILLUMINATION_SOURCE_730NM:
      digitalWrite(LASER_730nm, LOW);
      break;
  }
  illumination_is_on = false;
}

void set_illumination(int source, uint16_t intensity)
{
  illumination_source = source;
  illumination_intensity = intensity * illumination_intensity_factor;
  switch (source)
  {
    case ILLUMINATION_SOURCE_405NM:
      set_DAC8050x_output(0, illumination_intensity);
      break;
    case ILLUMINATION_SOURCE_488NM:
      set_DAC8050x_output(1, illumination_intensity);
      break;
    case ILLUMINATION_SOURCE_638NM:
      set_DAC8050x_output(3, illumination_intensity);
      break;
    case ILLUMINATION_SOURCE_561NM:
      set_DAC8050x_output(2, illumination_intensity);
      break;
    case ILLUMINATION_SOURCE_730NM:
      set_DAC8050x_output(4, illumination_intensity);
      break;
  }
  if (illumination_is_on)
    turn_on_illumination(); //update the illumination
}

void set_illumination_led_matrix(int source, uint8_t r, uint8_t g, uint8_t b)
{
  illumination_source = source;
  led_matrix_r = r;
  led_matrix_g = g;
  led_matrix_b = b;
  if (illumination_is_on)
    turn_on_illumination(); //update the illumination
}

void ISR_strobeTimer()
{
  for (int camera_channel = 0; camera_channel < 6; camera_channel++)
  {
    // strobe pulse
    if (control_strobe[camera_channel])
    {
      if (illumination_on_time[camera_channel] <= 30000)
      {
        // if the illumination on time is smaller than 30 ms, use delayMicroseconds to control the pulse length to avoid pulse length jitter
        if ( ((micros() - timestamp_trigger_rising_edge[camera_channel]) >= strobe_delay[camera_channel]) && strobe_output_level[camera_channel] == LOW )
        {
          turn_on_illumination();
          delayMicroseconds(illumination_on_time[camera_channel]);
          turn_off_illumination();
          control_strobe[camera_channel] = false;
        }
      }
      else
      {
        // start the strobe
        if ( ((micros() - timestamp_trigger_rising_edge[camera_channel]) >= strobe_delay[camera_channel]) && strobe_output_level[camera_channel] == LOW )
        {
          turn_on_illumination();
          strobe_output_level[camera_channel] = HIGH;
        }
        // end the strobe
        if (((micros() - timestamp_trigger_rising_edge[camera_channel]) >= strobe_delay[camera_channel] + illumination_on_time[camera_channel]) && strobe_output_level[camera_channel] == HIGH)
        {
          turn_off_illumination();
          strobe_output_level[camera_channel] = LOW;
          control_strobe[camera_channel] = false;
        }
      }
    }
  }
}

/***************************************************************************************************/
/******************************************* joystick **********************************************/
/***************************************************************************************************/
PacketSerial joystick_packetSerial;

void onJoystickPacketReceived(const uint8_t* buffer, size_t size)
{

  if (size != JOYSTICK_MSG_LENGTH)
  {
    if (DEBUG_MODE)
      Serial.println("! wrong number of bytes received !");
    return;
  }

  if (first_packet_from_joystick_panel)
  {
    focuswheel_pos = int32_t(uint32_t(buffer[0]) << 24 | uint32_t(buffer[1]) << 16 | uint32_t(buffer[2]) << 8 | uint32_t(buffer[3]));
    first_packet_from_joystick_panel = false;
  }
  else
  {
    focusPosition = focusPosition + (int32_t(uint32_t(buffer[0]) << 24 | uint32_t(buffer[1]) << 16 | uint32_t(buffer[2]) << 8 | uint32_t(buffer[3])) - focuswheel_pos);
    focuswheel_pos = int32_t(uint32_t(buffer[0]) << 24 | uint32_t(buffer[1]) << 16 | uint32_t(buffer[2]) << 8 | uint32_t(buffer[3]));
  }

  joystick_delta_x = JOYSTICK_SIGN_X * int16_t( uint16_t(buffer[4]) * 256 + uint16_t(buffer[5]) );
  joystick_delta_y = JOYSTICK_SIGN_Y * int16_t( uint16_t(buffer[6]) * 256 + uint16_t(buffer[7]) );
  btns = buffer[8];

  // temporary
  /*
    if(btns & 0x01)
    {
    joystick_button_pressed = true;
    joystick_button_pressed_timestamp = millis();
    // to add: ACK for the joystick panel
    }
  */

  flag_read_joystick = true;

}

/***************************************************************************************************/
/*********************************************  utils  *********************************************/
/***************************************************************************************************/
long signed2NBytesUnsigned(long signedLong, int N)
{
  long NBytesUnsigned = signedLong + pow(256L, N) / 2;
  //long NBytesUnsigned = signedLong + 8388608L;
  return NBytesUnsigned;
}

int sgn(int val) {
  if (val < 0) return -1;
  if (val == 0) return 0;
  return 1;
}
