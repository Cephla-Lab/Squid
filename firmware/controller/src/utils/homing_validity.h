#ifndef HOMING_VALIDITY_H_
#define HOMING_VALIDITY_H_

#include <stdint.h>

// Homing validity (firmware 1.9): per-axis homed flags, arming, one configuration signature and the startup verdict.
// Pure logic, host-tested; no hardware calls. Axes are PROTOCOL numbers (0 = X, 1 = Y, 2 = Z), not the firmware's
// internal array indices; the glue converts.

// Covered configuration: each slot, per axis, holds 4 bytes as last received from the host.
enum HvConfigSlot : uint8_t {
    HV_CFG_PITCH,
    HV_CFG_DRIVER,
    HV_CFG_VEL_ACC,
    HV_CFG_HOME_MARGIN,
    HV_CFG_LIMIT_SWITCH,
    HV_CFG_SLOTS
};

struct HomingValidity {
    bool homed[3];
    bool armed[3];  // a home started with power good and the driver enabled, and nothing has broken that since
    bool driver_enabled[3];
    bool signature_stored;
    uint32_t signature;
    uint8_t signature_format;
    bool cfg_seen[HV_CFG_SLOTS][3];
    uint8_t cfg_bytes[HV_CFG_SLOTS][3][4];
};

void hv_power_up(HomingValidity *hv);
void hv_home_start(HomingValidity *hv, uint8_t axis, bool power_good);
void hv_home_complete(HomingValidity *hv, uint8_t axis, bool power_good);
void hv_invalidate_axis(HomingValidity *hv, uint8_t axis);
void hv_driver_enable(HomingValidity *hv, uint8_t axis, bool enabled);
void hv_power_good(HomingValidity *hv, bool power_good);
void hv_initialize(HomingValidity *hv);
void hv_store_signature(HomingValidity *hv, uint32_t signature, uint8_t format);
void hv_config_received(HomingValidity *hv, uint8_t slot, uint8_t axis, const uint8_t bytes[4]);

// First failing check wins: no signature, signature/format differs, unhealthy (power, TMC config, any driver
// disabled), moving (at_rest_mask != 0b111), a required axis not homed; otherwise warm. Returns STARTUP_VERDICT_*.
uint8_t hv_verdict(const HomingValidity *hv, uint32_t signature, uint8_t format, uint8_t required_mask,
                   uint8_t at_rest_mask, bool power_good, bool tmc_config_ok);

// A power-good drop latched by the PG falling-edge interrupt: if the latch is set, clear it, then clear every homed
// flag (hv_power_good(false)). Catches a drop shorter than one loop pass, which the per-pass poll can miss.
void hv_consume_power_drop(HomingValidity *hv, volatile bool *drop_latched);

// The status packet's status byte (byte 1). Precedence: checksum error (CMD_CHECKSUM_ERROR); a pending startup
// verdict (STARTUP_VERDICT_STATUS_BASE + verdict); IN_PROGRESS; the last command's execution status.
uint8_t hv_packet_status(bool checksum_error, bool verdict_pending, uint8_t verdict, bool in_progress,
                         uint8_t execution_status);

#endif /* HOMING_VALIDITY_H_ */
