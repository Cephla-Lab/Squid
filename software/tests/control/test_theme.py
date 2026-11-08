# tests/control/test_theme.py
from pathlib import Path
from qtpy.QtGui import QColor, QPalette
import control.theme as theme


def test_light_and_dark_tokens_match_the_spec():
    assert theme.LIGHT.ground == "#f3f3f3" and theme.DARK.ground == "#202020"
    assert theme.LIGHT.accent == "#0067c0" and theme.DARK.accent == "#4cc2ff"
    assert theme.LIGHT.accent_text == "#ffffff" and theme.DARK.accent_text == "#0b1a24"
    assert theme.LIGHT.input_border_bottom == "#8a8a8a" and theme.DARK.input_border_bottom == "#9a9a9a"
    assert theme.LIGHT.control_height == 26 and theme.LIGHT.control_radius == 4 and theme.LIGHT.card_radius == 8


ROW_TOKENS = ("row_disabled", "row_highlight", "row_running", "row_marked", "row_invalid", "row_conflict")


def test_classic_row_tokens_are_todays_literals():
    c = theme.CLASSIC
    assert [getattr(c, n).lower() for n in ROW_TOKENS] == [
        "#f0f0f0",
        "#f0f0ff",
        "#c8ffc8",
        "#fff4b4",
        "#ffcdcd",
        "#ffd2d2",
    ]
    assert c.row_text_muted.lower() == "#808080" and c.input_bg.lower() == "#ffffff"


def test_row_tokens_are_distinct_and_readable_against_text():
    for t in (theme.LIGHT, theme.DARK, theme.CLASSIC):
        values = [getattr(t, n).lower() for n in ROW_TOKENS]
        assert len(set(values)) == len(values), values
    for t in (theme.LIGHT, theme.DARK):
        for name in ROW_TOKENS:
            assert theme.contrast_ratio(getattr(t, name), t.text) >= 4.5, (t.ground, name)


def test_stylesheet_scales_lengths(tmp_path):
    one = theme.build_stylesheet(theme.LIGHT, 1.0, None)
    one_and_half = theme.build_stylesheet(theme.LIGHT, 1.5, None)
    assert "border-radius: 4px" in one and "border-radius: 6px" in one_and_half
    assert 'QPushButton[role="primary"]' in one and "#0067c0" in one


def test_stylesheet_styles_cards_by_role_not_every_frame():
    qss = theme.build_stylesheet(theme.LIGHT, 1.0, None)
    assert 'QWidget[role="card"]' in qss
    assert (
        "\nQFrame {" not in qss and "\nQFrame{" not in qss
    )  # QLabel inherits QFrame; a bare selector would border every label


def test_stylesheet_without_icon_dir_has_no_url_references():
    assert "url(" not in theme.build_stylesheet(theme.DARK, 1.0, None)


def test_icons_are_recoloured_from_tokens(tmp_path):
    out = theme.write_theme_icons(theme.DARK, tmp_path / "dark")
    assert out is not None
    assert theme.DARK.accent_text in (out / "check.svg").read_text()
    assert theme.DARK.muted in (out / "arrow_down.svg").read_text()
    assert "url(" in theme.build_stylesheet(theme.DARK, 1.0, out)


def test_icons_unwritable_dir_returns_none(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert theme.write_theme_icons(theme.LIGHT, blocker / "icons") is None


def test_palette_follows_tokens():
    p = theme.build_palette(theme.DARK)
    assert p.color(QPalette.Window) == QColor("#202020")
    assert p.color(QPalette.Base) == QColor("#333333")
    assert p.color(QPalette.Text) == QColor("#f0f0f0")
    assert p.color(QPalette.Highlight) == QColor("#4cc2ff")
    assert p.color(QPalette.HighlightedText) == QColor("#0b1a24")
    assert p.color(QPalette.PlaceholderText) == QColor("#a6a6a6")


def test_theme_module_uses_qtpy_only():
    src = Path(theme.__file__).read_text()
    assert "PyQt5" not in src and "PyQt6" not in src and "PySide" not in src
