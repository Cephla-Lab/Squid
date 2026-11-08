# tests/control/test_theme_roles.py
import pytest
from qtpy.QtCore import Qt
from qtpy.QtGui import QPalette
from qtpy.QtWidgets import QLabel, QPushButton, QWidget

import control.theme as theme


def test_classic_literals_are_pinned():
    s = theme.CLASSIC_STYLES
    assert s["primary"] == "background-color: #C2C2FF"
    assert s["action"] == "background-color: #C2C2FF"
    assert s["action:on"] == "background-color: #C2FFC2"
    assert s["action-bold"] == "background-color: #C2C2FF; font-weight: bold;"
    assert s["note"] == "color: #666;"
    assert s["note-gray"] == "color: gray;"
    assert s["hint"] == "color: #666; font-style: italic;"
    assert s["hint-gray"] == "color: gray; font-style: italic;"
    assert s["danger"] == "color: #CC0000; font-weight: bold;"
    assert s["danger-soft"] == "color: #e74c3c; font-weight: bold;"
    assert s["danger-text"] == "color: #c0392b;"
    assert s["danger-text-bold"] == "color: #c0392b; font-weight: bold;"
    assert s["alert"] == "color: #b00020;"
    assert s["alert-bold"] == "color: #b00020; font-weight: bold;"
    assert s["alert-bold-first"] == "font-weight: bold; color: #b00020;"
    assert s["alert-group"] == "QGroupBox { color: #b00020; }"
    assert s["success"] == "color: #2e7d32;"
    assert s["status-ok"] == "color: green;"
    assert s["status-bad"] == "color: red;"
    assert s["status-idle"] == "color: gray;"
    assert s["banner-danger"] == "background-color: #FF6B6B; color: white; font-weight: bold; padding: 8px;"
    assert s["banner-engine"] == "background-color: #c0392b; color: white; padding: 4px;"
    assert s["callout-warning"] == "background-color: #fff3cd; padding: 10px; border-radius: 4px;"
    assert s["card"] == ""
    assert s["group-plain"] == ""
    assert s["drop-target"] == " {cls} {{ border: 3px dashed #4a90d9; }}"


def test_set_role_in_classic_applies_the_literal(qapp):
    theme.install(qapp, "classic")
    b = QPushButton()
    theme.set_role(b, "action", on=True)
    assert b.styleSheet() == "background-color: #C2FFC2"
    theme.set_role(b, "action")
    assert b.styleSheet() == "background-color: #C2C2FF"
    theme.set_role(b, "primary")
    assert b.styleSheet() == "background-color: #C2C2FF"


def test_set_role_in_light_sets_properties_not_literals(qapp):
    m = theme.install(qapp, "light")
    try:
        b = QPushButton()
        theme.set_role(b, "action", on=True)
        assert b.styleSheet() == "" and b.property("role") == "action" and b.property("on") is True
        label = QLabel()
        theme.set_role(label, "hint")
        assert label.property("role") == "hint"
    finally:
        m.apply("classic")


def test_set_role_follows_a_switch_made_before_the_widget_existed(qapp):
    m = theme.install(qapp, "light")
    try:
        m.apply("dark")
        b = QPushButton()
        theme.set_role(b, "primary")
        assert b.property("role") == "primary" and b.styleSheet() == ""
        m.apply("classic")
        theme.set_role(b, "primary")
        assert b.styleSheet() == "background-color: #C2C2FF"
    finally:
        m.apply("classic")


def test_unknown_role_raises():
    with pytest.raises(KeyError):
        theme.classic_style("sparkly")


def test_card_sets_styled_background_only_in_skinned_themes(qapp):
    m = theme.install(qapp, "classic")
    try:
        w = QWidget()
        theme.set_role(w, "card")
        assert w.styleSheet() == "" and not w.testAttribute(Qt.WA_StyledBackground)
        m.apply("light")
        theme.set_role(w, "card")
        assert w.property("role") == "card" and w.testAttribute(Qt.WA_StyledBackground)
    finally:
        m.apply("classic")


def test_drop_target_property_coexists_with_role(qapp):
    m = theme.install(qapp, "light")
    try:
        b = QPushButton()
        theme.set_role(b, "action")
        b.setProperty("dropTarget", True)
        theme.set_role(b, "action", on=True)
        assert b.property("dropTarget") is True and b.property("role") == "action"
    finally:
        m.apply("classic")


def test_borders_are_one_device_pixel_at_any_scale():
    qss = theme.build_stylesheet(theme.LIGHT, 1.5, None)
    assert "border: 1px" in qss and "border: 2px" not in qss


def test_group_plain_has_no_title_band():
    qss = theme.build_stylesheet(theme.LIGHT, 1.0, None)
    assert 'QGroupBox[role="group-plain"] { margin-top: 0; }' in qss


def test_dark_palette_bevels_are_darker_than_button():
    p = theme.build_palette(theme.DARK)
    button = p.color(QPalette.Button).lightness()
    assert p.color(QPalette.Dark).lightness() < button
    assert p.color(QPalette.Mid).lightness() < button
    assert button < p.color(QPalette.Midlight).lightness() <= p.color(QPalette.Light).lightness()


def test_button_state_rules_exist():
    qss = theme.build_stylesheet(theme.LIGHT, 1.0, None)
    for rule in (
        'QPushButton[role="primary"]:hover',
        'QPushButton[role="primary"]:pressed',
        'QPushButton[role="action"][on="true"]:disabled',
    ):
        assert rule in qss


def test_every_classic_role_has_a_light_rule():
    qss = theme.build_stylesheet(theme.LIGHT, 1.0, None)
    for key in theme.CLASSIC_STYLES:
        if key == "drop-target":  # a format template for *[dropTarget="true"], not a role
            continue
        name, _, state = key.partition(":")
        selector = f'[role="{name}"]' + (f'[{state}="true"]' if state else "")
        assert selector in qss, key


def test_drop_target_feedback_classic_restores_original_sheet(qapp):
    theme.install(qapp, "classic")
    w = QWidget()
    w.setStyleSheet("color: red;")
    theme.set_drop_target_feedback(w, True)
    assert w.styleSheet() == "color: red; QWidget { border: 3px dashed #4a90d9; }"
    theme.set_drop_target_feedback(w, False)
    assert w.styleSheet() == "color: red;"


def test_drop_target_feedback_light_sets_property(qapp):
    m = theme.install(qapp, "light")
    try:
        w = QWidget()
        theme.set_drop_target_feedback(w, True)
        assert w.property("dropTarget") is True and w.styleSheet() == ""
        theme.set_drop_target_feedback(w, False)
        assert w.property("dropTarget") is False
    finally:
        m.apply("classic")
