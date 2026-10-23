#include "panel_lock.h"

void pl_apply(bool locked, JoystickPanelInput *in) {
    if (!locked)
        return;
    in->delta_x = 0;
    in->delta_y = 0;
    in->focus_delta = 0;
}
