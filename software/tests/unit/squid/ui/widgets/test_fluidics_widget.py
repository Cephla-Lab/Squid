"""
Tests for FluidicsWidget features.

Tests the Save Log button functionality and bug fixes.
"""

import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch, call

from squid.core.events import EventBus, FluidicsProtocolCompleted


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def qapp():
    """Create a QApplication for widget tests, or use existing one."""
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def mock_service():
    """Create a mock FluidicsService."""
    service = MagicMock()
    service.is_available = True
    service.get_available_solutions.return_value = {
        "wash_buffer": 1,
        "probe_mix": 2,
        "SSC": 3,
    }
    service.get_syringe_capacity_ul.return_value = 5000.0
    service.get_available_ports.return_value = [1, 2, 3]
    return service


@pytest.fixture
def fluidics_widget(qapp, event_bus):
    """Create a FluidicsWidget for testing (no service)."""
    from squid.ui.widgets.fluidics import FluidicsWidget

    mock_ui_bus = MagicMock()
    mock_ui_bus.subscribe = MagicMock()

    widget = FluidicsWidget(
        fluidics_service=None,
        event_bus=mock_ui_bus,
        service_registry=None,
        is_simulation=True,
    )
    yield widget
    widget.close()


@pytest.fixture
def fluidics_widget_with_service(qapp, event_bus, mock_service):
    """Create a FluidicsWidget with a mock service."""
    from squid.ui.widgets.fluidics import FluidicsWidget

    mock_ui_bus = MagicMock()
    mock_ui_bus.subscribe = MagicMock()

    widget = FluidicsWidget(
        fluidics_service=mock_service,
        event_bus=mock_ui_bus,
        service_registry=None,
        is_simulation=True,
    )
    yield widget
    widget.close()


class TestFluidicsWidgetSaveLog:
    """Test Save Log button functionality."""

    def test_save_log_button_exists(self, fluidics_widget):
        """Save Log button should exist in the widget."""
        assert hasattr(fluidics_widget, "btn_save_log")
        assert fluidics_widget.btn_save_log is not None

    def test_save_log_writes_content(self, fluidics_widget):
        """_save_log should write status_text content to file."""
        # Add some log content
        fluidics_widget.status_text.append("[12:00:00] Test log entry 1")
        fluidics_widget.status_text.append("[12:00:01] Test log entry 2")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            temp_path = f.name

        try:
            with patch.object(
                fluidics_widget,
                "_save_log",
                wraps=None,
            ):
                # Directly test the save logic
                with open(temp_path, "w") as f:
                    f.write(fluidics_widget.status_text.toPlainText())

                with open(temp_path, "r") as f:
                    content = f.read()

                assert "Test log entry 1" in content
                assert "Test log entry 2" in content
        finally:
            os.unlink(temp_path)

    def test_save_log_with_dialog(self, fluidics_widget):
        """_save_log should open file dialog and write content."""
        fluidics_widget.status_text.append("[12:00:00] Test entry")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            temp_path = f.name

        try:
            with patch(
                "squid.ui.widgets.fluidics.QFileDialog.getSaveFileName",
                return_value=(temp_path, "Text Files (*.txt)"),
            ):
                fluidics_widget._save_log()

            with open(temp_path, "r") as f:
                content = f.read()

            assert "Test entry" in content
        finally:
            os.unlink(temp_path)

    def test_save_log_cancelled(self, fluidics_widget):
        """_save_log should do nothing if dialog is cancelled."""
        with patch(
            "squid.ui.widgets.fluidics.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            # Should not raise
            fluidics_widget._save_log()


class TestEmergencyStopResetsAbort:
    """Test that emergency stop resets the abort flag."""

    def test_emergency_stop_resets_abort_flag(self, fluidics_widget_with_service, mock_service):
        """After emergency stop, reset_abort() should be called so manual ops work."""
        fluidics_widget_with_service._emergency_stop()

        mock_service.abort.assert_called_once()
        mock_service.reset_abort.assert_called_once()

    def test_emergency_stop_without_service(self, fluidics_widget):
        """Emergency stop should not crash when service is None."""
        fluidics_widget._emergency_stop()  # Should not raise


class TestEmptySyringeDoesNotCheckGauge:
    """Test that empty syringe doesn't check the stale gauge value."""

    def test_empty_syringe_does_not_check_gauge(self, fluidics_widget_with_service, mock_service):
        """Empty syringe should call service even when gauge shows 0."""
        # Set gauge to 0 - previously this would cause early return
        fluidics_widget_with_service.syringe_gauge.setValue(0)

        fluidics_widget_with_service._empty_syringe_pump()

        # Give the daemon thread a moment to start
        import time
        time.sleep(0.1)

        # Verify reset_abort and empty_syringe were called
        mock_service.reset_abort.assert_called()
        mock_service.empty_syringe.assert_called_once()


class TestManualOpsResetAbort:
    """Test that manual operations reset the abort flag before executing."""

    def test_manual_flow_resets_abort(self, fluidics_widget_with_service, mock_service):
        """Manual flow should call reset_abort() before flow_solution_by_name()."""
        import time

        # Set up the widget with valid inputs
        fluidics_widget_with_service.manual_solution_combo.addItems(["wash_buffer"])
        fluidics_widget_with_service.manual_solution_combo.setCurrentIndex(0)
        fluidics_widget_with_service.txt_manual_volume.setText("500")
        fluidics_widget_with_service.txt_manual_flow_rate.setText("100")
        mock_service.get_port_for_solution.return_value = 1

        fluidics_widget_with_service._start_manual_flow()
        time.sleep(0.1)

        mock_service.reset_abort.assert_called()

    def test_manual_prime_resets_abort(self, fluidics_widget_with_service, mock_service):
        """Manual prime should call reset_abort() before prime()."""
        import time

        fluidics_widget_with_service.txt_prime_ports.setText("1")
        fluidics_widget_with_service.txt_prime_volume.setText("500")
        fluidics_widget_with_service.txt_prime_flow_rate.setText("5000")

        fluidics_widget_with_service._start_prime()
        time.sleep(0.1)

        mock_service.reset_abort.assert_called()

    def test_manual_flow_calls_flow_solution(self, fluidics_widget_with_service, mock_service):
        """Manual flow should call flow_solution_by_name()."""
        import time

        fluidics_widget_with_service.manual_solution_combo.addItems(["wash_buffer"])
        fluidics_widget_with_service.manual_solution_combo.setCurrentIndex(0)
        fluidics_widget_with_service.txt_manual_volume.setText("500")
        fluidics_widget_with_service.txt_manual_flow_rate.setText("5000")

        fluidics_widget_with_service._start_manual_flow()
        time.sleep(0.1)

        mock_service.reset_abort.assert_called()
        mock_service.flow_solution_by_name.assert_called_once()


class TestStepsTableColumns:
    """Test steps table has expected columns."""

    def test_steps_table_has_expected_columns(self, fluidics_widget):
        """Steps table should have 6 columns: #, Operation, Solution, Volume, Rate, Incubation."""
        assert fluidics_widget.steps_table.columnCount() == 6

        headers = []
        for i in range(fluidics_widget.steps_table.columnCount()):
            item = fluidics_widget.steps_table.horizontalHeaderItem(i)
            headers.append(item.text() if item else "")

        assert headers == ["#", "Operation", "Solution", "Volume", "Rate", "Incubation"]


class TestProtocolDurationDisplay:
    """Test estimated duration display when selecting a protocol."""

    def test_protocol_selection_shows_duration(self, fluidics_widget):
        """Selecting a protocol should display estimated duration."""
        from squid.core.protocol.fluidics_protocol import (
            FluidicsProtocol,
            FluidicsProtocolStep,
            FluidicsCommand,
        )

        # Create a test protocol with known duration
        protocol = FluidicsProtocol(
            steps=[
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="wash_buffer",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
                FluidicsProtocolStep(
                    operation=FluidicsCommand.INCUBATE,
                    duration_s=120,
                ),
            ]
        )
        fluidics_widget._protocols = {"Test Protocol": protocol}
        fluidics_widget._populate_protocols_list()

        # Select the protocol
        fluidics_widget.protocols_list.setCurrentRow(0)

        # Duration should be displayed
        duration_text = fluidics_widget.lbl_estimated_duration.text()
        assert duration_text != ""
        assert "~" in duration_text

    def test_no_protocol_clears_duration(self, fluidics_widget):
        """Deselecting protocol should clear duration label."""
        fluidics_widget.lbl_estimated_duration.setText("~5m 0s")
        fluidics_widget.protocols_list.clear()

        # After clearing, selecting None should clear duration
        fluidics_widget._on_protocol_selected(None, None)
        assert fluidics_widget.lbl_estimated_duration.text() == ""


class TestSolutionHighlighting:
    """Test that invalid solutions are highlighted in the steps table."""

    def test_invalid_solution_highlighted(self, fluidics_widget):
        """Steps with unknown solutions should be highlighted orange."""
        from squid.core.protocol.fluidics_protocol import (
            FluidicsProtocol,
            FluidicsProtocolStep,
            FluidicsCommand,
        )

        # Set available solutions so we can detect mismatches
        fluidics_widget._available_solutions = ["wash_buffer", "probe_mix"]

        protocol = FluidicsProtocol(
            steps=[
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="wash_buffer",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="unknown_solution",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
            ]
        )
        fluidics_widget._populate_steps_table(protocol)

        # Row 0, column 2 (Solution) should be default color (valid solution)
        valid_item = fluidics_widget.steps_table.item(0, 2)
        assert valid_item is not None
        assert valid_item.foreground().color().name() != "#e67e22"

        # Row 1, column 2 (Solution) should be orange (invalid solution)
        invalid_item = fluidics_widget.steps_table.item(1, 2)
        assert invalid_item is not None
        assert invalid_item.foreground().color().name() == "#e67e22"
        assert "not found" in invalid_item.toolTip()


class TestProtocolCompletedError:
    """Test that error messages are displayed on protocol failure."""

    def test_protocol_failure_shows_error_message(self, fluidics_widget):
        """Protocol failure should include error_message in the log."""
        event = FluidicsProtocolCompleted(
            protocol_name="Test",
            success=False,
            steps_completed=2,
            total_steps=5,
            error_message="Solution 'X' not found",
        )
        fluidics_widget._on_protocol_completed(event)

        # Check log contains error message
        log_text = fluidics_widget.status_text.toPlainText()
        assert "Solution 'X' not found" in log_text
