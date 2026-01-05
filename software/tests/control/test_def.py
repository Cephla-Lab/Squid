"""Tests for control._def module, specifically ZMotorConfig enum and conf_attribute_reader."""

import pytest
from control._def import ZMotorConfig, conf_attribute_reader


class TestZMotorConfig:
    """Tests for ZMotorConfig enum."""

    def test_enum_values(self):
        """Test that enum has expected values."""
        assert ZMotorConfig.STEPPER.value == "STEPPER"
        assert ZMotorConfig.STEPPER_PIEZO.value == "STEPPER + PIEZO"
        assert ZMotorConfig.PIEZO.value == "PIEZO"

    def test_convert_to_enum_from_string(self):
        """Test conversion from string values."""
        assert ZMotorConfig.convert_to_enum("STEPPER") == ZMotorConfig.STEPPER
        assert ZMotorConfig.convert_to_enum("STEPPER + PIEZO") == ZMotorConfig.STEPPER_PIEZO
        assert ZMotorConfig.convert_to_enum("PIEZO") == ZMotorConfig.PIEZO

    def test_convert_to_enum_from_enum(self):
        """Test that convert_to_enum returns enum unchanged."""
        assert ZMotorConfig.convert_to_enum(ZMotorConfig.STEPPER) == ZMotorConfig.STEPPER
        assert ZMotorConfig.convert_to_enum(ZMotorConfig.PIEZO) == ZMotorConfig.PIEZO

    def test_convert_to_enum_invalid_value(self):
        """Test that invalid values raise ValueError."""
        with pytest.raises(ValueError, match="Invalid Z motor config"):
            ZMotorConfig.convert_to_enum("INVALID")
        with pytest.raises(ValueError, match="Invalid Z motor config"):
            ZMotorConfig.convert_to_enum("stepper")  # Case sensitive

    def test_has_piezo(self):
        """Test has_piezo() method."""
        assert ZMotorConfig.STEPPER.has_piezo() is False
        assert ZMotorConfig.STEPPER_PIEZO.has_piezo() is True
        assert ZMotorConfig.PIEZO.has_piezo() is True

    def test_is_piezo_only(self):
        """Test is_piezo_only() method."""
        assert ZMotorConfig.STEPPER.is_piezo_only() is False
        assert ZMotorConfig.STEPPER_PIEZO.is_piezo_only() is False
        assert ZMotorConfig.PIEZO.is_piezo_only() is True


class TestConfAttributeReader:
    """Tests for conf_attribute_reader function."""

    def test_parses_none(self):
        """Test parsing None value."""
        assert conf_attribute_reader("None") is None

    def test_parses_bool_true(self):
        """Test parsing boolean True."""
        assert conf_attribute_reader("True") is True
        assert conf_attribute_reader("true") is True

    def test_parses_bool_false(self):
        """Test parsing boolean False."""
        assert conf_attribute_reader("False") is False
        assert conf_attribute_reader("false") is False

    def test_parses_int(self):
        """Test parsing integer values."""
        assert conf_attribute_reader("42") == 42
        assert conf_attribute_reader("-10") == -10

    def test_parses_float(self):
        """Test parsing float values."""
        assert conf_attribute_reader("3.14") == 3.14
        assert conf_attribute_reader("10.0") == 10.0
        assert conf_attribute_reader("-2.5") == -2.5

    def test_parses_list(self):
        """Test parsing JSON list values."""
        assert conf_attribute_reader("[1, 2, 3]") == [1, 2, 3]
        assert conf_attribute_reader("[5.0, 10.0, 20.0]") == [5.0, 10.0, 20.0]

    def test_parses_dict(self):
        """Test parsing JSON dict values."""
        assert conf_attribute_reader('{"key": "value"}') == {"key": "value"}

    def test_returns_string_for_unparseable(self):
        """Test that unparseable values are returned as strings."""
        assert conf_attribute_reader("hello world") == "hello world"

    def test_strips_inline_comments_from_float(self):
        """Test that inline comments are stripped from float values."""
        # This is the bug that caused TypeError in ensure_plate_resolution_in_well_resolutions
        result = conf_attribute_reader("10.0  # Auto-added to DOWNSAMPLED_WELL_RESOLUTIONS_UM if not present")
        assert result == 10.0
        assert isinstance(result, float)

    def test_strips_inline_comments_from_int(self):
        """Test that inline comments are stripped from int values."""
        result = conf_attribute_reader("42  # some comment")
        assert result == 42
        assert isinstance(result, int)

    def test_strips_inline_comments_from_bool(self):
        """Test that inline comments are stripped from bool values."""
        assert conf_attribute_reader("True  # enable feature") is True
        assert conf_attribute_reader("False  # disable feature") is False

    def test_preserves_hash_in_json_list(self):
        """Test that # in JSON list values is preserved."""
        # A list should not have comments stripped
        result = conf_attribute_reader('["#FFFFFF", "#000000"]')
        assert result == ["#FFFFFF", "#000000"]

    def test_preserves_hash_in_json_dict(self):
        """Test that # in JSON dict values is preserved."""
        result = conf_attribute_reader('{"color": "#FF0000"}')
        assert result == {"color": "#FF0000"}

    def test_strips_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        assert conf_attribute_reader("  10.0  ") == 10.0
        assert conf_attribute_reader("  True  ") is True

    def test_strips_inline_comments_from_json_list(self):
        """Test that inline comments after JSON lists are stripped."""
        result = conf_attribute_reader("[1, 2, 3]  # some comment")
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_strips_inline_comments_from_json_dict(self):
        """Test that inline comments after JSON dicts are stripped."""
        result = conf_attribute_reader('{"key": "value"}  # config comment')
        assert result == {"key": "value"}
        assert isinstance(result, dict)

    def test_preserves_hash_in_json_while_stripping_trailing_comment(self):
        """Test that # inside JSON is preserved while trailing comments are stripped."""
        # This tests the fix for the edge case where JSON contains # AND has a trailing comment
        result = conf_attribute_reader('{"color": "#FF0000"}  # red color')
        assert result == {"color": "#FF0000"}
        result = conf_attribute_reader('["#FFFFFF", "#000000"]  # colors')
        assert result == ["#FFFFFF", "#000000"]

    def test_preserves_hash_in_string_without_whitespace(self):
        """Test that # in string values is preserved when not preceded by whitespace."""
        # Hash without preceding whitespace is part of the value, not a comment
        assert conf_attribute_reader("my#tag") == "my#tag"
        assert conf_attribute_reader("color#123") == "color#123"
        assert conf_attribute_reader("test#value  # comment") == "test#value"

    def test_strips_at_earliest_comment_separator(self):
        """Test that stripping occurs at the earliest comment separator."""
        # Should strip at first \t#, not at later  #
        assert conf_attribute_reader("value\t# comment1  # comment2") == "value"
        # Should strip at first  #, not at later \t#
        assert conf_attribute_reader("value # comment1\t# comment2") == "value"
