/**
 * Boot / fault-diagnostics core. See boot.h for the contract.
 */

#include "boot/boot.h"

namespace boot {

namespace {

// 32-bit mix (Knuth multiplicative + MurmurHash3 finalizer). Bijective, so a
// distinct boot_count always yields a distinct nonce even with identical
// cycle-counter entropy.
uint32_t mix_nonce(uint32_t boot_count, uint32_t cycle) {
    uint32_t x = boot_count * 2654435761u + cycle;
    x ^= x >> 16;
    x *= 0x85ebca6bu;
    x ^= x >> 13;
    x *= 0xc2b2ae35u;
    x ^= x >> 16;
    return x ? x : 0xA5A5A5A5u;  // never zero (0 would look like "no session")
}

}  // namespace

Boot::Boot(BootHal& hal)
    : hal_(hal),
      reset_cause_(RESET_UNKNOWN),
      boot_count_(0),
      session_nonce_(0),
      fault_head_(0),
      fault_total_(0),
      loop_max_us_(0),
      isr_max_us_(0) {}

uint32_t Boot::ee_read_u32(uint16_t addr) const {
    return (uint32_t)hal_.eeprom_read(addr) | ((uint32_t)hal_.eeprom_read(addr + 1) << 8) |
           ((uint32_t)hal_.eeprom_read(addr + 2) << 16) |
           ((uint32_t)hal_.eeprom_read(addr + 3) << 24);
}

void Boot::ee_write_u32(uint16_t addr, uint32_t value) {
    hal_.eeprom_write(addr, (uint8_t)(value & 0xFF));
    hal_.eeprom_write(addr + 1, (uint8_t)((value >> 8) & 0xFF));
    hal_.eeprom_write(addr + 2, (uint8_t)((value >> 16) & 0xFF));
    hal_.eeprom_write(addr + 3, (uint8_t)((value >> 24) & 0xFF));
}

void Boot::begin() {
    // 1. Safe state FIRST — outputs off, motors disabled — before touching
    //    EEPROM, the reset-cause register, or the watchdog.
    hal_.safe_state();

    // 2. Capture the reset cause exactly once, then clear it for the next boot.
    reset_cause_ = hal_.read_reset_cause();
    hal_.clear_reset_cause();

    // 3. Initialize EEPROM on first-ever boot; otherwise preserve the persistent
    //    fault ring across reboots.
    bool initialized = (hal_.eeprom_read(kEeMagic) == kMagic0) &&
                       (hal_.eeprom_read(kEeMagic + 1) == kMagic1);
    if (!initialized) {
        hal_.eeprom_write(kEeMagic, kMagic0);
        hal_.eeprom_write(kEeMagic + 1, kMagic1);
        ee_write_u32(kEeBootCount, 0);
        hal_.eeprom_write(kEeFaultHead, 0);
        hal_.eeprom_write(kEeFaultTotal, 0);
    }

    // 4. Increment and persist the boot counter.
    boot_count_ = ee_read_u32(kEeBootCount) + 1;
    ee_write_u32(kEeBootCount, boot_count_);

    // 5. Mint a session nonce (non-zero, differs across boots).
    session_nonce_ = mix_nonce(boot_count_, hal_.cycle_counter());

    // 6. Arm the hardware watchdog.
    hal_.watchdog_arm(kWatchdogTimeoutMs);

    // Load the persistent fault-ring cursor.
    fault_head_ = hal_.eeprom_read(kEeFaultHead);
    fault_total_ = hal_.eeprom_read(kEeFaultTotal);

    loop_max_us_ = 0;
    isr_max_us_ = 0;
}

void Boot::fault_append(uint8_t code, uint8_t detail, uint32_t uptime_ms) {
    uint16_t base = kEeFaultRing + (uint16_t)fault_head_ * kFaultEntryBytes;
    ee_write_u32(base, uptime_ms);
    hal_.eeprom_write(base + 4, code);
    hal_.eeprom_write(base + 5, detail);

    fault_head_ = (uint8_t)((fault_head_ + 1) % kFaultRingSize);
    if (fault_total_ < 255) {
        ++fault_total_;
    }
    hal_.eeprom_write(kEeFaultHead, fault_head_);
    hal_.eeprom_write(kEeFaultTotal, fault_total_);
}

bool Boot::fault_get(uint8_t k, FaultRecord& out) const {
    uint8_t avail = (fault_total_ < kFaultRingSize) ? fault_total_ : kFaultRingSize;
    if (k >= avail) {
        return false;
    }
    // Newest is at (head - 1); the k-th newest is (head - 1 - k), modulo size.
    uint8_t idx = (uint8_t)((fault_head_ + kFaultRingSize - 1 - k) % kFaultRingSize);
    uint16_t base = kEeFaultRing + (uint16_t)idx * kFaultEntryBytes;
    out.uptime_ms = ee_read_u32(base);
    out.code = hal_.eeprom_read(base + 4);
    out.detail = hal_.eeprom_read(base + 5);
    return true;
}

}  // namespace boot
