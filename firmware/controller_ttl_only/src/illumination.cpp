/**
 * TTL-Only Firmware - Illumination Control Implementation
 */

#include "illumination.h"
#include <SPI.h>

/***************************************************************************************************/
/********************************************** DAC ************************************************/
/***************************************************************************************************/

void init_dac()
{
    pinMode(DAC8050x_CS_pin, OUTPUT);
    digitalWrite(DAC8050x_CS_pin, HIGH);

    // Initialize SPI
    SPI.begin();
    delayMicroseconds(1000);

    // Configure DAC
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE2));
    digitalWrite(DAC8050x_CS_pin, LOW);
    SPI.transfer(DAC8050x_CONFIG_ADDR);
    SPI.transfer16(0);  // Default config
    digitalWrite(DAC8050x_CS_pin, HIGH);
    SPI.endTransaction();

    // Set default gain: REFDIV-E = 0 (no div), gain = 2x for channel 7
    set_dac_gain(0x00, 0x80);
}

void set_dac_gain(uint8_t div, uint8_t gains)
{
    uint16_t value = (div << 8) + gains;
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE2));
    digitalWrite(DAC8050x_CS_pin, LOW);
    SPI.transfer(DAC8050x_GAIN_ADDR);
    SPI.transfer16(value);
    digitalWrite(DAC8050x_CS_pin, HIGH);
    SPI.endTransaction();
}

void set_dac_output(int channel, uint16_t value)
{
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE2));
    digitalWrite(DAC8050x_CS_pin, LOW);
    SPI.transfer(DAC8050x_DAC_ADDR + channel);
    SPI.transfer16(value);
    digitalWrite(DAC8050x_CS_pin, HIGH);
    SPI.endTransaction();
}

/***************************************************************************************************/
/***************************************** Illumination ********************************************/
/***************************************************************************************************/

void turn_off_all_lasers()
{
    digitalWrite(LASER_405nm, LOW);
    digitalWrite(LASER_488nm, LOW);
    digitalWrite(LASER_561nm, LOW);
    digitalWrite(LASER_638nm, LOW);
    digitalWrite(LASER_730nm, LOW);
}

void turn_on_illumination()
{
    illumination_is_on = true;

    switch (illumination_source)
    {
    case ILLUMINATION_SOURCE_405NM:
        digitalWrite(LASER_405nm, HIGH);
        break;
    case ILLUMINATION_SOURCE_488NM:
        digitalWrite(LASER_488nm, HIGH);
        break;
    case ILLUMINATION_SOURCE_561NM:
        digitalWrite(LASER_561nm, HIGH);
        break;
    case ILLUMINATION_SOURCE_638NM:
        digitalWrite(LASER_638nm, HIGH);
        break;
    case ILLUMINATION_SOURCE_730NM:
        digitalWrite(LASER_730nm, HIGH);
        break;
    default:
        // Unknown source - ignore (LED matrix patterns not supported)
        break;
    }
}

void turn_off_illumination()
{
    switch (illumination_source)
    {
    case ILLUMINATION_SOURCE_405NM:
        digitalWrite(LASER_405nm, LOW);
        break;
    case ILLUMINATION_SOURCE_488NM:
        digitalWrite(LASER_488nm, LOW);
        break;
    case ILLUMINATION_SOURCE_561NM:
        digitalWrite(LASER_561nm, LOW);
        break;
    case ILLUMINATION_SOURCE_638NM:
        digitalWrite(LASER_638nm, LOW);
        break;
    case ILLUMINATION_SOURCE_730NM:
        digitalWrite(LASER_730nm, LOW);
        break;
    default:
        // Unknown source - ignore
        break;
    }
    illumination_is_on = false;
}

void set_illumination(int source, uint16_t intensity)
{
    illumination_source = source;
    illumination_intensity = intensity * illumination_intensity_factor;

    // Set DAC output for intensity control
    // DAC channel mapping:
    //   405nm -> channel 0
    //   488nm -> channel 1
    //   561nm -> channel 2
    //   638nm -> channel 3
    //   730nm -> channel 4
    switch (source)
    {
    case ILLUMINATION_SOURCE_405NM:
        set_dac_output(0, illumination_intensity);
        break;
    case ILLUMINATION_SOURCE_488NM:
        set_dac_output(1, illumination_intensity);
        break;
    case ILLUMINATION_SOURCE_561NM:
        set_dac_output(2, illumination_intensity);
        break;
    case ILLUMINATION_SOURCE_638NM:
        set_dac_output(3, illumination_intensity);
        break;
    case ILLUMINATION_SOURCE_730NM:
        set_dac_output(4, illumination_intensity);
        break;
    default:
        // Unknown source - ignore
        break;
    }

    // If illumination is already on, update it
    if (illumination_is_on)
        turn_on_illumination();
}
