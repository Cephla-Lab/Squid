"""
Tests for Fluidics driver validation and Open Chamber support.

Tests the _validate_sequences() method and _validate_int_field() helper,
including support for Open Chamber application type.
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch


# We cannot import the Fluidics class directly because it depends on fluidics_v2
# hardware modules at import time. Instead, we test the validation logic by
# creating a minimal Fluidics-like object with the methods we want to test.


def _make_fluidics_stub(config=None, available_port_names=None, syringe_volume=5000):
    """Create a stub object with _validate_sequences and _validate_int_field methods.

    Avoids importing the Fluidics class (which has hardware dependencies at import time)
    by dynamically loading only the methods we need.
    """

    class FluidicsStub:
        pass

    stub = FluidicsStub()
    stub.config = config or {"application": "MERFISH"}
    stub.available_port_names = available_port_names or [f"Port {i}" for i in range(1, 11)]

    # Create a mock syringe_pump with volume attribute
    stub.syringe_pump = MagicMock()
    stub.syringe_pump.volume = syringe_volume

    # Import the validation methods from the source module dynamically
    import importlib
    import sys

    # We need to mock the fluidics_v2 imports to load the module
    fluidics_v2_mocks = [
        "fluidics",
        "fluidics.control",
        "fluidics.control.controller",
        "fluidics.control.syringe_pump",
        "fluidics.control.selector_valve",
        "fluidics.control.disc_pump",
        "fluidics.control.temperature_controller",
        "fluidics.merfish_operations",
        "fluidics.open_chamber_operations",
        "fluidics.experiment_worker",
        "fluidics._def",
    ]

    mock_modules = {}
    for mod_name in fluidics_v2_mocks:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()

    with patch.dict(sys.modules, mock_modules):
        # Force reimport to get fresh module with mocked dependencies
        mod_name = "squid.backend.drivers.fluidics.fluidics"
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
        else:
            mod = importlib.import_module(mod_name)

        Fluidics = mod.Fluidics

    # Bind the methods to our stub
    import types

    stub._validate_sequences = types.MethodType(Fluidics._validate_sequences, stub)
    stub._validate_int_field = types.MethodType(Fluidics._validate_int_field, stub)

    return stub


def _make_sequences(names, **kwargs):
    """Create a minimal sequences DataFrame for testing."""
    n = len(names)
    data = {
        "sequence_name": names,
        "fluidic_port": kwargs.get("fluidic_port", [1] * n),
        "flow_rate": kwargs.get("flow_rate", [500] * n),
        "volume": kwargs.get("volume", [200] * n),
        "incubation_time": kwargs.get("incubation_time", [0] * n),
        "repeat": kwargs.get("repeat", [1] * n),
        "fill_tubing_with": kwargs.get("fill_tubing_with", [0] * n),
        "include": kwargs.get("include", [1] * n),
    }
    df = pd.DataFrame(data)
    # Convert numeric columns to Int64 (matches what Fluidics.load_sequences does)
    for col in ["fluidic_port", "flow_rate", "volume", "incubation_time", "repeat", "fill_tubing_with", "include"]:
        df[col] = df[col].astype("Int64")
    return df


class TestValidateSequencesMERFISH:
    """Test validation with MERFISH application type (default)."""

    def test_valid_merfish_sequences(self):
        """MERFISH sequences with valid Flow names pass validation."""
        stub = _make_fluidics_stub()
        stub.sequences = _make_sequences(["Flow Probe", "Imaging", "Flow Wash"])
        stub._validate_sequences()  # Should not raise

    def test_flow_prefix_required_for_merfish(self):
        """MERFISH mode requires 'Flow ' prefix for non-standard sequence names."""
        stub = _make_fluidics_stub()
        stub.sequences = _make_sequences(["Add Reagent", "Imaging"])
        with pytest.raises(ValueError, match="Invalid sequence name"):
            stub._validate_sequences()

    def test_priming_and_cleanup_valid(self):
        """Priming and Clean Up are valid for both modes."""
        stub = _make_fluidics_stub()
        stub.sequences = _make_sequences(["Priming", "Imaging", "Clean Up"])
        stub._validate_sequences()  # Should not raise

    def test_missing_imaging_raises(self):
        """Missing Imaging sequence raises ValueError."""
        stub = _make_fluidics_stub()
        stub.sequences = _make_sequences(["Priming", "Clean Up"])
        with pytest.raises(ValueError, match="Missing required 'Imaging' sequence"):
            stub._validate_sequences()

    def test_multiple_imaging_raises(self):
        """Multiple Imaging sequences raise ValueError."""
        stub = _make_fluidics_stub()
        stub.sequences = _make_sequences(["Imaging", "Flow Probe", "Imaging"])
        with pytest.raises(ValueError, match="Multiple 'Imaging' sequences"):
            stub._validate_sequences()


class TestValidateSequencesOpenChamber:
    """Test validation with Open Chamber application type."""

    def test_valid_open_chamber_sequences(self):
        """Open Chamber sequences with valid names pass validation."""
        stub = _make_fluidics_stub(config={"application": "Open Chamber"})
        stub.sequences = _make_sequences(["Add Reagent", "Imaging", "Wash with Constant Flow"])
        stub._validate_sequences()  # Should not raise

    def test_clear_tubings_valid(self):
        """'Clear Tubings and Add Reagent' is valid for Open Chamber."""
        stub = _make_fluidics_stub(config={"application": "Open Chamber"})
        stub.sequences = _make_sequences(["Clear Tubings and Add Reagent", "Imaging"])
        stub._validate_sequences()  # Should not raise

    def test_flow_prefix_invalid_for_open_chamber(self):
        """Open Chamber mode does not allow 'Flow ' prefix sequences."""
        stub = _make_fluidics_stub(config={"application": "Open Chamber"})
        stub.sequences = _make_sequences(["Flow Probe", "Imaging"])
        with pytest.raises(ValueError, match="Invalid sequence name"):
            stub._validate_sequences()

    def test_unknown_name_invalid_for_open_chamber(self):
        """Unknown sequence names raise for Open Chamber."""
        stub = _make_fluidics_stub(config={"application": "Open Chamber"})
        stub.sequences = _make_sequences(["Unknown Action", "Imaging"])
        with pytest.raises(ValueError, match="Invalid sequence name"):
            stub._validate_sequences()


def _int_row(**kwargs):
    """Create a dict-like row with plain Python int values.

    When pandas iterates with iterrows(), Int64 values are converted to plain
    Python ints. This helper mimics that behavior for unit testing _validate_int_field.
    """
    return kwargs


class TestValidateIntField:
    """Test the _validate_int_field helper."""

    def test_valid_range(self):
        """Value within range passes."""
        stub = _make_fluidics_stub()
        row = _int_row(test_field=5)
        stub._validate_int_field(row, 0, "test_field", min_val=1, max_val=10)

    def test_below_min_raises(self):
        """Value below min raises ValueError."""
        stub = _make_fluidics_stub()
        row = _int_row(test_field=0)
        with pytest.raises(ValueError, match="Must be >= 1"):
            stub._validate_int_field(row, 0, "test_field", min_val=1)

    def test_above_max_raises(self):
        """Value above max raises ValueError."""
        stub = _make_fluidics_stub()
        row = _int_row(test_field=11)
        with pytest.raises(ValueError, match="Must be in range"):
            stub._validate_int_field(row, 0, "test_field", min_val=1, max_val=10)

    def test_allowed_values_pass(self):
        """Value in allowed list passes."""
        stub = _make_fluidics_stub()
        row = _int_row(test_field=1)
        stub._validate_int_field(row, 0, "test_field", allowed_values=[0, 1])

    def test_disallowed_value_raises(self):
        """Value not in allowed list raises ValueError."""
        stub = _make_fluidics_stub()
        row = _int_row(test_field=2)
        with pytest.raises(ValueError, match="Must be one of"):
            stub._validate_int_field(row, 0, "test_field", allowed_values=[0, 1])

    def test_min_boundary(self):
        """Value at min boundary passes."""
        stub = _make_fluidics_stub()
        row = _int_row(test_field=0)
        stub._validate_int_field(row, 0, "test_field", min_val=0)

    def test_max_boundary(self):
        """Value at max boundary passes."""
        stub = _make_fluidics_stub()
        row = _int_row(test_field=10)
        stub._validate_int_field(row, 0, "test_field", min_val=1, max_val=10)


class TestEmergencyStop:
    """Test emergency_stop null-safety."""

    def test_emergency_stop_with_none_pump(self):
        """emergency_stop should not crash if syringe_pump is None."""
        stub = _make_fluidics_stub()
        stub.syringe_pump = None
        stub.worker = None
        stub.emergency_stop_called = False

        # Import the method
        import types
        import importlib
        import sys

        fluidics_v2_mocks = [
            "fluidics",
            "fluidics.control",
            "fluidics.control.controller",
            "fluidics.control.syringe_pump",
            "fluidics.control.selector_valve",
            "fluidics.control.disc_pump",
            "fluidics.control.temperature_controller",
            "fluidics.merfish_operations",
            "fluidics.open_chamber_operations",
            "fluidics.experiment_worker",
            "fluidics._def",
        ]
        mock_modules = {}
        for mod_name in fluidics_v2_mocks:
            if mod_name not in sys.modules:
                mock_modules[mod_name] = MagicMock()

        with patch.dict(sys.modules, mock_modules):
            mod_name = "squid.backend.drivers.fluidics.fluidics"
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
            else:
                mod = importlib.import_module(mod_name)
            Fluidics = mod.Fluidics
            stub.emergency_stop = types.MethodType(Fluidics.emergency_stop, stub)

        stub.emergency_stop()  # Should not raise
        assert stub.emergency_stop_called is True

    def test_emergency_stop_with_pump_and_worker(self):
        """emergency_stop calls abort on pump and worker when present."""
        stub = _make_fluidics_stub()
        mock_pump = MagicMock()
        mock_worker = MagicMock()
        stub.syringe_pump = mock_pump
        stub.worker = mock_worker
        stub.emergency_stop_called = False

        import types
        import importlib
        import sys

        fluidics_v2_mocks = [
            "fluidics",
            "fluidics.control",
            "fluidics.control.controller",
            "fluidics.control.syringe_pump",
            "fluidics.control.selector_valve",
            "fluidics.control.disc_pump",
            "fluidics.control.temperature_controller",
            "fluidics.merfish_operations",
            "fluidics.open_chamber_operations",
            "fluidics.experiment_worker",
            "fluidics._def",
        ]
        mock_modules = {}
        for mod_name in fluidics_v2_mocks:
            if mod_name not in sys.modules:
                mock_modules[mod_name] = MagicMock()

        with patch.dict(sys.modules, mock_modules):
            mod_name = "squid.backend.drivers.fluidics.fluidics"
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
            else:
                mod = importlib.import_module(mod_name)
            Fluidics = mod.Fluidics
            stub.emergency_stop = types.MethodType(Fluidics.emergency_stop, stub)

        stub.emergency_stop()
        mock_pump.abort.assert_called_once()
        mock_worker.abort.assert_called_once()
        assert stub.emergency_stop_called is True
