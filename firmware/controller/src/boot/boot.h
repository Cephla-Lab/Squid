/**
 * Boot / fault-diagnostics module (design D9 robustness package).
 *
 * On power-up the firmware must, in order: enter a safe state (outputs off,
 * motors disabled) BEFORE anything else, capture why it reset, bump a
 * persistent boot counter, mint a session nonce so the host can detect a
 * mid-session reboot, and arm the hardware watchdog. During the loop it kicks
 * the watchdog, records loop/ISR timing watermarks, and appends faults to a
 * persistent ring that survives a crash/watchdog reset for post-mortem DIAG.
 *
 * The core logic here is pure and native-tested against an injected BootHal.
 * The RT1062/Arduino specifics (WDOG1 registers, SRC_SRSR interpretation,
 * EEPROM emulation, DWT cycle counter) live in boot_bind_teensy41.cpp and are
 * bench-verified in Phase C (design risk #1: RTWDOG vs WDOG1).
 *
 * No heap; EEPROM writes happen only on boot and on fault (rare — no wear
 * concern, design risk #2).
 */

#ifndef BOOT_BOOT_H
#define BOOT_BOOT_H

#include <stddef.h>
#include <stdint.h>

namespace boot {

enum ResetCause : uint8_t {
    RESET_UNKNOWN = 0,
    RESET_POWER_ON = 1,
    RESET_WATCHDOG = 2,
    RESET_SOFTWARE = 3,
    RESET_EXTERNAL = 4,
    RESET_LOCKUP = 5,
};

// Hardware abstraction — the binding provides these; tests fake them.
class BootHal {
public:
    virtual ~BootHal() {}

    // Force hardware into a safe state (all outputs off, motors disabled).
    virtual void safe_state() = 0;

    virtual uint8_t eeprom_read(uint16_t addr) = 0;
    virtual void eeprom_write(uint16_t addr, uint8_t value) = 0;

    // Reset cause for this boot, already mapped from SRC_SRSR by the binding.
    virtual uint8_t read_reset_cause() = 0;
    virtual void clear_reset_cause() = 0;

    virtual void watchdog_arm(uint32_t timeout_ms) = 0;
    virtual void watchdog_kick() = 0;

    // Free-running counter (DWT CYCCNT on hardware) used as nonce entropy.
    virtual uint32_t cycle_counter() = 0;
};

struct FaultRecord {
    uint32_t uptime_ms;
    uint8_t code;
    uint8_t detail;
};

class Boot {
public:
    static const uint8_t kFaultRingSize = 16;
    static const uint32_t kWatchdogTimeoutMs = 2000;

    explicit Boot(BootHal& hal);

    // Power-up sequence. safe_state() runs FIRST, then reset-cause capture,
    // boot-count increment, nonce mint, watchdog arm. Idempotent EEPROM init:
    // the persistent fault ring is preserved across reboots.
    void begin();

    uint8_t reset_cause() const { return reset_cause_; }
    uint32_t boot_count() const { return boot_count_; }
    uint32_t session_nonce() const { return session_nonce_; }

    void kick_watchdog() { hal_.watchdog_kick(); }

    // Append a fault to the persistent ring (advances head, bumps the
    // saturating total). uptime_ms is supplied by the caller (millis()).
    void fault_append(uint8_t code, uint8_t detail, uint32_t uptime_ms);
    uint8_t fault_count() const { return fault_total_; }  // saturating at 255
    uint8_t fault_head() const { return fault_head_; }
    // Read the k-th newest fault (0 = newest). False if k is out of range.
    bool fault_get(uint8_t k, FaultRecord& out) const;

    void note_loop_us(uint32_t us) {
        if (us > loop_max_us_) loop_max_us_ = us;
    }
    void note_isr_us(uint32_t us) {
        if (us > isr_max_us_) isr_max_us_ = us;
    }
    uint32_t loop_max_us() const { return loop_max_us_; }
    uint32_t isr_max_us() const { return isr_max_us_; }

private:
    // EEPROM layout (bytes).
    static const uint16_t kEeMagic = 0;        // 2 bytes
    static const uint16_t kEeBootCount = 2;    // 4 bytes (LE)
    static const uint16_t kEeFaultHead = 6;    // 1 byte
    static const uint16_t kEeFaultTotal = 7;   // 1 byte
    static const uint16_t kEeFaultRing = 8;    // kFaultRingSize * kFaultEntryBytes
    static const uint16_t kFaultEntryBytes = 6;  // u32 uptime + u8 code + u8 detail
    static const uint8_t kMagic0 = 0xB0;
    static const uint8_t kMagic1 = 0x07;

    uint32_t ee_read_u32(uint16_t addr) const;
    void ee_write_u32(uint16_t addr, uint32_t value);

    BootHal& hal_;
    uint8_t reset_cause_;
    uint32_t boot_count_;
    uint32_t session_nonce_;
    uint8_t fault_head_;
    uint8_t fault_total_;
    uint32_t loop_max_us_;
    uint32_t isr_max_us_;
};

}  // namespace boot

#endif  // BOOT_BOOT_H
