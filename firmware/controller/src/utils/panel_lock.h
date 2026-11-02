#ifndef PANEL_LOCK_H_
#define PANEL_LOCK_H_

#include "joystick_panel.h"

// SET_PANEL_LOCK (firmware 1.8): while the host holds the control panel locked (a Z lift system at its loading
// position), an accepted panel packet moves nothing. Pure helper, host-tested.

// Applied to every accepted packet before it is used: when locked, the joystick reads centred and the wheel delta is
// dropped. The link has already taken the packet's wheel position as its baseline, so nothing is saved up for the
// unlock. The buttons pass through.
void pl_apply(bool locked, JoystickPanelInput *in);

#endif /* PANEL_LOCK_H_ */
