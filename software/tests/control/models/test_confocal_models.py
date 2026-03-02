"""
Unit tests for confocal model registry.
"""

from control.models.confocal_models import CONFOCAL_MODELS, ConfocalModelDef


class TestConfocalModelRegistry:
    """Tests for CONFOCAL_MODELS registry."""

    def test_xlight_v3_has_both_iris(self):
        """XLight V3 should have illumination_iris and emission_iris."""
        model_def = CONFOCAL_MODELS["xlight_v3"]
        assert "illumination_iris" in model_def.objective_properties
        assert "emission_iris" in model_def.objective_properties
        assert model_def.objective_properties["illumination_iris"] == 100.0
        assert model_def.objective_properties["emission_iris"] == 100.0

    def test_xlight_v2_has_no_iris(self):
        """XLight V2 should have no objective properties."""
        model_def = CONFOCAL_MODELS["xlight_v2"]
        assert model_def.objective_properties == {}

    def test_cicero_has_no_iris(self):
        """Cicero should have no objective properties."""
        model_def = CONFOCAL_MODELS["cicero"]
        assert model_def.objective_properties == {}

    def test_unknown_model_raises_key_error(self):
        """Unknown model name should raise KeyError."""
        import pytest

        with pytest.raises(KeyError):
            CONFOCAL_MODELS["nonexistent_model"]
