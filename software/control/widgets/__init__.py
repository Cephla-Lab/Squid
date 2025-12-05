# GUI widgets module
from control.widgets.base import (
    error_dialog,
    check_space_available_with_error_dialog,
    WrapperWindow,
    CollapsibleGroupBox,
    PandasTableModel,
)
from control.widgets.config import (
    ConfigEditor,
    ConfigEditorBackwardsCompatible,
    ProfileWidget,
)

__all__ = [
    "error_dialog",
    "check_space_available_with_error_dialog",
    "WrapperWindow",
    "CollapsibleGroupBox",
    "PandasTableModel",
    "ConfigEditor",
    "ConfigEditorBackwardsCompatible",
    "ProfileWidget",
]
