import ast
from pathlib import Path

import control.objective_changer_constants as constants

MODULE = Path(constants.__file__)


def test_imports_nothing():
    """control._def reaches this module while it is still initializing (spec A §4.1)."""
    tree = ast.parse(MODULE.read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert imports == []


def test_slot_counts():
    assert constants.NIMOTION_TURRET_SLOTS == 4
    assert constants.XERYON_SLOTS == 2


def test_turret_controller_uses_the_shared_constant():
    import control.objective_turret_controller as turret

    assert turret.POSITIONS_PER_REV == constants.NIMOTION_TURRET_SLOTS
