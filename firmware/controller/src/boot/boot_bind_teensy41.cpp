/**
 * Teensy 4.1 / i.MX RT1062 binding for the boot module (BootHal).
 *
 * NOT compiled in the native test build (excluded by the platformio native
 * src filter). Exercised on hardware in Phase C/D — NOT natively.
 *
 * DESIGN RISK #1 (bench-verify in Phase C): this uses WDOG1 (the i.MX windowed
 * watchdog exposed by the Teensy core; RTWDOG is not exposed in imxrt.h). The
 * exact WDOG1_WCR field encoding, the SRC_SRSR reset-cause bit mapping, and the
 * ~2 s timeout must be confirmed on hardware before Phase C wires this to the
 * real serial loop. The pure core (boot.cpp) is binding-agnostic and fully
 * native-tested; only these register pokes are unverified.
 */

#if defined(ARDUINO) || defined(ARDUINO_TEENSY41) || defined(__IMXRT1062__)

#include <Arduino.h>
#include <EEPROM.h>

#include "boot/boot.h"

namespace boot {

class TeensyBootHal : public BootHal {
public:
    void safe_state() override {
        // TODO(Phase C): drive all illumination TTL/DAC/LED-matrix outputs low
        // and disable the stepper drivers here, using the app's pin map. Left
        // as a documented stub until Phase C owns the output inventory.
    }

    uint8_t eeprom_read(uint16_t addr) override { return EEPROM.read(addr); }
    void eeprom_write(uint16_t addr, uint8_t value) override {
        EEPROM.update(addr, value);  // update() skips the write if unchanged (less wear)
    }

    uint8_t read_reset_cause() override {
        const uint32_t srsr = SRC_SRSR;
        // Priority: watchdog > software > lockup > power-on/external.
        if (srsr & (SRC_SRSR_WDOG_RST_B | SRC_SRSR_WDOG3_RST_B)) {
            return RESET_WATCHDOG;
        }
        if (srsr & SRC_SRSR_IPP_USER_RESET_B) {
            return RESET_SOFTWARE;
        }
        if (srsr & SRC_SRSR_LOCKUP_SYSRESETREQ) {
            return RESET_LOCKUP;
        }
        if (srsr & SRC_SRSR_IPP_RESET_B) {
            return RESET_POWER_ON;
        }
        return RESET_UNKNOWN;
    }

    void clear_reset_cause() override {
        SRC_SRSR = SRC_SRSR;  // SRSR bits are write-1-to-clear
    }

    void watchdog_arm(uint32_t timeout_ms) override {
        // WDOG1 WCR: WT[15:8] timeout = (WT + 1) * 0.5 s; bit2 = WDE (enable).
        // WDE is write-once until the next reset. ~2 s -> WT = 3.
        uint32_t wt = (timeout_ms / 500);
        if (wt > 0) {
            wt -= 1;
        }
        if (wt > 0xFF) {
            wt = 0xFF;
        }
        WDOG1_WCR = (uint16_t)((wt << 8) | (1 << 2) /* WDE */);
    }

    void watchdog_kick() override {
        // WDOG1 service (refresh) sequence.
        WDOG1_WSR = 0x5555;
        WDOG1_WSR = 0xAAAA;
    }

    uint32_t cycle_counter() override {
        return ARM_DWT_CYCCNT;  // enabled below in bind_boot_hal()
    }
};

// Singleton binding (no heap). Phase C passes this to a Boot instance.
static TeensyBootHal g_boot_hal;

BootHal& bind_boot_hal() {
    // Ensure the DWT cycle counter is running (used for nonce entropy/timing).
    ARM_DEMCR |= ARM_DEMCR_TRCENA;
    ARM_DWT_CTRL |= 1;  // CYCCNTENA
    return g_boot_hal;
}

}  // namespace boot

#endif  // ARDUINO / __IMXRT1062__
