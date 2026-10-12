"""Slot counts of the motorized objective changers.

Imports nothing. control._def reaches this module (through control.objectives_config)
while it is still initializing, and the changer controllers import it as well. Reading
the count from a controller instead would be order-dependent: the turret controller
imports squid.abc, which imports squid.config, which imports control._def.
"""

NIMOTION_TURRET_SLOTS = 4
XERYON_SLOTS = 2
