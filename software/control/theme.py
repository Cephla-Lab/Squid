"""Theme tokens and their rendering to an application stylesheet, a QPalette and recoloured indicator icons.

Light and Dark are the "soft native" skin; Classic is today's look (no stylesheet, default palette) and only
contributes the literal row colours the code uses today. Every length here is a logical px at 96 dpi; the
stylesheet scales lengths with ``scale_factor``.

Keep this module importable with only the standard library, qtpy and pyqtgraph: no ``control._def``, no
``squid.*`` and no numpy at module scope. All Qt access goes through qtpy so either binding works.
"""

import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Optional

from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QFont, QFontDatabase, QPalette
from qtpy.QtWidgets import QApplication, QWidget

THEMES = ("light", "dark", "classic")

_ICON_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "icon" / "theme"


@dataclass(frozen=True)
class Tokens:
    # Chrome colours (the spec's Light/Dark tables).
    ground: str
    surface: str
    header: str
    border: str
    text: str
    muted: str
    input_bg: str
    input_border: str
    input_border_bottom: str
    button_bg: str
    button_border: str
    accent: str
    accent_text: str
    accent_soft: str
    band: str
    band_border: str
    tab_tray: str
    tab_selected: str
    warning_bg: str
    warning_text: str
    warning_border: str
    thumb: str
    # QSS has no box-shadow: a 1 px bottom edge one step darker than ``border`` stands in for it.
    shadow: str
    # Status text and banners, pinned per theme (warning_text is too soft for errors in Light).
    danger_text: str
    success_text: str
    banner_bg: str
    # Table/list row backgrounds painted by code (Classic: today's literals).
    row_disabled: str
    row_highlight: str
    row_running: str
    row_marked: str
    row_invalid: str
    row_conflict: str
    row_text_muted: str
    # Geometry, logical px at 96 dpi.
    control_height: int
    control_radius: int
    tray_radius: int
    card_radius: int
    checkbox_size: int


LIGHT = Tokens(
    ground="#f3f3f3",
    surface="#fbfbfb",
    header="#f6f6f6",
    border="#e5e5e5",
    text="#1a1a1a",
    muted="#5f5f5f",
    input_bg="#ffffff",
    input_border="#e0e0e0",
    input_border_bottom="#8a8a8a",
    button_bg="#fbfbfb",
    button_border="#d9d9d9",
    accent="#0067c0",
    accent_text="#ffffff",
    accent_soft="#e8f1fb",
    band="#f7f9fc",
    band_border="#e5e5e5",
    tab_tray="#ececec",
    tab_selected="#ffffff",
    warning_bg="#fff4ce",
    warning_text="#8a5a00",
    warning_border="#f5dd9a",
    thumb="#ffffff",
    shadow="#d9d9d9",
    danger_text="#b91c1c",
    success_text="#2e7d32",
    banner_bg="#b91c1c",
    row_disabled="#f3f3f3",
    row_highlight="#e8f1fb",
    row_running="#e3f6e8",
    row_marked="#fff4ce",
    row_invalid="#fde7e9",
    row_conflict="#fbe4c8",
    row_text_muted="#5f5f5f",
    control_height=26,
    control_radius=4,
    tray_radius=6,
    card_radius=8,
    checkbox_size=14,
)

DARK = Tokens(
    ground="#202020",
    surface="#2b2b2b",
    header="#272727",
    border="#3a3a3a",
    text="#f0f0f0",
    muted="#a6a6a6",
    input_bg="#333333",
    input_border="#454545",
    input_border_bottom="#9a9a9a",
    button_bg="#353535",
    button_border="#474747",
    accent="#4cc2ff",
    accent_text="#0b1a24",
    accent_soft="rgba(76,194,255,0.16)",
    band="#30343a",
    band_border="#3d4148",
    tab_tray="#262626",
    tab_selected="#3c3c3c",
    warning_bg="#3d3200",
    warning_text="#ffd45c",
    warning_border="#5a4a00",
    thumb="#2b2b2b",
    shadow="#1a1a1a",
    danger_text="#ff8a80",
    success_text="#7bd88f",
    banner_bg="#7f1d1d",
    row_disabled="#3a3a3a",
    row_highlight="#1f3a4d",
    row_running="#1f3d2a",
    row_marked="#3d3200",
    row_invalid="#4a2328",
    row_conflict="#4a3318",
    row_text_muted="#a6a6a6",
    control_height=26,
    control_radius=4,
    tray_radius=6,
    card_radius=8,
    checkbox_size=14,
)

# Classic never gets a stylesheet; only the row tokens and input_bg are read, and they are today's literals.
CLASSIC = replace(
    LIGHT,
    input_bg="#ffffff",
    row_disabled="#f0f0f0",
    row_highlight="#f0f0ff",
    row_running="#c8ffc8",
    row_marked="#fff4b4",
    row_invalid="#ffcdcd",
    row_conflict="#ffd2d2",
    row_text_muted="#808080",
)

# Lengths the stylesheet uses beyond the geometry tokens, logical px at 96 dpi. Input and button paddings are
# tight on purpose: they keep size hints within a few px of Fusion's so the Controls dock does not widen.
# control_height is not applied as a min-height for the same reason.
_LENGTHS = {
    "input_pad_y": 1,
    "input_pad_x": 2,
    "button_pad_y": 2,
    "button_pad_x": 3,
    "tab_pad_y": 2,
    "tab_pad_x": 10,
    "tab_gap": 2,
    "menu_pad_y": 4,
    "menu_pad_x": 20,
    "tip_pad": 4,
    "scrollbar": 12,
    "scrollbar_inset": 3,
    "scrollbar_handle_min": 24,
    "slider_groove": 4,
    "slider_handle": 10,
    "slider_ring": 3,
    "button_width": 12,
}

# With a QSS border, QGroupBox loses Fusion's title reservation; the title then sits in this top margin.
# In em so it follows the font: 1.2em puts the first child where Fusion does (Segoe UI 8 at 144 dpi: y 43 both).
_GROUP_TITLE_BAND = "1.2em"

_ICONS = {"arrow_down.svg": "muted", "arrow_up.svg": "muted", "check.svg": "accent_text", "radio.svg": "accent_text"}

# One rule per line where it fits, grouped by widget family. A new role is one added line.
_BASE_QSS = """
/* Base colours */
QWidget {{ color: {text}; }}
QWidget:disabled {{ color: {disabled_text}; }}

/* Window chrome on ground and surface */
QMainWindow {{ background: {ground}; }}
QMainWindow::separator {{ background: {ground}; }}
QMenuBar {{ background: {ground}; color: {text}; }}
QMenuBar::item {{ background: transparent; padding: {menu_pad_y} {tab_pad_x}; border-radius: {control_radius}; }}
QMenuBar::item:selected {{ background: {accent_soft}; }}
QMenu {{ background: {surface}; color: {text}; border: {hair} solid {border}; }}
QMenu::item {{ padding: {menu_pad_y} {menu_pad_x}; }}
QMenu::item:selected {{ background: {accent_soft}; color: {text}; }}
QMenu::item:disabled {{ color: {disabled_text}; }}
QMenu::separator {{ height: {hair}; background: {border}; }}
QStatusBar {{ background: {ground}; color: {muted}; }}
QToolTip {{ background: {surface}; color: {text}; border: {hair} solid {border}; padding: {tip_pad}; }}

/* Cards: only widgets carrying role="card", and group boxes. Never a bare QFrame/QWidget border. */
QWidget[role="card"] {{ background: {surface}; border: {hair} solid {border}; border-bottom-color: {shadow}; border-radius: {card_radius}; }}
QGroupBox {{ background: {surface}; border: {hair} solid {border}; border-bottom-color: {shadow}; border-radius: {card_radius}; margin-top: {group_title_band}; }}
QGroupBox[role="group-plain"] {{ margin-top: 0; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: {card_radius}; padding: 0 {tab_gap}; }}

/* Tabs: segmented pills in a tray */
QTabWidget::pane {{ background: {surface}; border: {hair} solid {border}; border-radius: {card_radius}; }}
QTabBar {{ background: {tab_tray}; border-radius: {tray_radius}; }}
QTabBar::tab {{ background: {tab_tray}; color: {muted}; border: none; border-bottom: {hair} solid {tab_tray}; border-radius: {control_radius}; padding: {tab_pad_y} {tab_pad_x}; margin: {tab_gap}; }}
QTabBar::tab:hover:!selected {{ color: {text}; }}
QTabBar::tab:selected {{ background: {tab_selected}; color: {text}; border-bottom-color: {shadow}; }}
QTabBar::tab:disabled {{ color: {disabled_text}; }}

/* Inputs: flat, 1 px border with a darker bottom edge, accent border on focus */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {input_bg}; color: {text}; border: {hair} solid {input_border}; border-bottom-color: {input_border_bottom}; border-radius: {control_radius}; padding: {input_pad_y} {input_pad_x}; selection-background-color: {accent}; selection-color: {accent_text}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ background: {ground}; color: {disabled_text}; border-bottom-color: {input_border}; }}
QComboBox QAbstractItemView {{ background: {input_bg}; color: {text}; border: {hair} solid {border}; selection-background-color: {accent_soft}; selection-color: {text}; }}

/* Item views */
QListWidget, QTableWidget, QTreeWidget {{ background: {input_bg}; alternate-background-color: {surface}; color: {text}; border: {hair} solid {input_border}; border-radius: {control_radius}; gridline-color: {border}; selection-background-color: {accent_soft}; selection-color: {text}; }}
QHeaderView::section {{ background: {header}; color: {muted}; border: none; border-right: {hair} solid {border}; border-bottom: {hair} solid {border}; padding: {input_pad_y} {input_pad_x}; }}

/* Scroll bars: thin, no arrow buttons */
QScrollBar:vertical {{ background: transparent; width: {scrollbar}; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: {scrollbar}; margin: 0; }}
QScrollBar::handle:vertical {{ background: {button_border}; min-height: {scrollbar_handle_min}; margin: {scrollbar_inset}; border-radius: {scrollbar_handle_radius}; }}
QScrollBar::handle:horizontal {{ background: {button_border}; min-width: {scrollbar_handle_min}; margin: {scrollbar_inset}; border-radius: {scrollbar_handle_radius}; }}
QScrollBar::handle:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: transparent; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Progress bar */
QProgressBar {{ background: {input_bg}; color: {text}; border: {hair} solid {input_border}; border-radius: {control_radius}; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: {control_radius}; }}

/* Slider: thin groove, accent fill to the handle, round handle with an accent ring */
QSlider::groove:horizontal {{ background: {button_border}; height: {slider_groove}; border-radius: {slider_groove_radius}; }}
QSlider::groove:vertical {{ background: {button_border}; width: {slider_groove}; border-radius: {slider_groove_radius}; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: {slider_groove_radius}; }}
QSlider::add-page:vertical {{ background: {accent}; border-radius: {slider_groove_radius}; }}
QSlider::handle:horizontal {{ background: {thumb}; border: {slider_ring} solid {accent}; width: {slider_handle}; height: {slider_handle}; margin: -{slider_overhang} 0; border-radius: {slider_handle_radius}; }}
QSlider::handle:vertical {{ background: {thumb}; border: {slider_ring} solid {accent}; width: {slider_handle}; height: {slider_handle}; margin: 0 -{slider_overhang}; border-radius: {slider_handle_radius}; }}
QSlider::handle:disabled {{ border-color: {button_border}; }}
QSlider::sub-page:horizontal:disabled, QSlider::add-page:vertical:disabled {{ background: {muted}; }}

/* Buttons: secondary by default; primary (Live, Start Acquisition) filled with the accent */
QPushButton {{ background: {button_bg}; color: {text}; border: {hair} solid {button_border}; border-bottom-color: {shadow}; border-radius: {control_radius}; padding: {button_pad_y} {button_pad_x}; }}
QPushButton:hover {{ background: {header}; }}
QPushButton:pressed {{ background: {ground}; }}
QPushButton:checked {{ background: {accent_soft}; border-color: {accent}; }}
QPushButton:disabled {{ background: {ground}; color: {disabled_text}; border-color: {border}; }}
QPushButton[role="primary"] {{ background: {accent}; color: {accent_text}; border-color: {accent}; }}
QPushButton[role="primary"]:disabled {{ background: {accent_soft}; color: {disabled_text}; border-color: {accent_soft}; }}
QPushButton[role="primary"]:hover {{ background: {primary_hover}; border-color: {primary_hover}; }}
QPushButton[role="primary"]:pressed {{ background: {primary_pressed}; border-color: {primary_pressed}; }}
QPushButton[role="action"][on="true"] {{ background: {accent_soft}; border-color: {accent}; }}
QPushButton[role="action"][on="true"]:disabled {{ background: {ground}; color: {disabled_text}; border-color: {border}; }}
QPushButton[role="action-bold"] {{ font-weight: bold; }}

/* Label roles: muted notes and hints, danger and success text, banners and callouts */
QLabel[role="note"], QLabel[role="note-gray"], QLabel[role="status-idle"] {{ color: {muted}; }}
QLabel[role="hint"], QLabel[role="hint-gray"] {{ color: {muted}; font-style: italic; }}
QLabel[role="danger"], QLabel[role="danger-soft"], QLabel[role="danger-text-bold"], QLabel[role="alert-bold"], QLabel[role="alert-bold-first"] {{ color: {danger_text}; font-weight: bold; }}
QLabel[role="danger-text"], QLabel[role="alert"], QLabel[role="status-bad"] {{ color: {danger_text}; }}
QGroupBox[role="alert-group"] {{ color: {danger_text}; }}
QLabel[role="success"], QLabel[role="status-ok"] {{ color: {success_text}; }}
QLabel[role="banner-danger"], QLabel[role="banner-engine"] {{ background: {banner_bg}; color: #ffffff; border-radius: {control_radius}; padding: {tip_pad}; }}
QLabel[role="banner-danger"] {{ font-weight: bold; }}
QLabel[role="callout-warning"] {{ background: {warning_bg}; color: {warning_text}; border: {hair} solid {warning_border}; border-radius: {control_radius}; padding: {tip_pad}; }}

/* Grouped settings (XY/Z/Time frames and the scan-settings band) */
QFrame[role="group"][active="true"] {{ background: {accent_soft}; border: {hair} solid {accent}; border-radius: {control_radius}; }}
QFrame[role="group"][active="false"] {{ border: {hair} solid {border}; border-radius: {control_radius}; }}
QFrame[role="group-body"][active="true"] {{ background: {band}; border: {hair} solid {band_border}; border-radius: {control_radius}; }}

/* Drag-and-drop target while a file is dragged over it */
*[dropTarget="true"] {{ border: {hair} dashed {accent}; border-radius: {control_radius}; }}

/* Docks and splitters */
QDockWidget {{ color: {text}; }}
QDockWidget::title {{ background: {header}; color: {muted}; border: {hair} solid {border}; padding: {tip_pad}; }}
QSplitter::handle {{ background: {ground}; }}
/* pyqtgraph DockArea/Dock/DockLabel set widget-level stylesheets; the ThemeManager restyles them there. */
"""

# Only emitted with an icon directory: once an indicator or arrow is styled, Qt stops drawing its default.
_ICON_QSS = """
/* Check boxes and radio buttons */
QCheckBox::indicator, QRadioButton::indicator {{ width: {checkbox_size}; height: {checkbox_size}; background: {input_bg}; border: {hair} solid {input_border_bottom}; }}
QCheckBox::indicator {{ border-radius: {control_radius}; }}
QRadioButton::indicator {{ border-radius: {radio_radius}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url("{icon_dir}/check.svg"); }}
QRadioButton::indicator:checked {{ background: {accent}; border-color: {accent}; image: url("{icon_dir}/radio.svg"); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {ground}; border-color: {input_border}; }}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{ background: {muted}; border-color: {muted}; }}

/* Combo box and spin box arrows */
QComboBox::drop-down {{ border: none; width: {button_width}; }}
QComboBox::down-arrow {{ image: url("{icon_dir}/arrow_down.svg"); width: {checkbox_size}; height: {checkbox_size}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{ border: none; background: transparent; width: {button_width}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{icon_dir}/arrow_up.svg"); width: {spin_arrow}; height: {spin_arrow}; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{icon_dir}/arrow_down.svg"); width: {spin_arrow}; height: {spin_arrow}; }}
"""


def scale_factor(app: QApplication) -> float:
    """Logical-DPI scale for stylesheet lengths when Qt is not already scaling (device pixel ratio 1)."""
    screen = app.primaryScreen()
    if screen is None or app.devicePixelRatio() != 1:
        return 1.0
    return screen.logicalDotsPerInchX() / 96


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 contrast ratio between two ``#rrggbb`` colours."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def build_stylesheet(tokens: Tokens, scale: float, icon_dir: Optional[Path]) -> str:
    """The application stylesheet for ``tokens``, lengths multiplied by ``scale``.

    With ``icon_dir`` (from ``write_theme_icons``) indicators and arrows use the recoloured SVGs; without it the
    stylesheet has no ``url(`` and Qt draws its default indicators and arrows.
    """

    def px(value: float) -> int:
        return max(1, round(value * scale))

    values = {}
    for f in fields(Tokens):
        value = getattr(tokens, f.name)
        values[f.name] = f"{px(value)}px" if isinstance(value, int) else value
    for name, length in _LENGTHS.items():
        values[name] = f"{px(length)}px"

    # Derived lengths come from the scaled pixels so the pieces stay concentric after rounding.
    groove = px(_LENGTHS["slider_groove"])
    handle_outer = px(_LENGTHS["slider_handle"]) + 2 * px(_LENGTHS["slider_ring"])
    values["slider_groove_radius"] = f"{groove // 2}px"
    values["slider_handle_radius"] = f"{handle_outer // 2}px"
    values["slider_overhang"] = f"{(handle_outer - groove) // 2}px"
    values["scrollbar_handle_radius"] = f"{(px(_LENGTHS['scrollbar']) - 2 * px(_LENGTHS['scrollbar_inset'])) // 2}px"
    values["radio_radius"] = f"{(px(tokens.checkbox_size) + 2) // 2}px"
    values["spin_arrow"] = f"{px(tokens.checkbox_size) * 3 // 4}px"
    values["disabled_text"] = _mix(tokens.text, tokens.ground, 0.4)
    values["group_title_band"] = _GROUP_TITLE_BAND
    # Borders are one device pixel at any scale; only the other lengths grow.
    values["hair"] = "1px"
    values["primary_hover"] = _mix(tokens.accent, tokens.text, 0.85)
    values["primary_pressed"] = _mix(tokens.accent, tokens.text, 0.7)

    qss = _BASE_QSS.format(**values)
    if icon_dir is not None:
        qss += _ICON_QSS.format(icon_dir=Path(icon_dir).resolve().as_posix(), **values)
    return qss


def build_palette(tokens: Tokens) -> QPalette:
    """A QPalette carrying the token colours, so unstyled and custom-painted widgets follow the theme."""
    palette = QPalette()
    # Fusion draws bevels and separators from Dark, Mid and Midlight: keep them close to Button in both themes.
    if _relative_luminance(tokens.ground) < 0.2:
        dark, mid = _mix(tokens.button_bg, "#000000", 0.5), _mix(tokens.button_bg, "#000000", 0.75)
        midlight = _mix(tokens.button_bg, tokens.tab_selected, 0.5)
    else:
        dark, mid, midlight = tokens.input_border_bottom, tokens.button_border, tokens.surface
    roles = {
        QPalette.Window: tokens.ground,
        QPalette.WindowText: tokens.text,
        QPalette.Base: tokens.input_bg,
        QPalette.AlternateBase: tokens.surface,
        QPalette.ToolTipBase: tokens.surface,
        QPalette.ToolTipText: tokens.text,
        QPalette.PlaceholderText: tokens.muted,
        QPalette.Text: tokens.text,
        QPalette.Button: tokens.button_bg,
        QPalette.ButtonText: tokens.text,
        QPalette.BrightText: tokens.danger_text,
        QPalette.Highlight: tokens.accent,
        QPalette.HighlightedText: tokens.accent_text,
        QPalette.Link: tokens.accent,
        QPalette.Light: tokens.tab_selected,
        QPalette.Midlight: midlight,
        QPalette.Mid: mid,
        QPalette.Dark: dark,
        QPalette.Shadow: tokens.shadow,
    }
    for role, colour in roles.items():
        palette.setColor(role, QColor(colour))
    disabled = QColor(_mix(tokens.text, tokens.ground, 0.4))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, disabled)
    return palette


def write_theme_icons(tokens: Tokens, out_dir: Path) -> Optional[Path]:
    """Write the indicator SVGs recoloured for ``tokens`` into ``out_dir``; ``None`` if that is not possible."""
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, token in _ICONS.items():
            svg = (_ICON_TEMPLATE_DIR / name).read_text(encoding="utf-8")
            (out_dir / name).write_text(svg.replace("currentColor", getattr(tokens, token)), encoding="utf-8")
    except OSError:
        return None
    return out_dir


def _rgb(colour: str) -> tuple:
    value = colour.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _relative_luminance(colour: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _mix(a: str, b: str, weight_a: float) -> str:
    """``#rrggbb`` blend of two ``#rrggbb`` colours, ``weight_a`` of ``a``."""
    mixed = (round(x * weight_a + y * (1 - weight_a)) for x, y in zip(_rgb(a), _rgb(b)))
    return "#" + "".join(f"{c:02x}" for c in mixed)


# ---------------------------------------------------------------------------------------------------------------
# Applying a theme to the running application
# ---------------------------------------------------------------------------------------------------------------

_manager: "Optional[ThemeManager]" = None

# Originals of the pyqtgraph methods the dock theme replaces, saved on first patch.
_dock_originals: dict = {}


def icon_cache_dir(theme: str) -> Path:
    """Where the recoloured indicator icons for ``theme`` are written (relative to the working directory)."""
    return Path("cache") / "theme_icons" / theme


def _dock_label_update_style(tokens: Tokens):
    """A ``DockLabel.updateStyle`` rendering the quiet card header (pyqtgraph 0.14 structure, Dock.py:283-323)."""

    def updateStyle(self):
        r = "3px"
        if self.dim:
            fg = _mix(tokens.muted, tokens.header, 0.7)
        else:
            fg = tokens.muted
        bg = tokens.header
        border = tokens.border

        if self.orientation == "vertical":
            self.vStyle = """DockLabel {
                background-color : %s;
                color : %s;
                border-top-right-radius: 0px;
                border-top-left-radius: %s;
                border-bottom-right-radius: 0px;
                border-bottom-left-radius: %s;
                border-width: 0px;
                border-right: 1px solid %s;
                padding-top: 3px;
                padding-bottom: 3px;
                font-size: %s;
            }""" % (
                bg,
                fg,
                r,
                r,
                border,
                self.fontSize,
            )
            self.setStyleSheet(self.vStyle)
        else:
            self.hStyle = """DockLabel {
                background-color : %s;
                color : %s;
                border-top-right-radius: %s;
                border-top-left-radius: %s;
                border-bottom-right-radius: 0px;
                border-bottom-left-radius: 0px;
                border-width: 0px;
                border-bottom: 1px solid %s;
                padding-left: 3px;
                padding-right: 3px;
                font-size: %s;
            }""" % (
                bg,
                fg,
                r,
                r,
                border,
                self.fontSize,
            )
            self.setStyleSheet(self.hStyle)

    return updateStyle


def _dock_update_style(original, tokens: Tokens):
    """Wrap ``Dock.updateStyle`` so the widget-area border uses the theme border instead of black."""

    def updateStyle(self):
        original(self)
        self.widgetArea.setStyleSheet(self.widgetArea.styleSheet().replace("#000", tokens.border))

    return updateStyle


def _install_dock_theme(tokens: Optional[Tokens]) -> None:
    """Restyle pyqtgraph docks with ``tokens``, or restore pyqtgraph's own look with ``None``."""
    import importlib

    dock_module = importlib.import_module("pyqtgraph.dockarea.Dock")
    if not _dock_originals:
        _dock_originals["label"] = dock_module.DockLabel.updateStyle
        _dock_originals["dock"] = dock_module.Dock.updateStyle
    if tokens is None:
        dock_module.DockLabel.updateStyle = _dock_originals["label"]
        dock_module.Dock.updateStyle = _dock_originals["dock"]
    else:
        dock_module.DockLabel.updateStyle = _dock_label_update_style(tokens)
        dock_module.Dock.updateStyle = _dock_update_style(_dock_originals["dock"], tokens)
    for widget in QApplication.allWidgets():
        if isinstance(widget, (dock_module.DockLabel, dock_module.Dock)):
            widget.updateStyle()


class ThemeManager(QObject):
    """Applies Light, Dark or Classic to the application and tells listeners when it changed."""

    theme_changed = Signal(str)

    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._default_font = QFont(app.font())
        self._default_palette = QPalette(app.palette())
        self._current = "classic"
        self._skinned_applied = False

    @property
    def current(self) -> str:
        return self._current

    def tokens(self) -> Tokens:
        return {"light": LIGHT, "dark": DARK}.get(self._current, CLASSIC)

    def color(self, name: str) -> QColor:
        return QColor(getattr(self.tokens(), name))

    def row_brush(self, name: str) -> QBrush:
        return QBrush(self.color(name))

    def is_classic(self) -> bool:
        return self._current == "classic"

    def needs_restart(self, new_theme: str) -> bool:
        """True when switching to ``new_theme`` crosses between Classic and a skinned theme."""
        return (self._current == "classic") != (new_theme == "classic")

    def apply(self, theme: str) -> None:
        if theme not in THEMES:
            raise ValueError(f"unknown theme {theme!r}; expected one of {THEMES}")
        app = self._app
        if theme == "classic":
            # Only undo what this manager did: setFont/setPalette on a pristine app would override the platform
            # theme's per-class fonts and mark the palette as application-set, changing Classic's rendering.
            if self._skinned_applied:
                app.setStyleSheet("")
                app.setPalette(self._default_palette)
                app.setFont(self._default_font)
                _install_dock_theme(None)
                self._skinned_applied = False
        else:
            self._skinned_applied = True
            tokens = LIGHT if theme == "light" else DARK
            icons = write_theme_icons(tokens, icon_cache_dir(theme))
            app.setStyleSheet(build_stylesheet(tokens, scale_factor(app), icons))
            app.setPalette(build_palette(tokens))
            if sys.platform == "win32":
                app.setFont(QFont("Segoe UI", 8))
            else:
                app.setFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))
            _install_dock_theme(tokens)
        self._current = theme
        self.theme_changed.emit(theme)


def install(app: QApplication, theme: str) -> ThemeManager:
    """Create (or reuse) the application's ThemeManager and apply ``theme``."""
    global _manager
    if _manager is None:
        _manager = ThemeManager(app)
    _manager.apply(theme)
    return _manager


def manager() -> ThemeManager:
    """The ThemeManager; before ``install`` a Classic one that changes nothing."""
    global _manager
    if _manager is None:
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("theme needs a QApplication")
        _manager = ThemeManager(app)
    return _manager


# ---------------------------------------------------------------------------
# Persistence of the chosen theme ([GENERAL] ui_theme in the machine ini)
# ---------------------------------------------------------------------------


def load_theme_choice() -> str:
    """The theme named by ``[GENERAL] ui_theme``; anything unknown falls back to ``"light"``."""
    import control._def
    import squid.logging

    raw = control._def.UI_THEME
    choice = str(raw).strip().lower()
    if choice in THEMES:
        return choice
    squid.logging.get_logger("theme").warning(f"Unknown ui_theme {raw!r}; using 'light'")
    return "light"


def save_theme_choice(ini_path: Optional[str], choice: str) -> bool:
    """Write ``ui_theme = choice`` into ``[GENERAL]`` of ``ini_path``; False if it cannot be written."""
    if not ini_path:
        return False
    import io
    import os
    import tempfile
    from configparser import ConfigParser, Error as ConfigParserError

    tmp_name = None
    try:
        if not Path(ini_path).is_file():
            return False
        config = ConfigParser()
        config.read(ini_path)
        if not config.has_section("GENERAL"):
            config.add_section("GENERAL")
        config.set("GENERAL", "ui_theme", choice)
        buffer = io.StringIO()
        config.write(buffer)
        # Write beside the ini, then swap it in: the ini is either untouched or fully replaced.
        fd, tmp_name = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(ini_path)), suffix=".tmp")
        with os.fdopen(fd, "w") as file:
            file.write(buffer.getvalue())
        os.replace(tmp_name, ini_path)
        tmp_name = None
        return True
    except (OSError, ConfigParserError):
        return False
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Roles: one name per meaning, rendered as today's literal in Classic and as a stylesheet rule otherwise
# ---------------------------------------------------------------------------

_LAVENDER = "background-color: #C2C2FF"

# role (or "role:state") -> the literal stylesheet the call sites use today. Several roles may share a literal.
CLASSIC_STYLES = {
    "primary": _LAVENDER,
    "action": _LAVENDER,
    "action:on": "background-color: #C2FFC2",
    "action-bold": "background-color: #C2C2FF; font-weight: bold;",
    "note": "color: #666;",
    "note-gray": "color: gray;",
    "hint": "color: #666; font-style: italic;",
    "hint-gray": "color: gray; font-style: italic;",
    "danger": "color: #CC0000; font-weight: bold;",
    "danger-soft": "color: #e74c3c; font-weight: bold;",
    "danger-text": "color: #c0392b;",
    "danger-text-bold": "color: #c0392b; font-weight: bold;",
    "alert": "color: #b00020;",
    "alert-bold": "color: #b00020; font-weight: bold;",
    "alert-bold-first": "font-weight: bold; color: #b00020;",
    "alert-group": "QGroupBox { color: #b00020; }",
    "success": "color: #2e7d32;",
    "status-ok": "color: green;",
    "status-bad": "color: red;",
    "status-idle": "color: gray;",
    "banner-danger": "background-color: #FF6B6B; color: white; font-weight: bold; padding: 8px;",
    "banner-engine": "background-color: #c0392b; color: white; padding: 4px;",
    "callout-warning": "background-color: #fff3cd; padding: 10px; border-radius: 4px;",
    "card": "",
    "group-plain": "",
    # Format template: {cls} is the widget's class name (drag-and-drop highlight, see set_drop_target_feedback).
    "drop-target": " {cls} {{ border: 3px dashed #4a90d9; }}",
}


def classic_style(role: str, **states: bool) -> str:
    """Today's literal stylesheet for ``role``; a true state with its own ``role:state`` entry wins."""
    if role not in CLASSIC_STYLES:
        raise KeyError(role)
    for name, value in states.items():
        if value and f"{role}:{name}" in CLASSIC_STYLES:
            return CLASSIC_STYLES[f"{role}:{name}"]
    return CLASSIC_STYLES[role]


def set_role(widget: QWidget, role: str, **states: bool) -> None:
    """Style ``widget`` by meaning: the Classic literal in Classic, ``role``/state properties otherwise.

    States are independent dynamic properties, so others set elsewhere (``dropTarget``) are left alone.
    """
    literal = classic_style(role, **states)
    if manager().is_classic():
        widget.setStyleSheet(literal)
        return
    # A state given on an earlier call but not this one is switched off rather than left stale.
    for name in widget.property("_roleStates") or []:
        if name not in states:
            widget.setProperty(name, False)
    widget.setStyleSheet("")
    widget.setProperty("role", role)
    for name, value in states.items():
        widget.setProperty(name, value)
    widget.setProperty("_roleStates", list(states))
    if role == "card":
        # A stylesheet background/border paints on a QWidget subclass only with this attribute.
        widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_drop_target_feedback(widget: QWidget, active: bool) -> None:
    """Show or clear the dashed drag-and-drop highlight on ``widget``."""
    if manager().is_classic():
        if active:
            if not hasattr(widget, "_original_stylesheet"):
                widget._original_stylesheet = widget.styleSheet()
            widget.setStyleSheet(
                widget._original_stylesheet + CLASSIC_STYLES["drop-target"].format(cls=type(widget).__name__)
            )
        elif hasattr(widget, "_original_stylesheet"):
            widget.setStyleSheet(widget._original_stylesheet)
        return
    widget.setProperty("dropTarget", active)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
