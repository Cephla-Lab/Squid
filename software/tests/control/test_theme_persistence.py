# Imports control._def at module scope; excluded from the PyQt6 venv run.
import control._def
import control.theme as theme


def test_load_theme_choice_normalises_and_falls_back(monkeypatch):
    monkeypatch.setattr(control._def, "UI_THEME", " Dark ")
    assert theme.load_theme_choice() == "dark"
    monkeypatch.setattr(control._def, "UI_THEME", "blue")
    assert theme.load_theme_choice() == "light"
    monkeypatch.setattr(control._def, "UI_THEME", "")
    assert theme.load_theme_choice() == "light"


def test_save_theme_choice_round_trips(tmp_path):
    ini = tmp_path / "configuration_test.ini"
    ini.write_text("[GENERAL]\ncamera_type = Default\n")
    assert theme.save_theme_choice(str(ini), "dark") is True
    text = ini.read_text()
    assert "ui_theme = dark" in text and "camera_type = Default" in text


def test_save_theme_choice_without_ini_returns_false(tmp_path):
    assert theme.save_theme_choice(None, "dark") is False
    assert theme.save_theme_choice(str(tmp_path / "missing.ini"), "dark") is False


def test_default_ui_theme_is_light():
    assert control._def.UI_THEME == "light"


def test_save_theme_choice_failed_replace_leaves_ini_intact(tmp_path, monkeypatch):
    import os

    ini = tmp_path / "configuration_test.ini"
    original = "[GENERAL]\ncamera_type = Default\n"
    ini.write_text(original)

    def boom(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    assert theme.save_theme_choice(str(ini), "dark") is False
    assert ini.read_text() == original
    assert [p.name for p in tmp_path.iterdir()] == ["configuration_test.ini"]


def test_save_theme_choice_adds_missing_general_section(tmp_path):
    ini = tmp_path / "configuration_test.ini"
    ini.write_text("[OTHER]\nkey = 1\n")
    assert theme.save_theme_choice(str(ini), "light") is True
    text = ini.read_text()
    assert "[GENERAL]" in text and "ui_theme = light" in text and "key = 1" in text


def test_save_theme_choice_directory_returns_false(tmp_path):
    assert theme.save_theme_choice(str(tmp_path), "dark") is False
