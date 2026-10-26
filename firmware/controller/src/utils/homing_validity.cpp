#include "homing_validity.h"
#include "constants_protocol.h"

static void clear_axis(HomingValidity *hv, uint8_t axis) {
    hv->homed[axis] = false;
    hv->armed[axis] = false;
}

static void clear_all_axes(HomingValidity *hv) {
    for (uint8_t a = 0; a < 3; a++) clear_axis(hv, a);
}

void hv_power_up(HomingValidity *hv) {
    clear_all_axes(hv);
    for (uint8_t a = 0; a < 3; a++) hv->driver_enabled[a] = true;
    hv->signature_stored = false;
    hv->signature = 0;
    hv->signature_format = 0;
    for (uint8_t s = 0; s < HV_CFG_SLOTS; s++) {
        for (uint8_t a = 0; a < 3; a++) {
            hv->cfg_seen[s][a] = false;
            for (uint8_t i = 0; i < 4; i++) hv->cfg_bytes[s][a][i] = 0;
        }
    }
}

void hv_home_start(HomingValidity *hv, uint8_t axis, bool power_good) {
    if (axis > 2) return;
    hv->homed[axis] = false;
    hv->armed[axis] = power_good && hv->driver_enabled[axis];
}

void hv_home_complete(HomingValidity *hv, uint8_t axis, bool power_good) {
    if (axis > 2) return;
    hv->homed[axis] = hv->armed[axis] && power_good && hv->driver_enabled[axis];
    hv->armed[axis] = false;
}

void hv_invalidate_axis(HomingValidity *hv, uint8_t axis) {
    if (axis > 2) return;
    clear_axis(hv, axis);
}

void hv_driver_enable(HomingValidity *hv, uint8_t axis, bool enabled) {
    if (axis > 2) return;
    hv->driver_enabled[axis] = enabled;
    if (!enabled) clear_axis(hv, axis);
}

void hv_power_good(HomingValidity *hv, bool power_good) {
    if (!power_good) clear_all_axes(hv);
}

void hv_initialize(HomingValidity *hv) {
    clear_all_axes(hv);
    hv->signature_stored = false;
    hv->signature = 0;
    hv->signature_format = 0;
    for (uint8_t a = 0; a < 3; a++) hv->driver_enabled[a] = true;
}

void hv_store_signature(HomingValidity *hv, uint32_t signature, uint8_t format) {
    // An emptied signature counts as different: a configuration change must not let old flags be vouched for again.
    if (!hv->signature_stored || hv->signature != signature || hv->signature_format != format) clear_all_axes(hv);
    hv->signature = signature;
    hv->signature_format = format;
    hv->signature_stored = true;
}

void hv_config_received(HomingValidity *hv, uint8_t slot, uint8_t axis, const uint8_t bytes[4]) {
    if (slot >= HV_CFG_SLOTS || axis > 2) return;
    bool changed = !hv->cfg_seen[slot][axis];
    for (uint8_t i = 0; i < 4; i++) {
        if (hv->cfg_bytes[slot][axis][i] != bytes[i]) changed = true;
        hv->cfg_bytes[slot][axis][i] = bytes[i];
    }
    hv->cfg_seen[slot][axis] = true;
    if (changed) hv->signature_stored = false;
}

uint8_t hv_verdict(const HomingValidity *hv, uint32_t signature, uint8_t format, uint8_t required_mask,
                   uint8_t at_rest_mask, bool power_good, bool tmc_config_ok) {
    if (!hv->signature_stored) return STARTUP_VERDICT_NO_SIGNATURE;
    if (hv->signature != signature || hv->signature_format != format) return STARTUP_VERDICT_SIGNATURE_MISMATCH;
    if (!(power_good && tmc_config_ok && hv->driver_enabled[0] && hv->driver_enabled[1] && hv->driver_enabled[2]))
        return STARTUP_VERDICT_UNHEALTHY;
    if (at_rest_mask != 0x07) return STARTUP_VERDICT_MOVING;
    for (uint8_t a = 0; a < 3; a++) {
        if ((required_mask & (1u << a)) && !hv->homed[a]) return STARTUP_VERDICT_NOT_HOMED;
    }
    return STARTUP_VERDICT_WARM;
}

void hv_consume_power_drop(HomingValidity *hv, volatile bool *drop_latched) {
    if (!*drop_latched) return;
    *drop_latched = false;  // before clearing the flags: a drop latched again after this clears them again next time
    hv_power_good(hv, false);
}

uint8_t hv_packet_status(bool checksum_error, bool verdict_pending, uint8_t verdict, bool in_progress,
                         uint8_t execution_status) {
    if (checksum_error) return CMD_CHECKSUM_ERROR;
    if (verdict_pending) return uint8_t(STARTUP_VERDICT_STATUS_BASE + verdict);
    if (in_progress) return IN_PROGRESS;
    return execution_status;
}
